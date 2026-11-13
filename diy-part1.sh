#!/bin/bash
set -euo pipefail

build_root=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
sh "$build_root/scripts/apply-source-patches.sh" "${1:-$PWD}"
