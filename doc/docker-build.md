# HG5585F 本地 Docker 编译

本地构建使用一个持久 Docker volume 保存源码、工具链、下载包和 ccache。当前仓库只读挂载到 `/recipe`；宿主机输出目录 `run` 保存构建日志和验证通过的 `.itb` 固件。

工作区结构如下：

```text
Docker volume /workdir
├── code/openwrt       固定源码路径
├── code/recipe        当前 recipe 快照
├── dl                 OpenWrt 下载缓存
└── ccache             编译缓存

宿主机 run/
└── <当前仓库 commit 的前 12 位>/
    ├── steps.log
    ├── download.log
    ├── build.log
    ├── validation.log
    ├── ccache.log
    └── *.itb
```

`recipe` 包含本仓库的补丁、配置、`source.json` 和构建、校验脚本。`/workdir/code/recipe` 是每次构建同步的配方副本；OpenWrt 源码位于 `code/openwrt`，下载包和编译缓存单独保存。

## 编译

```bash
cd /Users/happyelements/Desktop/刷机/build-ponwrt-hg5585f-ct
bash scripts/build-docker.sh build
```

脚本会自动：

- 构建或复用 `hg5585f-builder:ubuntu22.04` 镜像；
- 复用 `hg5585f-ubuntu22-<架构>` volume；
- 以只读方式挂载当前仓库到容器 `/recipe`；
- 挂载仓库上一级目录下的 `run` 到容器 `/run`，也可用 `HG5585F_RUN_DIR` 覆盖；
- 在 `/workdir/code/openwrt` 保持固定源码路径；
- 首次运行 clone 源码，后续只应用新增补丁；
- 使用宿主机逻辑 CPU 执行 `make -jN`；
- 在 `run/<当前仓库 commit 的前 12 位>` 中直接保存日志和验证通过的 `.itb`；
- 同一 commit 重建时先清空该输出目录，其他 commit 的结果保留。

默认只构建 NPU profile 的 sysupgrade ITB：

```text
ponwrt-airoha-an7581-fiberhome_hg5585f-squashfs-sysupgrade.itb
```

指定并发任务数：

```bash
HG5585F_JOBS=6 bash scripts/build-docker.sh build
```

`HG5585F_PROFILE` 默认为 `fiberhome_hg5585f-ct-npu`；其他 profile 不编译、不验证、不导出。

下载默认优先使用 ImmortalWrt 和 OpenWrt CDN 镜像，失败后继续使用 OpenWrt 自带的其他镜像和原始地址。需要指定自己的镜像列表时设置 `HG5585F_DOWNLOAD_MIRROR`，多个地址用分号分隔：

```bash
HG5585F_DOWNLOAD_MIRROR='https://mirror.example/a;https://mirror.example/b' \
  bash scripts/build-docker.sh build
```

所有下载仍按包定义的 SHA256 校验。

正常构建不需要 `--reuse` 参数。`shell` 和 `check` 仍可作为模式使用：

```bash
bash scripts/build-docker.sh shell
bash scripts/build-docker.sh check
```

## 固件和日志

目录使用 `build-ponwrt-hg5585f-ct` 仓库的 commit ID，和 OpenWrt 的 pinned source commit 区分，例如：

```text
run/907d745abcde/
├── steps.log
├── build.log
├── validation.log
└── *.itb
```

构建开始时清空当前 commit 的输出目录，因此失败时会保留本次日志，固件在编译后验证通过才复制。复制先写入临时目录，成功后再移入输出目录，仅导出 `.itb`。使用未提交修改构建时，仍归入当前 HEAD 的目录，完整 commit ID 会写入 `steps.log`。

## 增量构建

源码和编译缓存位于同一个 Docker volume，源码路径固定，因此重复执行同一命令会复用：

- OpenWrt `build_dir`、`staging_dir` 和 toolchain；
- `/workdir/dl` 下载缓存；
- `/workdir/ccache` 编译缓存；
- 已生成的配置和软件包构建状态。

feeds 已存在且 HEAD 与 `source.json` 一致时直接复用，缺失或版本变化时才执行更新。

recipe 的补丁序列保持追加式更新。修改已有补丁或改变 pinned source commit 时，增量升级会停止并报告原因；这时需要明确处理旧源码和相关构建状态，脚本不会自动混用它们。

已有旧版 volume 中如果存在兼容的 `trees/<旧 recipe>`，首次切换时会通过 `code` 软链接继续使用它，避免立即重新 clone；后续构建仍使用同一份源码和缓存。

## 手动运行 image

```bash
recipe=$(pwd -P)
run_dir=$(dirname -- "$recipe")/run
mkdir -p "$run_dir"
docker run --rm --init -it \
  --name hg5585f-local-build \
  --mount "type=bind,source=$recipe,target=/recipe,readonly" \
  --mount "type=bind,source=$run_dir,target=/run" \
  --mount type=volume,source=hg5585f-ubuntu22-aarch64,target=/workdir \
  --env HG5585F_JOBS=6 \
  --env HG5585F_RECIPE_COMMIT="$(git rev-parse HEAD)" \
  hg5585f-builder:ubuntu22.04 build
```

如果 Docker 显示的架构不是 `aarch64`，把 volume 名称中的架构替换为 `docker info --format '{{.Architecture}}'` 的结果。

## 检查和清理

查看 volume：

```bash
docker volume ls | grep hg5585f-ubuntu22
docker system df -v
```

不要在增量构建前删除 volume。删除它会同时清除源码、toolchain、下载包和 ccache。

```bash
docker volume rm hg5585f-ubuntu22-aarch64
```
