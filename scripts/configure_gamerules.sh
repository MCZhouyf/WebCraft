#!/usr/bin/env bash
set -euo pipefail

ROOT=/root/autodl-tmp/webcraft
source "$ROOT/scripts/.rcon_secrets"

apply_rules() {
  local port=$1 password=$2
  shift 2
  for rule in "$@"; do
    CUDA_VISIBLE_DEVICES='' nice -n 15 python3 "$ROOT/scripts/rcon.py" \
      --port "$port" --password "$password" gamerule "$rule" false >/dev/null
  done
  CUDA_VISIBLE_DEVICES='' nice -n 15 python3 "$ROOT/scripts/rcon.py" \
    --port "$port" --password "$password" time set day >/dev/null
}

legacy=(doDaylightCycle doWeatherCycle doMobSpawning doTraderSpawning doPatrolSpawning doInsomnia)
modern=(advance_time advance_weather spawn_mobs spawn_wandering_traders spawn_patrols spawn_phantoms)

apply_rules 25581 "$RCON_1165" "${legacy[@]}"
apply_rules 25582 "$RCON_119" "${legacy[@]}"
apply_rules 25583 "$RCON_12111" "${modern[@]}"
