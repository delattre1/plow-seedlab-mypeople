#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TAG=${TTYD_TAG:-1.7.7}
DEST=${1:-"$HOME/.local/share/mypeople/bin/ttyd-mypeople"}
WORK=${TTYD_BUILD_DIR:-"${TMPDIR:-/tmp}/mypeople-ttyd-build"}

rm -rf "$WORK"
git clone --depth 1 --branch "$TAG" https://github.com/tsl0922/ttyd.git "$WORK"
git -C "$WORK" apply --unidiff-zero "$ROOT/patches/ttyd-1.7.7-macos-disconnect.patch"

if command -v cmake >/dev/null 2>&1; then
  CMAKE=cmake
elif command -v uvx >/dev/null 2>&1; then
  CMAKE="uvx --from cmake cmake"
else
  echo "cmake or uvx is required" >&2
  exit 1
fi

# CMAKE may intentionally contain the uvx launcher and its arguments.
# shellcheck disable=SC2086
$CMAKE -S "$WORK" -B "$WORK/build" -DCMAKE_BUILD_TYPE=Release
# shellcheck disable=SC2086
$CMAKE --build "$WORK/build" -j4
mkdir -p "$(dirname -- "$DEST")"
install -m 0755 "$WORK/build/ttyd" "$DEST"
"$DEST" --version
