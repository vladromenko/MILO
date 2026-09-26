#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
ARCHIVE="milo-agent-aarch64-ros-jazzy-v0.2.0.tar.gz"
URL="https://github.com/vladromenko/MILO/releases/download/v0.2.0/$ARCHIVE"
EXPECTED="e82ddda722bbcfb94afc494758c93f60c85bd092a8a03f903ca1ea2022445be8"
CACHE="$ROOT/assets/runtime/$ARCHIVE"

if [[ -x vendor/agent/check-libs ]]; then
  vendor/agent/check-libs
  echo "[READY] verified existing micro-ROS agent bundle"
  exit 0
fi
[[ ! -e vendor/agent ]] || {
  echo "[BLOCKED] vendor/agent exists but is incomplete; inspect it before retrying"
  exit 1
}

mkdir -p "$ROOT/assets/runtime" "$ROOT/vendor"
if [[ ! -f "$CACHE" ]]; then
  curl -L --fail --retry 5 -o "$CACHE.partial" "$URL"
  mv "$CACHE.partial" "$CACHE"
fi
echo "$EXPECTED  $CACHE" | sha256sum --check --status || {
  echo "[BLOCKED] micro-ROS agent archive checksum mismatch"
  exit 1
}

WORK="$(mktemp -d "$ROOT/vendor/.agent.XXXXXX")"
cleanup() { rm -rf -- "$WORK"; }
trap cleanup EXIT
mkdir "$WORK/agent"
tar -xzf "$CACHE" --strip-components=1 -C "$WORK/agent"
"$WORK/agent/check-libs"
mv "$WORK/agent" vendor/agent
echo "[READY] installed verified Pi micro-ROS agent bundle"
