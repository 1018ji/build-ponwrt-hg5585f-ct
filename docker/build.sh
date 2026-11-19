#!/bin/bash
set -euo pipefail
umask 022

total_start=$(date +%s)
total_reported=0
log_file=

format_duration() {
  local seconds=${1:-0}
  printf '%02d:%02d:%02d' $((seconds / 3600)) $(((seconds / 60) % 60)) $((seconds % 60))
}

log() {
  local line
  line=$(printf '[%s] %s' "$(date '+%F %T')" "$*")
  printf '%s\n' "$line"
  if [ -n "$log_file" ]; then
    printf '%s\n' "$line" >> "$log_file"
  fi
}

report_total() {
  local rc=$?
  if [ "$total_reported" -eq 0 ]; then
    total_reported=1
    if [ "$rc" -eq 0 ]; then
      log "TOTAL completed in $(format_duration $(( $(date +%s) - total_start )))"
    else
      log "TOTAL failed with exit code $rc after $(format_duration $(( $(date +%s) - total_start )))"
    fi
  fi
  return "$rc"
}
trap report_total EXIT

step() {
  local name=$1
  shift
  local started=$(date +%s)
  log "START $name"
  if "$@"; then
    log "DONE  $name ($(format_duration $(( $(date +%s) - started ))))"
  else
    local rc=$?
    log "FAIL  $name (exit=$rc, $(format_duration $(( $(date +%s) - started ))))"
    return "$rc"
  fi
}

mode=${1:-build}
case "$mode" in
  shell) log 'Opening interactive build shell'; cd /workdir; exec bash ;;
  check)
    step 'Check Docker build dependencies' bash -euo pipefail -c '
      test "$(id -u)" -ne 0
      for command in gcc g++ make git python3 ccache rsync fdtget dumpimage; do command -v "$command"; done
      gcc --version | head -1
    '
    step 'Check case-sensitive Linux work volume' bash -euo pipefail -c '
      testfile=$(mktemp /workdir/hg5585f-fs.XXXXXX)
      test ! -e "${testfile/hg5585f-fs/HG5585F-FS}"
      rm -f "$testfile"
    '
    log "PASS Ubuntu 22.04 builder, non-root user, case-sensitive volume ($(uname -m))"
    exit 0 ;;
  build) ;;
  *) echo 'Usage: build.sh [build|shell|check]' >&2; exit 2 ;;
esac

jobs=${HG5585F_JOBS:-$(nproc)}
[[ "$jobs" =~ ^[1-9][0-9]*$ ]] || { echo 'HG5585F_JOBS must be a positive integer' >&2; exit 2; }
build_profile=${HG5585F_PROFILE:-fiberhome_hg5585f-ct-npu}
[[ "$build_profile" == fiberhome_hg5585f-ct-npu ]] || {
  echo 'HG5585F_PROFILE must be fiberhome_hg5585f-ct-npu' >&2
  exit 2
}
download_mirror=${HG5585F_DOWNLOAD_MIRROR:-https://mirror.immortalwrt.org/sources\;https://sources.cdn.openwrt.org}
recipe_commit=${HG5585F_RECIPE_COMMIT:-$(git -c safe.directory=/recipe -C /recipe rev-parse --verify HEAD)}
[[ "$recipe_commit" =~ ^[0-9a-f]{40}$ ]] || { echo 'Expected a full recipe Git commit ID' >&2; exit 2; }
exec 9>/workdir/.build.lock
flock -n 9 || { echo 'Another HG5585F build is already running' >&2; exit 1; }
build_root=/workdir/code
recipe=$build_root/recipe
source=$build_root/openwrt
run_root=/run
build_id=${recipe_commit:0:12}
output=$run_root/$build_id
export build_root recipe source output
rm -rf -- "$output"
mkdir -p "$output"
log_file=$output/steps.log
log "INFO  Build commit=$recipe_commit profile=$build_profile jobs=$jobs source=$source"
mkdir -p "$recipe" /workdir/dl /workdir/ccache
step 'Snapshot build recipe into persistent Linux volume' rsync -a --no-owner --no-group --delete \
  --exclude '.git' --exclude '__pycache__' --exclude '*.pyc' \
  --exclude 'openwrt' --exclude 'output' /recipe/ "$recipe/"
if [ ! -e "$build_root" ] && [ -d /workdir/trees ]; then
  if previous=$(python3 /recipe/scripts/upgrade-source.py select /workdir/trees); then
    if [ -n "$previous" ]; then
      ln -s "$previous" "$build_root"
      log "INFO  Reusing compatible source tree through $build_root"
    fi
  fi
fi
# Save the previous recipe before the snapshot replaces it.
if [ -d "$source" ] && [ ! -f "$source/.hg5585f-source.json" ]; then
  if [ ! -f "$recipe/source.json" ]; then
    log 'FAIL  Existing source has no previous recipe manifest'
    exit 1
  fi
  cp "$recipe/source.json" "$source/.hg5585f-source.json"
fi
export CCACHE_DIR=/workdir/ccache CCACHE_COMPILERCHECK=content CCACHE_MAXSIZE=3G
export GITHUB_WORKSPACE="$recipe"
step 'Check incremental patch upgrade behavior' python3 "$recipe/scripts/tests/test-incremental.py"
if [ ! -d "$source" ]; then
  step 'Clone pinned OpenWrt source' bash -euo pipefail -c '
    download_source=$(mktemp -d "$build_root/source.XXXXXX")
    rmdir "$download_source"
    trap '\''rm -rf "${download_source:-}"'\'' EXIT
    python3 "$recipe/scripts/prepare.py" clone "$download_source"
    mv "$download_source" "$source"
    trap - EXIT
  '
else
  step 'Apply only newly appended source patches' python3 "$recipe/scripts/upgrade-source.py" apply "$source" "$source/.hg5585f-source.json"
fi
# prepare.py and upgrade-source.py record the applied recipe in .hg5585f-source.json.
cd "$source"
step 'Check exported HG5585F patch bundle' python3 scripts/check-hg5585f-offload.py
if [ ! -e dl ]; then step 'Link persistent package download cache' ln -s /workdir/dl dl; fi
if [ ! -f .config ]; then
  step 'Create initial HG5585F build configuration' bash -o pipefail -c '
    cp configs/hg5585f-ct.config .config
    printf "\\nCONFIG_DEVEL=y\\nCONFIG_CCACHE=y\\nCONFIG_CCACHE_DIR=\\\"%s\\\"\\n" "$CCACHE_DIR" >> .config
  '
else
  log 'INFO  Reusing existing OpenWrt configuration'
fi
step 'Merge HG5585F profile configuration' bash -o pipefail -c './scripts/kconfig.pl + .config configs/hg5585f-ct.config > .config.merged && mv .config.merged .config'
step 'Fetch latest luci-app-airoha-npu' bash -euo pipefail -c '
  url=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))[\"vendored_sources\"][\"luci-app-airoha-npu\"][\"url\"])" "$recipe/source.json")
  rm -rf package/luci-app-airoha-npu
  git clone --depth=1 "$url" package/luci-app-airoha-npu
  rm -rf package/luci-app-airoha-npu/.git
'
step 'Update package feeds to latest' bash -o pipefail -c './scripts/feeds update -a'
step 'Install required hardware and LuCI feeds' bash -o pipefail -c './scripts/feeds install luci luci-theme-bootstrap luci-i18n-base-zh-cn luci-app-pon airoha-pon-debug airoha-ponctl airoha-pond kmod-airoha-xpon kmod-airoha-paged-bosa iperf3 pciutils htop zoneinfo-asia && sed -i "s#\$(TOPDIR)/feeds/luci/luci.mk#../../luci/luci.mk#" feeds/pon_userspace/luci-app-pon/Makefile'
step 'Resolve final OpenWrt configuration' make defconfig
step 'Verify generated OpenWrt configuration' python3 "$recipe/scripts/prepare.py" config .config
step 'Download package sources' bash -o pipefail -c "DOWNLOAD_MIRROR=\"$download_mirror\" make download -j\"$jobs\" 2>&1 | tee \"$output/download.log\""
log "INFO  Firmware build uses make -j$jobs PROFILE=$build_profile"
if ! step "Compile $build_profile firmware" bash -o pipefail -c "make -j\"$jobs\" PROFILE=\"$build_profile\" 2>&1 | tee \"$output/build.log\""; then
  step 'Retry firmware build serially with verbose output' bash -o pipefail -c "make -j1 V=s PROFILE=\"$build_profile\" 2>&1 | tee -a \"$output/build.log\""
fi
step 'Archive compiled firmware image' bash -euo pipefail -c '
  image_dir=bin/targets/airoha/an7581
  temporary=$(mktemp -d "$output/.images.XXXXXX")
  trap '\''rm -rf "$temporary"'\'' EXIT
  image="$image_dir/ponwrt-airoha-an7581-fiberhome_hg5585f-ct-npu-squashfs-sysupgrade.itb"
  test -f "$image"
  cp -a -- "$image" "$temporary/ponwrt-airoha-an7581-fiberhome_hg5585f-squashfs-sysupgrade.itb"
  mv -- "$temporary/ponwrt-airoha-an7581-fiberhome_hg5585f-squashfs-sysupgrade.itb" "$output/"
'
if step 'Run post-build validation (non-blocking)' bash -o pipefail -c "HG5585F_PROFILE=\"$build_profile\" bash \"$recipe/scripts/validate-build.sh\" 2>&1 | tee \"$output/validation.log\""; then
  :
else
  log 'WARN  Post-build validation failed; compiled firmware was already archived'
fi
step 'Print compiler cache statistics' bash -o pipefail -c "ccache -s | tee \"$output/ccache.log\""
log "OUTPUT logs and firmware: $output"
