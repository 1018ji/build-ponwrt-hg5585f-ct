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
bash scripts/build-docker.sh
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

### TXP sentinel、中断分类和故障隔离

0018 为 Airoha WED buffer 初始化完整的普通 AP sentinel `0x03ff`；同一候选随整机启动后能正常关联、收到新的 DHCPACK 并双向通信。之前热替换驱动后连不上的原因仍需单独排查，不能据此判定 TXP 初始化有误。

限制为两端 IPv4 TCP5201、同时允许双向硬件接管后，现场出现八个对应 BND，WDMA1 处理计数增长到 61,981。先出现 `TKID_WO_PYLD`，随后旧保护在计数 20,001 时关闭 WED IRQ；之后才记录 MCU 超时、PCIe completion timeout 和失败的恢复。两秒采样不能证明保护触发瞬间的具体 IRQ 原因，也不能把整条故障链都归因于保护。

0019 修复了已确认的计数问题：旧保护在 `irq_set_mask(0)` 中累加，硬中断和 tasklet 都会调用。新实现只在处理有效 WED 事件时计数，普通掩码操作和仅原生 PCIe 事件不计入；超过阈值时检查两条队列的硬件进度，避免将持续前进的数据流当成卡死。硬件错误另行累计，持续错误洪泛即使伴随数据进度也会进入隔离。

确认故障后，通过工作队列关闭 PPE 的 WED 出口，阻止新规则、失效旧规则、停止 DMA，并保留不确定的 DMA 所有权。故障处理与正常停止串行化，已标记隔离的路径仍会尝试停止硬件；诊断新增 `irq_disabled`、`irq_faulted`、`irq_service_count`、`irq_error_count` 和最后一次状态。故障隔离保持至设备重启，不能将它描述为自动恢复了 Wi-Fi。

已通过执行实际 C 函数的回归，包括扩展中断 W1C、重复掩码、仅原生事件、持续队列进度、计数回绕、错误洪泛、一次性隔离、PPE 关闭/规则清理/DMA 停止顺序，以及失败路径的所有权保留。最终 WED/PPE ARM64 对象编译成功，19 个补丁可从固定源码完整重放。实机短时吞吐结果见下节；长期稳定性仍待验证，`0x2` 的来源尚未解决。

12 组捕获的 WA free-done 报文按当前驱动格式解码，版本、数量和 token 范围均合法；它们不能直接证明 token 池损坏。上传时经 WED 返回的短 ACK 与此前持续下载的大包路径，是后续定位的重要对照。

完整固件构建和全部构建后校验已通过，bundle 为 `8913cbd09ada4217`，镜像与日志位于 `编译/8913cbd09ada4217`。已直接解包核对 NPU FIT 内核包含新的 IRQ 分类、隔离和诊断字符串。此结果仍不替代实机双向吞吐验证。

### 8913cbd09ada4217 首轮实机结果

用户提供的四流、各 20 秒日志中，上行为服务器接收平均 208 Mbit/s，下行为服务器发送平均 557 Mbit/s，两轮持续传输。下载共 352 次重传，其中首秒 280 次、第 17 秒附近 72 次。

随后单独开启同步采样再跑两轮，捕获到四条连接共八个双向 PPE BND，并同时观察到 WDMA 前进。复测有效采样窗口内，四核整体平均 CPU 为上传 10.56%、下载 7.81%，整体区间峰值分别 13.04%、13.16%，单核区间峰值最高 32.58%。这些 CPU 数值来自后一次采样复测，不是前一份吞吐日志的同期统计。

上传窗口为启动后 517.37–538.19 秒，WDMA 增加 52,870；下载窗口为 540.73–562.00 秒，WDMA 增加 1,076,705。采样边界略超过名义 20 秒，统计只包含 WDMA 进度超过 200 包/秒的区间。复测结束时 WDMA1 总计 3,281,301，未出现 IRQ fault/quarantine、MCU 消息超时或 PCIe completion timeout，NPU CPU-return 接收错误为 0。本轮 IPv4 TCP 的硬件转发已有对应流表和硬件进度证据；`0x2` 仍存在，长期稳定性、2.4GHz 和其他协议还不能据此判定通过。


### 保留为后续排查基线

以上修复保留为已通过短时实机验证的基线，剩余异常独立排查。原厂模块的错误字符串也确认 `0x2` 为 `tx free token has no packet to point!`，`0x40000` 为 `rx drv coherent!`；后者在本次下载窗口启动后 554.608916 秒出现。WED RX driver 在这里处理无线 TX-free 通知，因此优先核对回收格式、token/payload 映射和描述符所有权。该名称本身不能证明 CPU 缓存一致性或内存硬件故障。

当前两种错误共用一处日志限流，`callbacks suppressed` 表示被抑制的日志调用数量，不能换算成丢包数量，也不能由显示的一条 `0x40000` 认定它只发生一次。下一步先对照原厂 TX-free 初始化，再增加各错误位独立计数及首次异常的有界快照，核对短 ACK 与大包路径、token 范围、回收环索引和 DDONE。现阶段保留日志，暂缓 CPU 优化和关闭日志对照。

CPU 数值是重采样过程中的总占用；同一份记录在未打流窗口也有约 6%–7% 总占用，主要是内核时间。因此上传 10.56%、下载 7.81% 不能全部算作硬件转发自身的开销，也不能作为关闭日志后的性能结论。需在异常定位后用轻量采样重新测量。


### 2026-11-18 MCU 超时后的 DMA 恢复修复

此次现场在 MCU `0x5aed`（GET_MIB_INFO）超时前，WA 已对四个 MSDU ID 反复报
`drop by reaseon ... but failed`。随后 `wed_error=0 npu_error=-110`：WED 停止成功，
MT7916 WFDMA/NPU 组合停止失败，原有共享错误提示把它写成了 WED 失败。

0010 修复完整恢复时的执行顺序：先置 RESET、停止发送 worker 和 NAPI，再停止
WED 与 NPU/GDMA，最后检查 WFDMA 是否空闲。只有完整恢复、WED 和 NPU/GDMA
均停止成功时，才允许对仍忙的 WFDMA 做一次逻辑复位，之后必须重新验证 busy 位。
任何未解决的 DMA 错误仍保留 buffer/token 所有权并进入隔离。日志分别输出
`wfdma_error`、`npu_error`，复位后停止成功时输出 `wfdma_stop_recovered`。

`offload_status` 改成完全读取主机内存，故障后不会访问 reset 中的 NPU 寄存器。
`rx_queue_view: host-software` 下的 `rxN_host_head`、`rxN_host_tail` 是主机队列索引，
不等同于旧 `rxN_producer`、`rxN_consumer` 的硬件读数；两者不能直接比较。

已执行单/双 HIF 故障注入、WED/NPU 停止失败、复位后仍忙、NAPI 配对及状态读取验证，
并用现有 OpenWrt GCC/内核/backports 配置交叉编译四个修改对象。
这些验证覆盖恢复代码，尚未证明修复了 WA 首次异常；仍需刷入后压测并确认恢复成功、
终端重新关联与业务恢复。旧固件已经 terminal quarantine 时需要整机重启。
