#!/bin/bash
set -euo pipefail

build_root=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
sh "$build_root/scripts/check-build-config.sh" "${1:-$PWD/.config}"
