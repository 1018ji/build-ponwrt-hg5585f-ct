#!/bin/bash
set -euo pipefail

build_root=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
source_dir=${1:-$PWD}
source_dir=$(CDPATH='' cd -- "$source_dir" && pwd -P)
sh "$build_root/scripts/apply-source-patches.sh" "$source_dir"

# Carbon is a standalone LuCI package repository. OpenWrt feed scanning skips
# a feed repository's root Makefile, so place the checkout under package/ where
# the normal package scanner can discover luci-theme-carbon.
carbon_dir=$source_dir/package/luci-theme-carbon
if [ -e "$carbon_dir" ]; then
	[ -f "$carbon_dir/Makefile" ] || {
		echo "diy-part1: unexpected Carbon package path: $carbon_dir" >&2
		exit 1
	}
else
	git clone --depth 1 --branch main \
		https://github.com/mrjoechen/luci-theme-carbon.git "$carbon_dir"
fi
