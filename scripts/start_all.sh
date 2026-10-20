#!/usr/bin/env bash
set -euo pipefail

ROOT=/root/autodl-tmp/webcraft
available_kib=$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)
required_kib=$((13 * 1024 * 1024))
if (( available_kib < required_kib )); then
  echo "insufficient memory for three servers plus 8 GiB reserve" >&2
  exit 3
fi
for version in 1.16.5 1.19 1.21.11; do
  CUDA_VISIBLE_DEVICES='' nice -n 15 "$ROOT/scripts/start_one.sh" "$version"
done
