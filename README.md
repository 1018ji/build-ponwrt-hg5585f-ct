# FiberHome HG5585F CT PonWrt 构建工程

源码使用 [1018ji/ponwrt](https://github.com/1018ji/ponwrt) 的 `master` 分支。
根目录 `.config` 仅选择 **HG5585F CT**。本工程只在独立编译副本上打补丁，不修改旁边的参考源码 `../ponwrt`。

## 文件结构

```text
.config                         # 用户选定的 CT 软件/机型配置
patches/ponwrt/series            # 源码树补丁应用顺序
patches/ponwrt/0001-*.patch      # Config-kernel.in 与 Linux 内核补丁
patches/ponwrt/0002-*.patch      # mt76 Makefile 与驱动补丁
patches/ponwrt/0003-*.patch      # HG5585F DTS 和 FIT 配置
patches/ponwrt/0004-*.patch      # 原厂 NPU 固件包
patches/ponwrt/0005-*.patch      # 只读自检/日志工具包
firmware/                       # 配套本地原厂 NPU 二进制及来源说明
scripts/apply-source-patches.sh # 检查并应用整套补丁、安装固件
scripts/check-build-config.sh   # 检查最终 .config 只选择 CT
openwrt/                        # 实际编译源码；被 Git 忽略
```

研究工具、测试、中间产物和详细证据放在旁边 `hg5585f-npu研究/`，不放进本构建仓库。

## 自动构建

Actions 的 **FiberHome HG5585F CT Builder** 流程：

1. 克隆 PonWrt 到独立 `openwrt/`。
2. `diy-part1.sh` 在 feeds 更新前按 `series` 应用五组补丁，并核对/安装 NPU 固件。
3. 更新、安装 feeds，复制根目录 `.config`，执行 `diy-part2.sh` 检查 CT 选择。
4. `make defconfig` 后再次核对 CT 及全部功能/驱动依赖，再下载和编译，并校验最终 ITB 的默认设备树。
5. 成功后仅上传 `openwrt/bin/targets/airoha/an7581/*.itb`，Actions 下载包名为 **bin**。找不到固件会失败；不发布 Release。

补丁验证基线是 `d9dd3a49f7bb7661c01e9c57c9e2aac2a1114b3e`。源码跟随 master，未来更新冲突时应用脚本会停止，不清理或强行覆盖工作区。
整套补丁已应用时重复执行会跳过；部分应用或存在冲突会报错。
不要对外层 patch 使用 `--whitespace=fix`，否则会改变其中 Linux/mt76 补丁的上下文。

本地准备独立编译副本：

```sh
git clone https://github.com/1018ji/ponwrt -b master openwrt
bash diy-part1.sh "$PWD/openwrt"
cp .config openwrt/.config
bash diy-part2.sh "$PWD/openwrt/.config"
```

以上仅准备源码。OpenWrt 编译应在 Linux 环境进行。

## 实验功能选择

根目录 `.config` 末尾已启用以下 HG5585F CT 自定义适配选项，用于编译 WED TX + 原厂 NPU RX/重排序组合及自检工具：

```text
CONFIG_KERNEL_NET_AIROHA_NPU_HG5585F=y
CONFIG_KERNEL_NET_AIROHA_PPE_HG5585F=y
CONFIG_KERNEL_NET_AIROHA_WED=y
CONFIG_PACKAGE_MT7915_NPU_RX=y
CONFIG_PACKAGE_hg5585f-offload-tools=y
```

已把组合模式合入 CT 默认设备树 `an7581-fiberhome-hg5585f-ct-npu-wed`：正常启动即使用原厂 NPU 接收/重排序、host PPE 与 WED 发送，无需手选 overlay。
驱动和两类固件（Wi-Fi 固件与 NPU 固件）、debugfs、能力诊断入口及开机自检都已启用。WED 模块参数默认 `wed_enable=Y`。
已编译并默认开启 `npu_probe=Y` 能力诊断；运行状态还由不发固件消息的 `offload_status` 和开机检查采集。
独立寄存器占用的 diagnostics-only 模式与活动 WED 冲突，因此不用它；只读运行状态诊断已启用。

`make defconfig` 后严格检查所有功能和安装依赖仍为 `y`；编译结束还会解析每个 ITB 的实际默认 DTB，缺失 NPU/WED/PPE 或内存/reset 描述就停止上传。
其他机型的默认设备树不变。当前仍由 Linux 完成接收转发，尚未实现的 RX→PPE 硬转发不在本次启用范围。

目前接收 DMA/重排序交给 NPU，收到的包返回 Linux 转发。完整 RX→PPE 硬转发尚未完成；这些实验代码只有离线验证，不能视作实机已验证。

## 缓存

手动启动 Actions 时可选择 Cache；外部触发默认“使用缓存”。

| 模式 | 下载缓存 | 工具链/ccache 恢复 | 缓存保存 |
| --- | --- | --- | --- |
| 使用缓存（默认） | 恢复 | 恢复 | 保存 |
| 只读缓存 | 恢复 | 恢复 | 不保存、不清理 |
| 重新编译 | 恢复 | 不恢复 | 重新构建后保存 |
| 不使用缓存 | 不恢复 | 不恢复 | 不保存、不清理 |

缓存分为 `dl`、host/tools/toolchain、`.ccache` 三层，采用 HG5585F CT 专用前缀。
工具链只接受精确匹配：缓存键包含实际打补丁后的相关源码、CPU/编译器/库及内核配置，
不复用不匹配的旧工具链。ccache 按编译器内容校验，容量 3G。
这套缓存机制来自参考构建工程；GitHub 的缓存属于各自仓库，首次在本仓库运行时需自行建立缓存。

`CONFIG_DEVEL`、`CONFIG_CCACHE` 只在临时编译副本按模式启用，根 `.config` 保留原样。
工具链构建成功后立即保存；固件失败时也会尝试保存非空 ccache，不保存失败/未开始的工具链。
同一工作流串行运行，每类缓存只在本次成功保存新缓存后清理旧项；只读和禁用模式不会修改远程缓存。
缓存恢复、保存或维护失败不会阻止正常编译及 ITB 下载包上传。
固件编译设有时间上限，在 runner 六小时上限前预留约 20 分钟用于保存已有编译缓存。

## 固件输入

`firmware/npu_rv32.bin` 和 `firmware/npu_data.bin` 是从 HG5585F 原厂备份提取的配套输入，详情见 [firmware/README.md](firmware/README.md)。
它们单独放置，不编码进 patch。GitHub runner 也需要这两个文件；缺失或哈希不符会在更新 feeds 前失败。
本仓库脚本不提交或上传它们，是否随构建仓库保存由仓库维护者决定。
