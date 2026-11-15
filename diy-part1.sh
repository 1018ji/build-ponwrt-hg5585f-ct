#!/bin/bash
set -euo pipefail

build_root=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
source_dir=${1:-$PWD}
source_dir=$(CDPATH='' cd -- "$source_dir" && pwd -P)
sh "$build_root/scripts/apply-source-patches.sh" "$source_dir"
