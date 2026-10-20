#!/usr/bin/env bash
set -euo pipefail

ROOT=/root/autodl-tmp/webcraft
TMUX="$ROOT/env/tools/bin/tmux"
SOCKET="$ROOT/logs/tmux.sock"
version=${1:?version required}

case "$version" in
  1.16.5) session=wc_mc_1165; game_port=25571; rcon_port=25581; heap_gib=2 ;;
  1.19) session=wc_mc_119; game_port=25572; rcon_port=25582; heap_gib=2 ;;
  1.21.11) session=wc_mc_12111; game_port=25573; rcon_port=25583; heap_gib=2 ;;
  *) echo "unsupported version: $version" >&2; exit 2 ;;
esac

available_kib=$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)
required_kib=$(( (heap_gib + 8) * 1024 * 1024 ))
if (( available_kib < required_kib )); then
  echo "insufficient memory for $version plus 8 GiB reserve" >&2
  exit 3
fi
for port in "$game_port" "$rcon_port"; do
  if ss -ltnH | awk '{print $4}' | grep -Eq "(^|:)$port$"; then
    echo "port already occupied: $port" >&2
    exit 4
  fi
done
if "$TMUX" -S "$SOCKET" has-session -t "$session" 2>/dev/null; then
  echo "session already exists: $session" >&2
  exit 5
fi

log="$ROOT/logs/server-$version.console.log"
: > "$log"
nice -n 15 "$TMUX" -S "$SOCKET" new-session -d -s "$session" \
  "exec '$ROOT/scripts/run_server.sh' '$version' >> '$log' 2>&1"

for _ in $(seq 1 180); do
  if grep -q 'Done (' "$log" 2>/dev/null; then
    echo "$version ready"
    exit 0
  fi
  if ! "$TMUX" -S "$SOCKET" has-session -t "$session" 2>/dev/null; then
    echo "$version exited before ready" >&2
    tail -n 80 "$log" >&2
    exit 6
  fi
  sleep 1
done
echo "$version startup timeout" >&2
exit 7
