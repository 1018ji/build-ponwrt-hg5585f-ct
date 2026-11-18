#!/bin/bash
# Run after firmware upload; a failed check must still fail the job.
set -euo pipefail

export PATH="$PWD/staging_dir/host/bin:$PATH"
shopt -s nullglob
kernel_configs=()
while IFS= read -r -d '' kernel_config; do
  kernel_dir=${kernel_config%/.config}
  kernel_name=${kernel_dir##*/}
  [[ "$kernel_name" =~ ^linux-[0-9]+\.[0-9]+(\.[0-9]+)?$ ]] || continue
  kernel_configs+=("$kernel_config")
done < <(find -L build_dir/target-* -type f -path '*/linux-airoha_an7581/linux-*/.config' -print0 2>/dev/null)
[ "${#kernel_configs[@]}" -eq 1 ] || {
  echo "Expected one configured AN7581 kernel; found ${#kernel_configs[@]}"
  if [ "${#kernel_configs[@]}" -gt 0 ]; then
    printf 'Kernel candidate: %s\n' "${kernel_configs[@]}"
  else
    find -L build_dir/target-* -type f -path '*/linux-airoha_an7581/linux-*/.config' -print 2>/dev/null || true
  fi
  exit 1
}
for kernel_config in "${kernel_configs[@]}"; do
  [ -f "$kernel_config" ] || { echo "Kernel config missing: $kernel_config"; exit 1; }
  kernel=${kernel_config%/.config}
  echo "Checking built kernel source: $kernel"
  python3 "$GITHUB_WORKSPACE/scripts/prepare.py" kernel "$kernel/.config"
  python3 "$GITHUB_WORKSPACE/scripts/tests/test-wed-probe.py" --kernel "$kernel"
  python3 "$GITHUB_WORKSPACE/scripts/tests/test-wed-irq-storm.py" --kernel "$kernel"
  python3 "$GITHUB_WORKSPACE/scripts/tests/test-wed-stop.py" --kernel "$kernel"
  python3 "$GITHUB_WORKSPACE/scripts/tests/test-wed-exceptions.py" --kernel "$kernel"
  python3 "$GITHUB_WORKSPACE/scripts/tests/test-npu-lifecycle.py" --kernel "$kernel"
  python3 "$GITHUB_WORKSPACE/scripts/tests/test-ppe-flow-setup.py" --kernel "$kernel"
  python3 "$GITHUB_WORKSPACE/scripts/tests/test-wed-flow-blocks.py" --kernel "$kernel"
  python3 "$GITHUB_WORKSPACE/scripts/tests/test-bridge-wifi-path.py" --source "$kernel/net/netfilter/nf_flow_table_path.c"
  python3 "$GITHUB_WORKSPACE/scripts/tests/test-ap-flowtable.py" --hotplug target/linux/airoha/an7581/base-files/etc/hotplug.d/net/50-fw4-bridge-reload
  python3 "$GITHUB_WORKSPACE/scripts/tests/check-hg5585f-ppe-host-start.py" --source "$kernel/drivers/net/ethernet/airoha/airoha_ppe.c"
  python3 scripts/check-hg5585f-ppe-accounting.py --source "$kernel/drivers/net/ethernet/airoha/airoha_ppe_account.c"
  mt76_found=0
  for mt76 in "${kernel%/*}"/mt76-*; do
    [ -d "$mt76" ] || continue
    [ -f "$mt76/mt7915/pci.c" ] || continue
    [ -f "$mt76/npu_rx_legacy.c" ] || {
      echo "MT76 NPU RX source missing: $mt76/npu_rx_legacy.c"; exit 1;
    }
    echo "Checking built MT76 source: $mt76"
    python3 scripts/check-hg5585f-rom-abi.py --header "$kernel/include/linux/soc/airoha/airoha_wlan_legacy.h" --backend "$mt76/npu_rx_legacy.c"
    python3 scripts/check-hg5585f-wifi-diagnostics.py --mt76 "$mt76" --kernel "$kernel"
    python3 scripts/check-hg5585f-wed-pci-irq.py --mt76 "$mt76" --kernel "$kernel"
    python3 scripts/check-hg5585f-wed-rx-irq.py --mt76 "$mt76"
    python3 scripts/check-hg5585f-npu-input-ring.py --header "$kernel/include/linux/soc/airoha/airoha_wlan_legacy.h" --mt76 "$mt76" --firmware files/lib/firmware/airoha/en7581_MT7916_npu_rv32.bin
    python3 "$GITHUB_WORKSPACE/scripts/tests/check-npu-dma-device.py" --source "$mt76/mt7915/mmio.c"
    python3 "$GITHUB_WORKSPACE/scripts/tests/test-wed-txp-template.py" --mt76 "$mt76"
    python3 "$GITHUB_WORKSPACE/scripts/tests/test-mt7915-dma-recovery.py" --mt76 "$mt76"
    mt76_found=$((mt76_found + 1))
  done
  [ "$mt76_found" -gt 0 ] || {
    echo "MT76 build directory missing under: ${kernel%/*}"
    find "${kernel%/*}" -maxdepth 2 -type d -name 'mt76*' -print
    exit 1
  }
done
profiles=(native wed npu)
if [ "${HG5585F_PROFILE:-all}" = fiberhome_hg5585f-ct-npu ]; then
  profiles=(npu)
fi
for profile in "${profiles[@]}"; do
  case "$profile" in
    native) pattern='*fiberhome_hg5585f-ct-*'; flag= ;;
    wed) pattern='*fiberhome_hg5585f-ct-wed-*'; flag=--wed ;;
    npu) pattern='*fiberhome_hg5585f-ct-npu-*'; flag=--npu ;;
  esac
  recovery_count=0
  sysupgrade_count=0
  while IFS= read -r image; do
    case "$profile:$image" in native:*-ct-wed-*|native:*-ct-npu-*) continue ;; esac
    case "$image" in
      *initramfs-recovery.itb) recovery_count=$((recovery_count + 1)) ;;
      *sysupgrade.itb) sysupgrade_count=$((sysupgrade_count + 1)) ;;
      *) echo "Unexpected $profile image name: $image"; exit 1 ;;
    esac
    if [ -n "$flag" ]; then
      python3 scripts/check-hg5585f-images.py "$flag" "$image"
    else
      python3 scripts/check-hg5585f-images.py "$image"
    fi
  done < <(find bin/targets/airoha/an7581 -maxdepth 1 -name "$pattern.itb" -type f)
  [ "$recovery_count" -ge 1 ] && [ "$sysupgrade_count" -ge 1 ] || {
    echo "Missing recovery/sysupgrade image pair for $profile (recovery=$recovery_count sysupgrade=$sysupgrade_count)"
    exit 1
  }
done
