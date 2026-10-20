#!/usr/bin/env bash
set -euo pipefail

ROOT=/root/autodl-tmp/webcraft
TMUX="$ROOT/env/tools/bin/tmux"
SOCKET="$ROOT/logs/tmux.sock"
source "$ROOT/scripts/.rcon_secrets"
version=${1:?version required}

case "$version" in
  1.16.5) session=wc_mc_1165; rcon_port=25581; password=$RCON_1165 ;;
  1.19) session=wc_mc_119; rcon_port=25582; password=$RCON_119 ;;
  1.21.11) session=wc_mc_12111; rcon_port=25583; password=$RCON_12111 ;;
  *) echo "unsupported version: $version" >&2; exit 2 ;;
esac

if ! "$TMUX" -S "$SOCKET" has-session -t "$session" 2>/dev/null; then
  echo "$version not running"
  exit 0
fi

CUDA_VISIBLE_DEVICES='' nice -n 15 python3 "$ROOT/scripts/rcon.py" \
  --port "$rcon_port" --password "$password" stop || true
for _ in $(seq 1 60); do
  if ! "$TMUX" -S "$SOCKET" has-session -t "$session" 2>/dev/null; then
    echo "$version stopped"
    exit 0
  fi
  sleep 1
done
"$TMUX" -S "$SOCKET" kill-session -t "$session"
echo "$version tmux session closed after graceful-stop timeout"
