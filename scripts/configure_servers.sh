#!/usr/bin/env bash
set -euo pipefail

ROOT=/root/autodl-tmp/webcraft
source "$ROOT/scripts/.rcon_secrets"

write_properties() {
  local version=$1 game_port=$2 rcon_port=$3 password=$4 level_type=$5 secure_profile=$6 simulation=$7
  local directory="$ROOT/servers/$version"
  printf 'eula=true\n' > "$directory/eula.txt"
  {
    printf 'server-ip=127.0.0.1\n'
    printf 'server-port=%s\n' "$game_port"
    printf 'online-mode=false\n'
    if [[ -n "$secure_profile" ]]; then printf 'enforce-secure-profile=false\n'; fi
    printf 'enable-rcon=true\n'
    printf 'rcon.port=%s\n' "$rcon_port"
    printf 'rcon.password=%s\n' "$password"
    printf 'gamemode=survival\n'
    printf 'force-gamemode=true\n'
    printf 'difficulty=peaceful\n'
    printf 'spawn-monsters=false\n'
    printf 'spawn-protection=0\n'
    printf 'view-distance=4\n'
    if [[ -n "$simulation" ]]; then printf 'simulation-distance=4\n'; fi
    printf 'max-players=8\n'
    printf 'level-type=%s\n' "$level_type"
    printf 'level-name=world\n'
    printf 'level-seed=webcraft-stage0\n'
    printf 'enable-status=false\n'
    printf 'enable-query=false\n'
    printf 'enable-command-block=false\n'
    printf 'allow-flight=false\n'
    printf 'white-list=false\n'
    printf 'max-tick-time=60000\n'
    printf 'motd=WebCraft isolated test\n'
  } > "$directory/server.properties"
}

write_properties 1.16.5 25571 25581 "$RCON_1165" flat '' ''
write_properties 1.19 25572 25582 "$RCON_119" 'minecraft\:flat' yes yes
write_properties 1.21.11 25573 25583 "$RCON_12111" 'minecraft\:flat' yes yes
