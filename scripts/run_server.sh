#!/usr/bin/env bash
set -euo pipefail

ROOT=/root/autodl-tmp/webcraft
version=${1:?version required}
export CUDA_VISIBLE_DEVICES=""

case "$version" in
  1.16.5)
    java="$ROOT/env/jdk17/bin/java"
    heap=1536M
    ;;
  1.19)
    java="$ROOT/env/jdk17/bin/java"
    heap=1536M
    ;;
  1.21.11)
    java="$ROOT/env/jdk21/bin/java"
    heap=2G
    ;;
  *)
    echo "unsupported version: $version" >&2
    exit 2
    ;;
esac

cd "$ROOT/servers/$version"
printf '%s\n' "$$" > "$ROOT/logs/server-$version.pid"
exec nice -n 15 "$java" -XX:ActiveProcessorCount=2 -Xms512M -Xmx"$heap" \
  -jar "$ROOT/servers/$version/server.jar" nogui
