#!/bin/bash
# Run after firmware upload; a failed check must still fail the job.
set -euo pipefail

export PATH="$PWD/staging_dir/host/bin:$PATH"
shopt -s nullglob
kernel_configs=(build_dir/target-*/linux-airoha_an7581/linux-[0-9]*/.config)
[ "${#kernel_configs[@]}" -eq 1 ] || {
  echo "Expected one configured AN7581 kernel; found ${#kernel_configs[@]}"
  if [ "${#kernel_configs[@]}" -gt 0 ]; then
    printf 'Kernel candidate: %s\n' "${kernel_configs[@]}"
  fi
  exit 1
}
for kernel_config in "${kernel_configs[@]}"; do
  [ -f "$kernel_config" ] || { echo "Kernel config missing: $kernel_config"; exit 1; }
  kernel=${kernel_config%/.config}
  echo "Checking built kernel source: $kernel"
  python3 "$GITHUB_WORKSPACE/scripts/prepare.py" kernel "$kernel/.config"
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
    mt76_found=$((mt76_found + 1))
  done
  [ "$mt76_found" -gt 0 ] || {
    echo "MT76 build directory missing under: ${kernel%/*}"
    find "${kernel%/*}" -maxdepth 2 -type d -name 'mt76*' -print
    exit 1
  }
done
for profile in native wed npu; do
  case "$profile" in
    native) pattern='*fiberhome_hg5585f-ct-*'; flag= ;;
    wed) pattern='*fiberhome_hg5585f-ct-wed-*'; flag=--wed ;;
    npu) pattern='*fiberhome_hg5585f-ct-npu-*'; flag=--npu ;;
  esac
  count=0
  while IFS= read -r image; do
    case "$profile:$image" in native:*-ct-wed-*|native:*-ct-npu-*) continue ;; esac
    if [ -n "$flag" ]; then
      python3 scripts/check-hg5585f-images.py "$flag" "$image"
    else
      python3 scripts/check-hg5585f-images.py "$image"
    fi
    count=$((count + 1))
  done < <(find bin/targets/airoha/an7581 -maxdepth 1 -name "$pattern.itb" -type f)
  [ "$count" -ge 2 ] || { echo "Missing recovery/sysupgrade images for $profile"; exit 1; }
done
