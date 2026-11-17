# HG5585F Wi-Fi flowtable 排查

目标是让 Linux 负责建立规则，NPU/PPE 负责后续包的硬件转发。`rx_mode: npu` 和 `rx_ppe_configured: 1` 表示接收路径已配置；验证具体连接还需要匹配该连接的 PPE 绑定项及硬件计数。

本次查到两处问题：

- AP 的 netdev `add` 早于加入网桥，旧 hotplug 脚本等待 2 秒后的一次 reload 可能漏掉 AP。现在每个 AP 单独等待网桥就绪，验证 inet/bridge 两张表，最多尝试 30 次。
- `airoha_wed_setup_tc()` 使用 `flow_block_cb_setup_simple()`，相同 AP 的第二个 flow block 被拒绝为 `-EBUSY`。现场给 bridge 表加入 AP 后，继续给 inet 表加入同一 AP 复现 `Resource busy`。新实现采用有线 Airoha 驱动相同的 per-block 查找、引用和解绑，使两个 flowtable 及 reload 过渡期间的旧新 block 可以共存。

先前独立临时 bridge 表的实验未证明硬件转发生效；PPE 表是在打流结束后读取，不能排除短暂绑定后老化。新采样脚本每隔 5 秒保存一次绑定项，避免只看结束快照。

新增的 bridge 路径修复：`nft_dev_fill_bridge_path()` 原本把 `net_device_path_ctx` 全部置零。手机关联时，实机只读探针证实这会返回 `DEV_PATH_AIROHA_WDMA`，通用 flowtable 解析器不接受该类型，因而 PPE `replace_calls` 始终为 0。与 routed 路径一致设置 `ieee80211=true` 后，上游拿到支持的 `DEV_PATH_IEEE80211`；PPE 仍在自己的 egress 准备阶段单独获取 WDMA 参数。

该修复已单独编译为 `nf_flow_table.ko` 并临时加载，磁盘模块未替换。回归测试执行实际 bridge 路径函数和解析器，包含旧实现失败的负对照、DSA/VLAN 处理和路径错误。

### 2026-11-17 实机结果：能建表，但硬件接管后丢包

这行修改恢复的是建表入口，**当前仍不是可用的完整硬件转发版本**。首个临时模块编译脚本遗漏了重定位 `nf_flow_table.mod` 响应文件里的对象路径，导致链接旧对象；此轮结果无效。修正响应文件后，已比较模块 `.text` 并反汇编确认 `ieee80211=true` 的实际指令，然后重新测试。

- 旧路径的临时分支计数：`eval=308500`、`bridge_failed=306050`、`added=0`；PPE `replace_calls=0`。
- 正确链接的修复路径：`replace_calls=88`、`replace_success=22`，捕获到对应手机 `192.168.1.107` 与有线电脑 `192.168.1.163:5201` 的双向 BND；WDMA1 开始处理包。
- 但 iperf 上传只传出 640 KiB，随后为 0；反向测试只交给发送端约 512 KiB，也持续重传、无后续进展。不能用低 CPU 或 BND 将其视为加速成功。
- 同期出现三次 WED `0x00000002`，其位名是 `TKID_WO_PYLD`；WDMA1 处理计数最初停在 291，后续测试累计到 636。该异常是线索，尚未证明是全部丢包的根因。还需区分 Wi-Fi→LAN 的 FOE 出口与 LAN→WED 的 ACK/数据出口。
- `reject_parse` 的最后错误是 `-EEXIST`，来自同一 PPE 通过多个设备回调收到重复规则，不能把它当作前端拒绝新规则。

故障现场已保存到本机 `/private/tmp/hg5585f-flow-followup`。临时 `nf_flow_table`、`nft_flow_offload` 诊断模块和只读观察模块均已卸载，恢复固件原模块并清空故障 BND。恢复模块的 `.text` SHA256 为 `adaae29a8ec8106dee0b3ac199b4e82eb8920d1a923eec661fc079f0c4b455cd`，与编译缓存原版一致。

在 FOE/WED 出口数据格式完成验证前，不将 948 当作已验证吞吐修复，也不重复用完整双向硬件绑定做盲测。下一步需要对照原厂 `PpeSetInfoBlk2`、`wifi_tx_tuple_add`、`wifi_fbuf_init` 和 WED/WDMA 实际包头、token 信息，分别验证两个方向。

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
