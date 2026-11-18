#!/bin/bash
set -euo pipefail

total_start=$(date +%s)
format_duration() {
  local seconds=${1:-0}
  printf '%02d:%02d:%02d' $((seconds / 3600)) $(((seconds / 60) % 60)) $((seconds % 60))
}
log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }
report_total() {
  local rc=$?
  if [ "$rc" -eq 0 ]; then
    log "TOTAL completed in $(format_duration $(( $(date +%s) - total_start )))"
  else
    log "TOTAL failed with exit code $rc after $(format_duration $(( $(date +%s) - total_start )))"
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
if [ "${1:-}" = --help ] || [ "${1:-}" = -h ]; then
  cat <<'HELP'
Usage: bash scripts/build-docker.sh [build|shell|check]

Build HG5585F firmware in Ubuntu 22.04 using one persistent Docker volume.
HG5585F_RUN_DIR defaults to /Users/happyelements/Desktop/刷机/run.
HG5585F_JOBS defaults to the host logical CPU count and is passed to make -j.
The source tree, downloads, toolchain and ccache are reused automatically.
HELP
  exit 0
fi
mode=${1:-build}
case "$mode" in build|shell|check) ;; *) echo 'Expected build, shell or check' >&2; exit 2 ;; esac
recipe=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
run_dir=${HG5585F_RUN_DIR:-/Users/happyelements/Desktop/刷机/run}
mkdir -p "$run_dir/log" "$run_dir/target"
run_dir=$(cd -- "$run_dir" && pwd -P)
image=hg5585f-builder:ubuntu22.04
architecture=$(docker info --format '{{.Architecture}}')
volume=hg5585f-ubuntu22-$architecture
if [ -z "${HG5585F_JOBS:-}" ]; then
  case "$(uname -s)" in
    Darwin) default_jobs=$(sysctl -n hw.logicalcpu) ;;
    Linux) default_jobs=$(nproc) ;;
    *) default_jobs=4 ;;
  esac
else
  default_jobs=$HG5585F_JOBS
fi
[[ "$default_jobs" =~ ^[1-9][0-9]*$ ]] || {
  echo 'HG5585F_JOBS must be a positive integer' >&2
  exit 2
}
printf 'Docker build: make -j%s\n' "$default_jobs"
step 'Build Ubuntu 22.04 Docker image' docker build -t "$image" "$recipe/docker"
options=(--rm --init --name hg5585f-local-build
  --mount "type=bind,source=$recipe,target=/recipe,readonly"
  --mount "type=bind,source=$run_dir,target=/run"
  --mount "type=volume,source=$volume,target=/workdir"
  --env "HG5585F_JOBS=$default_jobs")
if [ -t 0 ] && [ -t 1 ]; then options+=(-it); fi
step "Run Docker compiler ($mode)" docker run "${options[@]}" "$image" "$mode"
