#!/usr/bin/env bash
set -euo pipefail
ROOT=/root/autodl-tmp/webcraft
export CUDA_VISIBLE_DEVICES=""
cd "$ROOT"
version=${1:?version required}
nice -n 15 python3 -B scripts/stage_preflight.py
trap 'nice -n 15 bash "$ROOT/scripts/stop_one.sh" "$version"' EXIT
nice -n 15 bash scripts/start_one.sh "$version"
nice -n 15 "$ROOT/env/node/bin/node" "$ROOT/harness/src/reset_arena.mjs" "$version" 0
nice -n 15 "$ROOT/env/node/bin/node" "$ROOT/harness/src/stage1_leakage_probe.mjs" "$version"
