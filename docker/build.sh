#!/bin/bash
set -euo pipefail
umask 022
mode=${1:-build}
case "$mode" in
  shell) cd /workdir; exec bash ;;
  check)
    test "$(id -u)" -ne 0
    for command in gcc g++ make git python3 ccache rsync fdtget dumpimage; do
      command -v "$command"
    done
    testfile=$(mktemp /workdir/hg5585f-fs.XXXXXX)
    trap 'rm -f "$testfile"' EXIT
    test ! -e "${testfile/hg5585f-fs/HG5585F-FS}"
    gcc --version | head -1
    printf 'PASS: Ubuntu 22.04 builder, non-root user, case-sensitive volume (%s)\n' "$(uname -m)"
    exit 0 ;;
  build) ;;
  *) echo 'Usage: build.sh [build|shell|check]' >&2; exit 2 ;;
esac

jobs=${HG5585F_JOBS:-$(nproc)}
[[ "$jobs" =~ ^[1-9][0-9]*$ ]] || { echo 'HG5585F_JOBS must be a positive integer' >&2; exit 2; }
bundle=$(sha256sum /recipe/source.json | cut -c1-16)
build_root=/workdir/trees/$bundle
recipe=$build_root/recipe
source=$build_root/openwrt
output=/output/$bundle
mkdir -p "$recipe" "$output" /workdir/dl /workdir/ccache
# Snapshot the recipe into the Linux filesystem; host files remain read-only.
rsync -a --delete --exclude '.git' --exclude 'openwrt' --exclude 'output' /recipe/ "$recipe/"
export CCACHE_DIR=/workdir/ccache CCACHE_COMPILERCHECK=content CCACHE_MAXSIZE=3G
export GITHUB_WORKSPACE="$recipe"
python3 "$recipe/scripts/tests/test-workflow-paths.py"
if [ ! -d "$source" ]; then
  # A failed clone is kept out of the reusable source directory.
  download_source=$(mktemp -d "$build_root/source.XXXXXX")
  rmdir "$download_source"
  trap 'rm -rf "${download_source:-}"' EXIT
  python3 "$recipe/scripts/prepare.py" clone "$download_source"
  mv "$download_source" "$source"
  trap - EXIT
else
  python3 "$recipe/scripts/prepare.py" apply "$source"
fi
cd "$source"
if [ ! -e dl ]; then ln -s /workdir/dl dl; fi
python3 scripts/check-hg5585f-offload.py
if [ ! -f .config ]; then
  cp configs/hg5585f-ct.config .config
  printf '\nCONFIG_DEVEL=y\nCONFIG_CCACHE=y\nCONFIG_CCACHE_DIR="%s"\n' "$CCACHE_DIR" >> .config
fi
./scripts/feeds update -a
./scripts/feeds install luci luci-i18n-base-zh-cn luci-app-pon airoha-pon-debug \
  airoha-ponctl airoha-pond kmod-airoha-xpon kmod-airoha-paged-bosa \
  iperf3 pciutils htop zoneinfo-asia
make defconfig
python3 "$recipe/scripts/prepare.py" config .config
make download -j"$jobs" 2>&1 | tee "$output/download.log"
printf 'Building %s with make -j%s; incremental state: %s\n' "$bundle" "$jobs" "$source"
if ! make -j"$jobs" 2>&1 | tee "$output/build.log"; then
  make -j1 V=s 2>&1 | tee -a "$output/build.log"
fi
# Preserve firmware before post-build validation, as in Actions.
cp -a bin/targets/airoha/an7581/. "$output/"
cp "$recipe/source.json" "$output/source.json"
bash "$recipe/scripts/validate-build.sh" 2>&1 | tee "$output/validation.log"
ccache -s | tee "$output/ccache.log"
printf 'Firmware and logs: %s\n' "$output"
