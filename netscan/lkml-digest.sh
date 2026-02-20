#!/bin/bash
# lkml-digest.sh — backward-compat wrapper for lore-digest.sh
# Runs the linux-media feed. Use lore-digest.sh directly for other feeds.
exec "$(dirname "$(readlink -f "$0")")/lore-digest.sh" --feed linux-media
