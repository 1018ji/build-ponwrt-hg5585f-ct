# HG5585F 原厂 NPU 编译输入

此目录保存本次授权复制的两份本地原厂固件。原始研究输入在
`../hg5585f-npu研究/private/extracted/b/userfs/`（相对于构建仓库）。

| 文件 | 字节数 | SHA-256 |
| --- | ---: | --- |
| `npu_rv32.bin` | 93404 | `a8757aabc872c31d20683aeb4c05dbb9f7f590bdd6a1102c8a6902385bb5e9a6` |
| `npu_data.bin` | 2764 | `6f8cd8d7cd1cdaadfa03bef63bf71c1ad21aa2a4ad950b501761d2387d52a696` |

`apply-source-patches.sh` 在任何补丁写入前检查输入，并复制到独立编译副本的
`package/firmware/airoha-hg5585f-npu/files/`。已有文件不同会报错，不覆盖未知输入。
这些文件只适配当前实验旧 NPU ABI；基础镜像仍可选择上游 NPU 固件。
