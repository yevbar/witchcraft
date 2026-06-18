# RUNBOOK — elastic incremental MTG engine (build & validate from scratch)

This branch (`elastic-incremental-engine`) contains the **elastic incremental Datalog engine**: a fork of
Soufflé (`third_party/souffle`) with an `--incremental` translation strategy that re-evaluates only the strata
affected by an input change instead of recomputing from scratch. It is byte-identical to a full recompute and
supports game-tree-search rollback. Full overview: **`incremental/README.md`**.

These steps reproduce the build and the test suite on a fresh machine. **Linux-first** (the Mac Mini target runs
Linux); macOS notes inline. Run everything from the repo root unless noted. Python is **stdlib-only** (no `pip`),
and **no separate Soufflé install is needed** — both the incremental engine *and* the recompute backends (the
byte-identity baseline) use the in-repo fork built in step 2 (it does standard, non-incremental codegen too).

---

## 0. Prerequisites

A C++17 toolchain, CMake, Bison ≥ 3.2, Flex, Python 3.9+, and Soufflé's build deps.

**Linux (Fedora/RHEL):**
```bash
sudo dnf install -y git cmake bison flex g++ python3 libffi-devel ncurses-devel zlib-ng-devel
```
**Linux (Debian/Ubuntu):**
```bash
sudo apt-get install -y git cmake bison flex g++ python3 libffi-dev libncurses-dev zlib1g-dev
```
**macOS:** `xcode-select --install`, then `brew install cmake bison` (Apple's `/usr/bin/bison` is 2.3, too old;
`build_souffle.sh` auto-uses the brew one). Apple Silicon and Intel both work.

Check: `cmake --version` (≥3.15), `bison --version` (≥3.2), `g++ --version` (C++17), `python3 --version`.

---

## 1. Clone + the souffle submodule

The engine fork is the submodule `third_party/souffle` (`github.com/yevbar/souffle`). Its `.gitmodules` URL is
SSH (`git@github.com:yevbar/souffle.git`) — either have SSH access to that repo, or rewrite to HTTPS first:
```bash
git config submodule.third_party/souffle.url https://github.com/yevbar/souffle.git   # if no SSH key
git submodule update --init third_party/souffle
```
> Only `third_party/souffle` is needed. `third_party/souffle-elastic` (the 2019 davidwzhao fork) is a
> reference-algorithm copy and can be left uninitialized.

---

## 2. Build the souffle fork

```bash
bash incremental/build_souffle.sh
```
This runs CMake + `make` (Release) and prints a toy sanity check (transitive closure with a multi-support
retraction — the re-discovery case the incremental Update must reproduce). It produces the binary:
```
third_party/souffle/build/src/souffle
```
Build takes a few minutes. If it fails on Bison, your system Bison is < 3.2 (install a newer one).

---

## 3. Validate

The per-program engine `.so` (the souffle-generated C++ + the ctypes shim `incremental/harness/shim.cpp`) is
compiled **on demand** by the harness with `g++`/`clang++`, then cached in `/tmp` by content hash. **The first
test that touches it pays a one-time ~4–7 min compile; everything after is fast.**

Run the full suite (correctness + the search/perf benchmarks):
```bash
bash incremental/run_tests.sh
```
Expected: every line ends `PASS ✓`. The key oracles:
- `test_engine` / `test_sequential` — update == fresh recompute on real states.
- `test_fuzz` — randomized differential (320 checks; scale with `FUZZ_SEEDS=20 FUZZ_STEPS=100` for a 2000-check
  stress, also clean).
- `test_branching` — push/pop/branch rollback == recompute.

**Strongest single check — demo byte-identity** (the incremental backend must match the recompute backend over a
whole game):
```bash
python3 -c "import driver; driver.demo()"                > /tmp/demo_recompute.txt
MTG_INCREMENTAL=1 python3 -c "import driver; driver.demo()" > /tmp/demo_incremental.txt
diff /tmp/demo_recompute.txt /tmp/demo_incremental.txt && echo "BYTE-IDENTICAL ✓"
```

---

## 4. Use the incremental engine

```python
import engine_incremental as E          # in-process fork --incremental .so, ctypes bridge
E.evaluate(fkey)                         # bootstrap once, then incremental updates (auto-diffs from last state)
# game-tree search primitives (O(diff), no snapshot):
result = E.push(child_fkey)              # apply a move, remember the parent
E.pop()                                  # roll back via the inverse diff
with E.branch(child_fkey) as result: ... # push + auto-pop on exit
```
`fkey` is `driver._facts_key(state)`. In the driver, the incremental backend is **opt-in** via the env var —
`MTG_INCREMENTAL=1` routes `driver._evaluate` / `driver.run` (and the real search layer `search.py`) through it.
Default backend is `engine_inproc` (full-fixpoint recompute); both are byte-identical.

---

## 5. Benchmarks (optional)

```bash
python3 incremental/harness/bench.py          # single-move speedup vs recompute (~2x at 100–500 facts)
python3 incremental/harness/search_bench.py   # where it helps the real search (crossover ~250 facts)
python3 incremental/harness/profile_strata.py # per-stratum recompute cost (the hotspot)
```

---

## Notes & gotchas

- **`.so` cache:** keyed by `dl_text + shim.cpp + souffle binary mtime`. Rebuilding souffle (step 2) or editing
  the rules/shim invalidates it → the next run recompiles (~4–7 min). The recompute-relation set is also cached
  (`/tmp/mtg_recompute_*.txt`).
- **No MTG references in `third_party/souffle/`** — the souffle engine is domain-agnostic by design (say
  "witchcraft" only if a comment truly needs a domain word). All MTG logic lives in `datalog/` + the Python.
- **Where things are:** the `--incremental` C++ strategy is `third_party/souffle/src/ast2ram/incremental/`;
  the ctypes shim + harness + tests are `incremental/harness/`; the Python backends are `engine_incremental.py`
  / `engine_inproc.py` / `engine_native.py`; the rules are `datalog/`.
- **Status & history:** `incremental/README.md` (overview, perf envelope, frontiers),
  `incremental/PHASE3_UPDATE_PLAN.md` (per-phase reasoning), `incremental/experiments/README.md` (measured
  dead-ends — do not re-attempt precise-publish/`cond_met`).
