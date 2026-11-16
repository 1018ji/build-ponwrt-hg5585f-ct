# HG5585F 本地 Docker 编译

本地编译使用 Ubuntu 22.04 Docker 镜像。宿主机的 `/Users/happyelements/Desktop/刷机/编译` 只保存固件和日志；源码、工具链、下载包和 ccache 保存在 Docker volume 中，重复编译会复用它们。

## 宿主机一键编译

在仓库根目录执行：

```bash
cd /Users/happyelements/Desktop/刷机/build-ponwrt-hg5585f-ct
bash scripts/build-docker.sh build
```

脚本会自动：

- 构建或复用 `hg5585f-builder:ubuntu22.04` 镜像；
- 创建或复用 `hg5585f-ubuntu22-<架构>` volume；
- 挂载仓库到容器 `/recipe`；
- 挂载 `/Users/happyelements/Desktop/刷机/编译` 到容器 `/output`；
- 使用宿主机逻辑 CPU 数执行 `make -jN`；
- 编译 native、WED、NPU 三组固件。

手动指定并发任务数：

```bash
HG5585F_JOBS=6 bash scripts/build-docker.sh build
```

`HG5585F_JOBS` 就是传给 `make -j` 的任务数。你的 M5 默认读取为 10 个逻辑核心，即 `make -j10`。

## 更新补丁后的增量编译

宿主机执行下面的命令，复用 volume 中兼容的旧源码目录：

```bash
cd /Users/happyelements/Desktop/刷机/build-ponwrt-hg5585f-ct
HG5585F_JOBS=6 bash scripts/build-docker.sh --reuse build
```

如果已经在旧容器 shell 中，用只读挂载的最新脚本执行：

```bash
HG5585F_REUSE_TREE=1 HG5585F_JOBS=6 bash /recipe/docker/build.sh build
```

`--reuse` 会校验旧构建的 manifest，保持源码的绝对路径不变，只应用新追加的补丁。工具链、下载包、软件包构建状态和 ccache 会继续保留。若原有补丁被修改或基准源码版本改变，会新建独立构建目录；检测到旧源码被本地修改时会停止，保留修改。

本次新增内核补丁时，OpenWrt 可能重新准备、编译内核并重新打包固件，ccache 会加速重复编译。不能保证只重新编译单个驱动对象。每次成功准备源码后会记录 `.hg5585f-source.json`，用于下一次增量校验。

## 宿主机单独构建 image

```bash
cd /Users/happyelements/Desktop/刷机/build-ponwrt-hg5585f-ct
docker build -t hg5585f-builder:ubuntu22.04 docker
```

确认镜像：

```bash
docker image inspect hg5585f-builder:ubuntu22.04
```

## 宿主机直接运行 image

Apple Silicon 使用 `aarch64` volume：

```bash
mkdir -p /Users/happyelements/Desktop/刷机/编译
docker run --rm --init -it \
  --name hg5585f-local-build \
  --mount type=bind,source=/Users/happyelements/Desktop/刷机/build-ponwrt-hg5585f-ct,target=/recipe,readonly \
  --mount type=bind,source=/Users/happyelements/Desktop/刷机/编译,target=/output \
  --mount type=volume,source=hg5585f-ubuntu22-aarch64,target=/workdir \
  --env HG5585F_JOBS=6 \
  hg5585f-builder:ubuntu22.04 build
```

如果 Docker 显示的架构不是 `aarch64`，先查看：

```bash
docker info --format '{{.Architecture}}'
```

然后把 volume 名称中的 `aarch64` 换成实际架构。

## 进入容器

使用仓库脚本进入：

```bash
bash scripts/build-docker.sh shell
```

脚本会进入 `/workdir`，并保留源码和缓存。

手动进入已经运行的容器：

```bash
docker exec -it hg5585f-local-build bash
```

如果容器没有运行，直接启动 shell：

```bash
docker run --rm --init -it \
  --name hg5585f-local-shell \
  --mount type=bind,source=/Users/happyelements/Desktop/刷机/build-ponwrt-hg5585f-ct,target=/recipe,readonly \
  --mount type=bind,source=/Users/happyelements/Desktop/刷机/编译,target=/output \
  --mount type=volume,source=hg5585f-ubuntu22-aarch64,target=/workdir \
  --env HG5585F_JOBS=6 \
  hg5585f-builder:ubuntu22.04 shell
```

## 容器内执行命令

进入容器后，先做环境检查：

```bash
/usr/local/bin/build.sh check
```

开始或继续编译：

```bash
HG5585F_JOBS=6 /usr/local/bin/build.sh build
```

不设置 `HG5585F_JOBS` 时，容器默认使用 `nproc` 检测到的 CPU 数量。源码目录和当前构建状态位于：

```bash
ls -lah /workdir/trees
find /workdir/trees -maxdepth 3 -type d -name openwrt -print
```

查看输出：

```bash
find /output -maxdepth 2 -type f | sort
```

退出容器：

```bash
exit
```

## 输出和日志

宿主机输出目录下按 recipe 哈希分目录：

```text
/Users/happyelements/Desktop/刷机/编译/<recipe-hash>/
```

常见文件：

- `*.itb`、`*.bin`：固件；
- `sha256sums`：校验文件；
- `steps.log`：每一步的开始、结束、失败和耗时；
- `download.log`：源码下载日志；
- `build.log`：编译日志；
- `validation.log`：编译后验证日志；
- `ccache.log`：编译缓存统计。

## 缓存和清理

查看 volume：

```bash
docker volume ls | grep hg5585f-ubuntu22
```

查看 volume 占用：

```bash
docker system df -v
```

不要删除 `hg5585f-ubuntu22-aarch64`，否则源码、工具链、下载包和 ccache 都会丢失，需要重新准备。

确认清理后再删除：

```bash
docker volume rm hg5585f-ubuntu22-aarch64
```

清理不会删除宿主机 `/Users/happyelements/Desktop/刷机/编译` 中已经导出的固件和日志。
