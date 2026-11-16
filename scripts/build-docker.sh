#!/bin/bash
set -euo pipefail
if [ "${1:-}" = --help ] || [ "${1:-}" = -h ]; then
  cat <<'HELP'
Usage: bash scripts/build-docker.sh [build|shell|check]

Build HG5585F firmware in Ubuntu 22.04 using a persistent Docker volume.
HG5585F_OUTPUT_DIR defaults to /Users/happyelements/Desktop/刷机/编译.
HG5585F_JOBS defaults to 4. Reuse the same command for incremental builds.
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
docker build -t "$image" "$recipe/docker"
options=(--rm --init --name hg5585f-local-build
  --mount "type=bind,source=$recipe,target=/recipe,readonly"
  --mount "type=bind,source=$output,target=/output"
  --mount "type=volume,source=$volume,target=/workdir"
  --env "HG5585F_JOBS=${HG5585F_JOBS:-4}")
if [ -t 0 ] && [ -t 1 ]; then options+=(-it); fi
docker run "${options[@]}" "$image" "$mode"
