#!/usr/bin/env bash
set -euo pipefail

ROOT=/root/autodl-tmp/webcraft
TMUX="$ROOT/env/tools/bin/tmux"
SOCKET="$ROOT/logs/tmux.sock"

for entry in '1.16.5 wc_mc_1165 25571 25581' '1.19 wc_mc_119 25572 25582' '1.21.11 wc_mc_12111 25573 25583'; do
  read -r version session game_port rcon_port <<< "$entry"
  if "$TMUX" -S "$SOCKET" has-session -t "$session" 2>/dev/null; then state=running; else state=stopped; fi
  printf '%s session=%s game=%s rcon=%s\n' "$version" "$state" \
    "$(ss -ltnH | awk -v p=":$game_port" '$4 ~ p"$" {print $4}' | paste -sd, -)" \
    "$(ss -ltnH | awk -v p=":$rcon_port" '$4 ~ p"$" {print $4}' | paste -sd, -)"
done
