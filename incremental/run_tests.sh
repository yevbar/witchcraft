#!/usr/bin/env bash
# Run the full elastic-incremental engine test suite (correctness + benchmarks) from the repo root.
# The first run compiles the per-program .so once (~4-7 min, cached in /tmp); later runs are fast.
# Each test prints a "... PASS ✓" / "FAIL ✗" line and exits non-zero on failure.
set -uo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"

if [[ ! -x third_party/souffle/build/src/souffle ]]; then
  echo "souffle fork not built — run: bash incremental/build_souffle.sh" >&2
  exit 1
fi

# Correctness oracles first (broad -> narrow), then the benchmarks.
TESTS=(
  test_harness test_update test_incremental test_negation test_selective
  test_deletion test_nullary test_simultaneous_delete test_branching
  test_engine test_sequential test_fuzz
)

fail=0
for t in "${TESTS[@]}"; do
  out="$(python3 "incremental/harness/$t.py" 2>&1)"
  line="$(printf '%s\n' "$out" | grep -iE 'PASS|FAIL|✓|✗' | tail -1)"
  printf '%-28s %s\n' "$t" "${line:-<no result — see below>}"
  if printf '%s\n' "$out" | grep -qiE 'FAIL|✗|Traceback'; then
    fail=1
    printf '%s\n' "$out" | tail -15
  fi
done

echo "----"
if [[ $fail -eq 0 ]]; then
  echo "ALL TESTS PASS ✓"
else
  echo "SOME TESTS FAILED ✗" >&2
fi
exit $fail
