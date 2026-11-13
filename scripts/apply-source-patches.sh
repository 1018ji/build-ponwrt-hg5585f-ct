#!/bin/sh
set -eu

die() {
	printf 'apply-source-patches: %s\n' "$*" >&2
	exit 1
}

[ "$#" -le 1 ] || die "usage: $0 [PonWrt build checkout]"
build_root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd -P)
patch_dir=$build_root/patches/ponwrt
firmware_dir=$build_root/firmware
source_dir=${1:-"$PWD"}
[ -d "$source_dir" ] || die "source directory missing: $source_dir"
source_dir=$(CDPATH='' cd -- "$source_dir" && pwd -P)
source_root=$(git -C "$source_dir" rev-parse --show-toplevel 2>/dev/null) || die 'source must be a Git checkout'
[ "$source_dir" = "$source_root" ] || die 'run against the root of the independent build checkout'
[ "$source_dir" != "$build_root" ] || die 'refusing to patch the build-recipe repository itself'
reference_dir=$build_root/../ponwrt
if [ -d "$reference_dir" ]; then
	reference_dir=$(CDPATH='' cd -- "$reference_dir" && pwd -P)
	[ "$source_dir" != "$reference_dir" ] || die 'reference ponwrt checkout is protected; use an independent clone'
fi
for file in config/Config-kernel.in package/kernel/mt76/Makefile target/linux/airoha/image/an7581.mk; do
	[ -f "$source_dir/$file" ] || die "not the expected PonWrt source tree: $file missing"
done

sha256_file() {
	if command -v sha256sum >/dev/null 2>&1; then
		sha256sum "$1" | awk '{print $1}'
	elif command -v shasum >/dev/null 2>&1; then
		shasum -a 256 "$1" | awk '{print $1}'
	else
		die 'sha256sum or shasum is required'
	fi
}

verify_file() {
	if [ ! -f "$1" ] || [ -L "$1" ]; then
		die "firmware input missing or is a symlink: $1"
	fi
	actual_sha=$(sha256_file "$1")
	[ "$actual_sha" = "$2" ] || die "firmware SHA-256 mismatch: $1"
}

# Check binary inputs and existing destinations before modifying source files.
verify_file "$firmware_dir/npu_rv32.bin" a8757aabc872c31d20683aeb4c05dbb9f7f590bdd6a1102c8a6902385bb5e9a6
verify_file "$firmware_dir/npu_data.bin" 6f8cd8d7cd1cdaadfa03bef63bf71c1ad21aa2a4ad950b501761d2387d52a696
firmware_dest=$source_dir/package/firmware/airoha-hg5585f-npu/files
for file in npu_rv32.bin npu_data.bin; do
	if [ -e "$firmware_dest/$file" ] || [ -L "$firmware_dest/$file" ]; then
		if [ ! -f "$firmware_dest/$file" ] || [ -L "$firmware_dest/$file" ]; then
			die "unsafe firmware destination: $file"
		fi
		cmp -s "$firmware_dir/$file" "$firmware_dest/$file" || die "existing firmware differs: $file"
	fi
done

[ -s "$patch_dir/series" ] || die 'patches/ponwrt/series is missing or empty'
set --
seen='|'
while IFS= read -r patch_name || [ -n "$patch_name" ]; do
	case "$patch_name" in ''|\#*) continue;; esac
	case "$patch_name" in *[!a-zA-Z0-9._-]*|.*) die "invalid series entry: $patch_name";; esac
	case "$patch_name" in *.patch) ;; *) die "series entry is not a patch: $patch_name";; esac
	case "$seen" in *"|$patch_name|"*) die "duplicate series entry: $patch_name";; esac
	seen=$seen$patch_name'|'
	[ -s "$patch_dir/$patch_name" ] || die "patch missing: $patch_name"
	set -- "$@" "$patch_dir/$patch_name"
done < "$patch_dir/series"
[ "$#" -gt 0 ] || die 'no patches listed'

# Apply the complete series in one operation. Do not fix whitespace inside nested patches.
if git -C "$source_dir" apply --check --whitespace=nowarn "$@" 2>/dev/null; then
	git -C "$source_dir" apply --whitespace=nowarn "$@"
	printf 'Applied %s source-tree patches at %s\n' "$#" "$(git -C "$source_dir" rev-parse HEAD)"
elif git -C "$source_dir" apply --reverse --check --whitespace=nowarn "$@" 2>/dev/null; then
	printf 'Source-tree patches already fully applied; keeping existing files\n'
else
	git -C "$source_dir" apply --check --whitespace=nowarn "$@" >&2 || :
	die 'patch conflict or partial application; source changes were not discarded or reset'
fi
mkdir -p "$firmware_dest"
for file in npu_rv32.bin npu_data.bin; do
	if [ ! -e "$firmware_dest/$file" ]; then
		cp "$firmware_dir/$file" "$firmware_dest/$file"
	fi
done
printf 'Verified and installed HG5585F NPU firmware inputs\n'
