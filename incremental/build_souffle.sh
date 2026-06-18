#!/usr/bin/env bash
# Build our souffle fork (third_party/souffle = yevbar/souffle, latest 2.x CMake) — the clean base for the
# elastic-incremental feature. Reproducible on macOS and the mac mini's Linux. (The 2019 davidwzhao fork in
# third_party/souffle-elastic is the reference algorithm only — see SOUFFLE_ELASTIC_BUILD_NOTES.md.)
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
SF="$HERE/third_party/souffle"

git -C "$HERE" submodule update --init third_party/souffle

CMAKE_ARGS=(-S "$SF" -B "$SF/build" -DCMAKE_BUILD_TYPE=Release -DSOUFFLE_USE_CURSES=OFF -DSOUFFLE_USE_SQLITE=OFF)
if [[ "$(uname)" == "Darwin" ]]; then
  # Apple's /usr/bin/bison is 2.3; souffle needs >=3.2 -> brew bison (keg-only).
  command -v brew >/dev/null && brew list bison >/dev/null 2>&1 || brew install bison
  CMAKE_ARGS+=(-DBISON_EXECUTABLE="$(brew --prefix bison)/bin/bison")
fi
# Linux: install via the distro first, e.g. Fedora:
#   dnf install cmake bison flex libffi-devel ncurses-devel zlib-ng-devel g++   (system bison is 3.x — fine)

cmake "${CMAKE_ARGS[@]}"
cmake --build "$SF/build" -j"$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)"

echo "== built: $SF/build/src/souffle =="
"$SF/build/src/souffle" --version | head -2

# toy sanity: transitive closure + the multi-support retraction the incremental Update must reproduce.
mkdir -p /tmp/tc_out
"$SF/build/src/souffle" "$HERE/incremental/toy/tc.dl" -F "$HERE/incremental/toy" -D /tmp/tc_out
echo "== toy path (E with edge(1,3)): $(sort /tmp/tc_out/path.csv | tr '\t' ',' | tr '\n' ' ')"
echo "(removing edge(1,3) must KEEP path(1,3) via 1->2->3 — the re-discovery case the Update term handles)"
