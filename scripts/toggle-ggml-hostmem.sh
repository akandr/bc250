#!/usr/bin/env bash
# Toggle GGML_VK_PREFER_HOST_MEMORY in the ollama service drop-in and restart.
# Usage: toggle-ggml-hostmem.sh on|off
# Requires passwordless sudo (which bc250 user already has).
set -euo pipefail
MODE="${1:?usage: $0 on|off}"
DROPIN=/etc/systemd/system/ollama.service.d/override.conf
[[ -f "$DROPIN" ]] || { echo "drop-in $DROPIN missing"; exit 1; }
BAK="$DROPIN.bak.$(date +%s)"
sudo -n cp "$DROPIN" "$BAK"
case "$MODE" in
  on)
    if ! grep -q '^Environment="GGML_VK_PREFER_HOST_MEMORY=1"' "$DROPIN"; then
      sudo -n sed -i '/^\[Service\]/a Environment="GGML_VK_PREFER_HOST_MEMORY=1"' "$DROPIN"
    fi
    ;;
  off)
    sudo -n sed -i '/^Environment="GGML_VK_PREFER_HOST_MEMORY=1"/d' "$DROPIN"
    ;;
  *) echo "bad mode: $MODE"; exit 2;;
esac
sudo -n systemctl daemon-reload
sudo -n systemctl restart ollama
sleep 5
systemctl is-active ollama
echo "GGML_VK_PREFER_HOST_MEMORY=$MODE applied (backup: $BAK)"
grep -q '^Environment="GGML_VK_PREFER_HOST_MEMORY=1"' "$DROPIN" && echo "  -> drop-in has env: YES" || echo "  -> drop-in has env: NO"
