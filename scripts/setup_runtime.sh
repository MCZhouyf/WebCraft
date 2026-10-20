#!/usr/bin/env bash
set -euo pipefail

ROOT=/root/autodl-tmp/webcraft
ENV_ROOT="$ROOT/env"
export CUDA_VISIBLE_DEVICES=""

# The host JDK 17 is used read-only through a project-local symbolic link.
if [[ ! -e "$ENV_ROOT/jdk17" ]]; then
  ln -s /usr/lib/jvm/java-17-openjdk-amd64 "$ENV_ROOT/jdk17"
fi

if [[ ! -x "$ENV_ROOT/jdk21/bin/java" ]]; then
  metadata="$ENV_ROOT/adoptium-jdk21.json"
  archive="$ENV_ROOT/jdk21.tar.gz"
  curl --fail --location --retry 2 --connect-timeout 15 --max-time 180 \
    'https://api.adoptium.net/v3/assets/latest/21/hotspot?architecture=x64&image_type=jdk&os=linux&vendor=eclipse' \
    --output "$metadata"
  readarray -t package < <(python3 - "$metadata" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    data = json.load(handle)
binary = data[0]["binary"]["package"]
print(binary["link"])
print(binary["checksum"])
PY
  )
  curl --fail --location --retry 2 --connect-timeout 15 --max-time 900 \
    "${package[0]}" --output "$archive"
  printf '%s  %s\n' "${package[1]}" "$archive" | sha256sum --check --status
  mkdir -p "$ENV_ROOT/jdk21.unpack"
  tar -xzf "$archive" -C "$ENV_ROOT/jdk21.unpack"
  extracted=$(find "$ENV_ROOT/jdk21.unpack" -mindepth 1 -maxdepth 1 -type d | head -n 1)
  mv "$extracted" "$ENV_ROOT/jdk21"
  rmdir "$ENV_ROOT/jdk21.unpack"
  rm -f "$archive"
fi

if [[ ! -x "$ENV_ROOT/node/bin/node" ]]; then
  index="$ENV_ROOT/node-index.json"
  if ! curl --fail --location --retry 2 --connect-timeout 15 --max-time 180 \
      https://nodejs.org/dist/index.json --output "$index"; then
    curl --fail --location --retry 2 --connect-timeout 15 --max-time 180 \
      https://npmmirror.com/mirrors/node/index.json --output "$index"
  fi
  node_version=$(python3 - "$index" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    versions = json.load(handle)
print(next(item["version"] for item in versions if item["version"].startswith("v22.") and item.get("lts")))
PY
  )
  archive_name="node-$node_version-linux-x64.tar.xz"
  base="https://nodejs.org/dist/$node_version"
  archive="$ENV_ROOT/$archive_name"
  checksums="$ENV_ROOT/node-SHASUMS256.txt"
  if ! curl --fail --location --retry 2 --connect-timeout 15 --max-time 600 \
      "$base/$archive_name" --output "$archive"; then
    base="https://npmmirror.com/mirrors/node/$node_version"
    curl --fail --location --retry 2 --connect-timeout 15 --max-time 600 \
      "$base/$archive_name" --output "$archive"
  fi
  curl --fail --location --retry 2 --connect-timeout 15 --max-time 180 \
    "$base/SHASUMS256.txt" --output "$checksums"
  expected=$(awk -v name="$archive_name" '$2 == name {print $1}' "$checksums")
  printf '%s  %s\n' "$expected" "$archive" | sha256sum --check --status
  mkdir -p "$ENV_ROOT/node.unpack"
  tar -xJf "$archive" -C "$ENV_ROOT/node.unpack"
  extracted=$(find "$ENV_ROOT/node.unpack" -mindepth 1 -maxdepth 1 -type d | head -n 1)
  mv "$extracted" "$ENV_ROOT/node"
  rmdir "$ENV_ROOT/node.unpack"
  rm -f "$archive"
fi

if [[ ! -x "$ENV_ROOT/tools/bin/tmux" ]]; then
  mkdir -p "$ENV_ROOT/conda-home" "$ENV_ROOT/conda-pkgs"
  HOME="$ENV_ROOT/conda-home" CONDA_PKGS_DIRS="$ENV_ROOT/conda-pkgs" \
    /root/miniconda3/bin/conda create --yes --no-default-packages \
    --prefix "$ENV_ROOT/tools" --override-channels --channel conda-forge tmux
fi

"$ENV_ROOT/jdk17/bin/java" -version
"$ENV_ROOT/jdk21/bin/java" -version
"$ENV_ROOT/node/bin/node" --version
"$ENV_ROOT/node/bin/npm" --version
"$ENV_ROOT/tools/bin/tmux" -V
