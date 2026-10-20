#!/usr/bin/env bash
set -euo pipefail

ROOT=/root/autodl-tmp/webcraft
MANIFEST="$ROOT/servers/version_manifest_v2.json"
VERSIONS=(1.16.5 1.19 1.21.11)

export CUDA_VISIBLE_DEVICES=""

curl --fail --location --retry 2 --connect-timeout 15 --max-time 180 \
  https://piston-meta.mojang.com/mc/game/version_manifest_v2.json \
  --output "$MANIFEST"

for version in "${VERSIONS[@]}"; do
  directory="$ROOT/servers/$version"
  version_json="$directory/version.json"
  readarray -t metadata < <(python3 - "$MANIFEST" "$version" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    manifest = json.load(handle)
record = next((item for item in manifest["versions"] if item["id"] == sys.argv[2]), None)
if record is None:
    raise SystemExit(f"version not found: {sys.argv[2]}")
print(record["url"])
print(record["sha1"])
PY
  )
  curl --fail --location --retry 2 --connect-timeout 15 --max-time 180 \
    "${metadata[0]}" --output "$version_json"
  printf '%s  %s\n' "${metadata[1]}" "$version_json" | sha1sum --check --status

  readarray -t server < <(python3 - "$version_json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    data = json.load(handle)
entry = data["downloads"]["server"]
print(entry["url"])
print(entry["sha1"])
print(data.get("javaVersion", {}).get("majorVersion", "UNKNOWN"))
PY
  )
  printf '%s\n' "${server[2]}" > "$directory/java_major.txt"
  if ! curl --fail --location --retry 2 --connect-timeout 15 --max-time 900 \
      "${server[0]}" --output "$directory/server.jar"; then
    curl --fail --location --retry 2 --connect-timeout 15 --max-time 900 \
      "https://bmclapi2.bangbang93.com/version/$version/server" \
      --output "$directory/server.jar"
  fi
  if ! printf '%s  %s\n' "${server[1]}" "$directory/server.jar" | sha1sum --check --status; then
    rm -f "$directory/server.jar"
    echo "SHA-1 mismatch for $version" >&2
    exit 1
  fi
  printf '%s  server.jar\n' "${server[1]}" > "$directory/server.jar.sha1"
  echo "$version java=${server[2]} sha1=${server[1]}"
done
