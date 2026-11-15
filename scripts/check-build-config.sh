#!/bin/sh
set -eu
generated=0
if [ "${1:-}" = --generated ]; then generated=1; shift; fi
[ "$#" -le 1 ] || { printf 'usage: %s [--generated] [config]\n' "$0" >&2; exit 1; }
config=${1:-.config}
[ -s "$config" ] || { printf 'Build config missing: %s\n' "$config" >&2; exit 1; }
awk -v generated="$generated" '
/^CONFIG_[A-Za-z0-9_-]+=/ {
	split($0, pair, "="); key=pair[1]; setting=substr($0,length(key)+2)
	if (key in value) {print "Config error: duplicate option " key > "/dev/stderr"; errors++}
	value[key]=setting
	if (key ~ /^CONFIG_TARGET_DEVICE_/ && key !~ /^CONFIG_TARGET_DEVICE_PACKAGES_/ && setting == "y") {multi++; multi_name=key}
	if (key ~ /^CONFIG_TARGET_.*_DEVICE_/ && key !~ /^CONFIG_TARGET_DEVICE_/ && setting == "y") {single++; single_name=key}
}
function fail(message) {print "Config error: " message > "/dev/stderr"; errors++}
function required(key) {if (value[key] != "y") fail(key " must be y")}
END {
	required("CONFIG_TARGET_airoha"); required("CONFIG_TARGET_airoha_an7581")
	if (value["CONFIG_TARGET_MULTI_PROFILE"] == "y") {
		if (multi != 1 || multi_name != "CONFIG_TARGET_DEVICE_airoha_an7581_DEVICE_fiberhome_hg5585f-ct") fail("expected only HG5585F CT")
	} else if (single != 1 || single_name != "CONFIG_TARGET_airoha_an7581_DEVICE_fiberhome_hg5585f-ct") fail("expected only HG5585F CT")
	count=split("CONFIG_KERNEL_NET_AIROHA_NPU_HG5585F CONFIG_KERNEL_NET_AIROHA_PPE_HG5585F CONFIG_KERNEL_NET_AIROHA_WED CONFIG_PACKAGE_airoha-hg5585f-npu-firmware CONFIG_PACKAGE_hg5585f-offload-tools CONFIG_PACKAGE_kmod-mt7915e CONFIG_PACKAGE_kmod-mt7916-firmware CONFIG_PACKAGE_kmod-mac80211 CONFIG_PACKAGE_MAC80211_DEBUGFS CONFIG_KERNEL_DEBUG_FS", keys, " ")
	for (i=1;i<=count;i++) required(keys[i])
	required("CONFIG_PACKAGE_luci-theme-bootstrap")
	if (value["CONFIG_PACKAGE_luci-theme-argon"] == "y") fail("Argon theme must be disabled")
	if (value["CONFIG_PACKAGE_luci-theme-carbon"] == "y") fail("Carbon theme must be disabled")
		if (errors) exit 1
	print "PASS: HG5585F CT, WED TX with native MT7916 RX, host PPE and installed dependencies enabled"
	if (generated) print "PASS: required custom options survived make defconfig"
}' "$config"
