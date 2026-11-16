#!/bin/bash
set -euo pipefail
if [ "${1:-}" = --help ] || [ "${1:-}" = -h ]; then
  cat <<'HELP'
Usage: bash scripts/build-docker.sh [build|shell|check]

Build HG5585F firmware in Ubuntu 22.04 using a persistent Docker volume.
HG5585F_OUTPUT_DIR defaults to /Users/happyelements/Desktop/刷机/编译.
HG5585F_JOBS defaults to the host logical CPU count and is passed to make -j.
Reuse the same command for incremental builds.
HELP
  exit 0
fi
mode=${1:-build}
case "$mode" in build|shell|check) ;; *) echo 'Expected build, shell or check' >&2; exit 2 ;; esac
recipe=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
output=${HG5585F_OUTPUT_DIR:-/Users/happyelements/Desktop/刷机/编译}
mkdir -p "$output"
output=$(cd -- "$output" && pwd -P)
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
docker build -t "$image" "$recipe/docker"
options=(--rm --init --name hg5585f-local-build
  --mount "type=bind,source=$recipe,target=/recipe,readonly"
  --mount "type=bind,source=$output,target=/output"
  --mount "type=volume,source=$volume,target=/workdir"
  --env "HG5585F_JOBS=$default_jobs")
if [ -t 0 ] && [ -t 1 ]; then options+=(-it); fi
docker run "${options[@]}" "$image" "$mode"
