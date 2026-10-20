#!/usr/bin/env bash
set -euo pipefail

ROOT=/root/autodl-tmp/webcraft
for version in 1.16.5 1.19 1.21.11; do
  CUDA_VISIBLE_DEVICES='' nice -n 15 "$ROOT/scripts/stop_one.sh" "$version"
done
