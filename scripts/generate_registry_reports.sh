#!/usr/bin/env bash
set -euo pipefail

ROOT=/root/autodl-tmp/webcraft
TMP="$ROOT/truth/tmp"
export CUDA_VISIBLE_DEVICES=""

run_report() {
  local version=$1 java=$2 mode=$3
  local output="$TMP/reports-$version"
  mkdir -p "$output"
  cd "$output"
  if [[ "$mode" == classpath ]]; then
    exec nice -n 15 "$java" -XX:ActiveProcessorCount=2 -Xms256M -Xmx1536M \
      -cp "$ROOT/servers/$version/server.jar" net.minecraft.data.Main --reports
  else
    exec nice -n 15 "$java" -XX:ActiveProcessorCount=2 -Xms256M -Xmx2G \
      -DbundlerMainClass=net.minecraft.data.Main \
      -jar "$ROOT/servers/$version/server.jar" --reports
  fi
}

case "${1:?version required}" in
  1.16.5) run_report 1.16.5 "$ROOT/env/jdk17/bin/java" classpath ;;
  1.19) run_report 1.19 "$ROOT/env/jdk17/bin/java" bundler ;;
  1.21.11) run_report 1.21.11 "$ROOT/env/jdk21/bin/java" bundler ;;
  *) echo "unsupported version" >&2; exit 2 ;;
esac
