#!/usr/bin/env bash
set -euo pipefail

ROOT=/root/autodl-tmp/webcraft
version=${1:?version required}
arena_index=${2:-0}
export CUDA_VISIBLE_DEVICES=""
export HOME="$ROOT/env/npm-home"
export PATH="$ROOT/env/node/bin:/usr/bin:/bin"
exec nice -n 15 "$ROOT/env/node/bin/node" "$ROOT/harness/src/reset_arena.mjs" "$version" "$arena_index"
