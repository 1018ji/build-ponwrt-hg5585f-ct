# HG5585F Wi-Fi flowtable 排查

目标是让 Linux 负责建立规则，NPU/PPE 负责后续包的硬件转发。`rx_mode: npu` 和 `rx_ppe_configured: 1` 表示接收路径已配置；验证具体连接还需要匹配该连接的 PPE 绑定项及硬件计数。

本次查到两处问题：

- AP 的 netdev `add` 早于加入网桥，旧 hotplug 脚本等待 2 秒后的一次 reload 可能漏掉 AP。现在每个 AP 单独等待网桥就绪，验证 inet/bridge 两张表，最多尝试 30 次。
- `airoha_wed_setup_tc()` 使用 `flow_block_cb_setup_simple()`，相同 AP 的第二个 flow block 被拒绝为 `-EBUSY`。现场给 bridge 表加入 AP 后，继续给 inet 表加入同一 AP 复现 `Resource busy`。新实现采用有线 Airoha 驱动相同的 per-block 查找、引用和解绑，使两个 flowtable 及 reload 过渡期间的旧新 block 可以共存。

先前独立临时 bridge 表的实验未证明硬件转发生效；PPE 表是在打流结束后读取，不能排除短暂绑定后老化。新采样脚本每隔 5 秒保存一次绑定项，避免只看结束快照。

新增的 bridge 路径修复：`nft_dev_fill_bridge_path()` 原本把 `net_device_path_ctx` 全部置零。手机关联时，实机只读探针证实这会返回 `DEV_PATH_AIROHA_WDMA`，通用 flowtable 解析器不接受该类型，因而 PPE `replace_calls` 始终为 0。与 routed 路径一致设置 `ieee80211=true` 后，上游拿到支持的 `DEV_PATH_IEEE80211`；PPE 仍在自己的 egress 准备阶段单独获取 WDMA 参数。

该修复已单独编译为 `nf_flow_table.ko` 并临时加载，磁盘模块未替换。回归测试执行实际 bridge 路径函数和解析器，包含旧实现失败的负对照、DSA/VLAN 处理和路径错误。实际 iperf/PPE 绑定效果仍需结合新一轮流量确认，不能由路径探针单独推断。

## 编译和测试

```sh
HG5585F_JOBS=6 bash scripts/build-docker.sh --reuse build
```

新增内核补丁需要更新完整内核及匹配模块。现有设备仅修改 nft 表，无法获得新内核的 WED 多 block 修复或诊断接口。

新增回归验证执行真实 C 函数，覆盖 inet/bridge 共用 AP、两个 AP、reload 新旧 block 共存、引用释放、分配失败以及各建表阶段错误原样返回。hotplug 测试覆盖 AP 延迟加入网桥和 reload 重试。

## 实机验证

固件更新后，先确认两个表都含 AP：

```sh
nft list flowtable inet fw4 ft
nft list flowtable bridge fw4 fb
cat /sys/kernel/debug/ppe/flow_setup
hg5585f-wifi-offload-check 60
```

在采样的 60 秒内做 Wi-Fi 手机与有线电脑的双向 iperf。报告保存规则设备列表、CPU/软中断、接口计数、建表诊断，以及打流期间的 PPE BND 快照。

- `replace_calls` 不变：规则没有到达 PPE 建表函数；继续查 flowtable 与绑定回调。
- `reject_parse/actions/mac` 增长：输入规则字段或动作不被驱动接受，`last_action` 是该阶段的 action/头类型。
- `reject_prepare` 增长：结合 `last_egress`、`wdma_error`、`wdma_*` 查出口解析。
- `replace_success` 增长：软件接受了规则；还需要 `hw_commit_*` 和对应流的 BND 确认硬件绑定。
- `hw_commit_errors` 增长：实际 FOE 提交失败，继续检查硬件表项。

`flow_setup` 只读取软件计数，不读取或清除硬件计数；`hw_commit_calls` 包括失效等提交，单独增长不能证明测试流已加速。

当前 FE accounting 曾出现明显异常大值，在寄存器布局核实前不把它作为吞吐或加速成功的依据。也不能把 WED TX 计数增长直接当作 PPE/WDMA 转发已验证。
