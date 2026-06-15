#!/usr/bin/env bash
# Toggle bc250_cc_write_mode modparam between 0 (24 CU baseline) and 3 (40 CU unlock).
# Edits /etc/modprobe.d/bc250-40cu.conf and REQUIRES REBOOT to take effect.
# Usage: toggle-cu-mode.sh 0|3
set -euo pipefail
MODE="${1:?usage: $0 0|3   (0 = 24 CU baseline, 3 = 40 CU unlock)}"
[[ "$MODE" =~ ^[03]$ ]] || { echo "mode must be 0 or 3"; exit 2; }
CONF=/etc/modprobe.d/bc250-40cu.conf
[[ -f "$CONF" ]] || { echo "$CONF missing -- 40CU unlock not installed?"; exit 1; }
BAK="$CONF.bak.$(date +%s)"
sudo -n cp "$CONF" "$BAK"
# Replace whatever bc250_cc_write_mode=N value is there with our target.
if grep -q 'bc250_cc_write_mode=' "$CONF"; then
  sudo -n sed -i -E "s/(bc250_cc_write_mode=)[0-9]+/\1${MODE}/" "$CONF"
else
  echo "options amdgpu bc250_cc_write_mode=${MODE}" | sudo -n tee -a "$CONF" >/dev/null
fi
echo "modparam set to bc250_cc_write_mode=${MODE} (backup: $BAK)"
cat "$CONF"
# amdgpu is loaded from the initramfs on this box, so a modprobe.d edit
# alone is NOT picked up after reboot. Regenerate initramfs for the
# running kernel so the new modparam actually takes effect.
KVER="$(uname -r)"
echo "regenerating initramfs for kernel $KVER (this can take ~30s)..."
sudo -n dracut -f "/boot/initramfs-${KVER}.img" "$KVER"
echo "initramfs rebuilt; REBOOT REQUIRED:  sudo systemctl reboot"
