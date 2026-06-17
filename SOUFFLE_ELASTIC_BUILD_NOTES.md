# Soufflé-elastic — Phase-A build/API/integration findings

Executes Phase A of `SOUFFLE_ELASTIC_PLAN.md` (de-risk the elastic fork in parallel). Target:
`davidwzhao/souffle` @ branch `incremental-with-provenance-eager-diffs` — the PPDP'21 "Towards Elastic
Incrementalization for Datalog" implementation.

**Go/No-go headline (UPDATED):** the fork is **vendored, fully analyzed, and now BUILT + Phase-A-validated on
this box** — the original "needs the mac mini" verdict is SUPERSEDED (see the UPDATE section). With autotools
installed as root and **`mcpp` built from source (no root)**, the fork compiles and `--incremental` works: the
toy insert/delete reproduces the full-recompute oracle including multi-support retraction. **Phase B is now
feasible here.** (Originally the build was blocked at the toolchain layer — no GNU autotools, no `mcpp`, no
root — never at compile and never memory; that's what the reference sections below first documented.)

---

## UPDATE — built + Phase-A-validated ON THIS BOX (supersedes the "needs the mac mini" verdict)

Once `autoconf`/`automake`/`libtool` were installed (root, Fedora repos) and **`mcpp` was built from source**
(NO root — Soufflé only needs the `mcpp` *binary on PATH*), the fork built and elastic `--incremental` was
validated here. Exact working recipe:

### mcpp 2.7.2 from source (the one unpackaged dep; `~/.local`, no root)
GCC 15 / aarch64 needs fixes the stock 2008 build lacks:
```bash
curl -fsSL -o /tmp/mcpp-2.7.2.tar.gz \
  https://downloads.sourceforge.net/project/mcpp/mcpp/V.2.7.2/mcpp-2.7.2.tar.gz
cd /tmp && tar xf mcpp-2.7.2.tar.gz && cd mcpp-2.7.2
./configure --prefix=$HOME/.local \
  --build=aarch64-unknown-linux-gnu --host=aarch64-unknown-linux-gnu \   # 2008 config.guess can't detect aarch64
  CFLAGS="-std=gnu11 -fcommon -fpermissive -w"                            # C23 true/false keywords + GCC14 permerrors
sed -i 's|/\* #undef LL_FORM \*/|#define LL_FORM "ll"|' src/config.h      # long-long printf fmt left undefined here
make && make install                                                      # -> ~/.local/bin/mcpp
export PATH=$HOME/.local/bin:$PATH
```

### The fork itself (compiled clean once autotools+mcpp present)
```bash
git submodule update --init third_party/souffle-elastic
cd third_party/souffle-elastic && export PATH=$HOME/.local/bin:$PATH
./bootstrap                          # autotools chain (non-fatal obsolete-macro warnings)
./configure --prefix=$PWD/install    # finds mcpp + ncurses/zlib/sqlite/libffi
systemd-run --user --scope -p MemoryMax=5G -p MemorySwapMax=2G make -j2   # memory-capped; ~5 min, no OOM
# -> src/souffle  (0.0.3-4408-g540cf8d33; has --incremental AND --provenance=...subtreeHeights)
```
No build errors (only `std::iterator`-deprecated C++17 warnings); `USE_NCURSES/USE_LIBZ/USE_SQLITE` enabled.

### Phase-A toy sanity: PASSED (incremental == recompute, multi-support retraction)
`souffle --incremental -o tc tc.dl`; bootstrap `edge={(1,2),(2,3),(2,4)}` → `path={(1,2),(1,3),(1,4),(2,3),
(2,4)}` (== recompute). Then the incremental REPL on stdin: `remove edge(2, 4)` then `commit` ("Commit done in
epoch 1!") → `out/path.csv = {(1,2),(1,3),(2,3)}` — the EXACT oracle: `(1,4)`/`(2,4)` retracted, `(1,3)`
SURVIVES on its alternate support. The standalone REPL's `commit` dumps the output dir clean (2 cols) — the
`@iteration`/`@count` columns did NOT leak into `path.csv` here (encouraging, but re-verify on the EMBEDDED
path in Phase D). REPL surface: `insert`/`remove`/`commit`/`setdepth`/`explain[diff|all]`/`subproof`/`format`/
`exit`.

---

## Phase B — RAN; NO-GO on this fork (two independent 2019-vs-2026 blockers). Mechanism is sound; the FORK isn't usable for our engine on a modern toolchain.

Phase B = "compile the wrapped `engine_rules.dl` under `--incremental` and assert incremental == full
recompute on real transitions." Result: **the elastic ALGORITHM is correct (Phase-A toy proved it), but the
fork itself cannot build our actual engine on a current toolchain.** Two distinct walls, both reproduced here:

### Blocker #1 — the fork's `--incremental` codegen SEGFAULTS on souffle aggregates
`souffle --incremental` on the full wrapped `engine_rules.dl` (166 EDB rels, ~191 rules) **crashes with
SIGSEGV** during RAM/codegen. Isolated to **aggregates**: our engine uses **46 `count:{…}` aggregate rules**
(scaled_count, multicolored, the cond_met graveyard/threshold counts, …); dropping every aggregate-bearing
rule → `--incremental -g` **succeeds** (emits a 20 MB `.cpp`). So the `incremental-with-provenance-eager-diffs`
branch has **no working aggregate support**. (There is a *sibling* lineage `incremental-evaluation-iterupdate-
eager-diffs-aggregates`, but it's the iterupdate family — i.e. WITHOUT the provenance-based multi-support
retraction we validated in Phase A. You can't get both provenance-retraction AND aggregates from one published
branch.)

### Blocker #2 — even the no-aggregate generated incremental C++ won't compile under g++ 15
Compiling the no-agg `.cpp` (via souffle `-o` AND via direct `g++ -O1`) **fails** — 2019 code vs the 2026
libstdc++:
```
include/souffle/ExplainProvenanceImpl.h:908: error: no match for 'operator<<' (std::ostream, std::vector<int>)
<generated>.cpp:67270: error: redeclaration of 'const souffle::ram::Tuple<int,5> key'  (+ conflicting 'auto range')
```
The first is a missing `operator<<` the modern stdlib no longer provides implicitly; the second is a generated-
code name collision g++ 15 rejects. These are a **cascade of small 2019-vs-now incompatibilities**, not a
one-flag fix. (Note: the fork's *own library* + the `souffle` binary build fine — it's the **code souffle
GENERATES for `--incremental`** that doesn't compile on a modern toolchain.)

### Verdict — this is NOT a hardware gate; a beefier/Mac box won't fix it
root/autotools/mcpp are resolved, the fork builds, and the toy works — so the earlier "needs the mac mini"
framing is the wrong axis. The real gate is **the fork's age + incomplete feature set**: (a) no aggregate
support on the provenance branch, (b) its generated incremental C++ predates modern stdlib. **An ARM Mac would
hit the SAME two walls** (clang's libc++ is at least as strict; the aggregate crash is machine-independent).
The path to a usable incremental engine is therefore the plan's **strategy (B): port/merge the elastic feature
onto modern Soufflé 2.x (CMake)** so (i) aggregates work and (ii) the generated code compiles on current
compilers — a substantial, dedicated effort, not an environment tweak. **Phase D (wire `update` into
`engine_inproc.mtg_run_delta`) stays BLOCKED until that exists** — there's no point integrating an engine that
can't codegen/compile our program. The live optimized engine on master remains inproc + cache + delta-input.

---

## Strategy (B) attempted: port the elastic feature onto latest Soufflé — it's a REIMPLEMENTATION, not a merge

Attempted the upstream-port. Findings (hard data, latest souffle = `souffle-lang/souffle@c3861e0`, which is the
SAME build already installed here):
- **No incremental upstream.** Modern Soufflé has `--provenance` only; "incremental" appears nowhere in its
  source except unit-test filenames. The PPDP'21 feature was never merged.
- **9 of 11 elastic-touched files no longer exist** in modern Soufflé — dissolved by the 2.0 AST/RAM redesign:
  `Incremental.h`, `IncrementalTransformer.cpp`, `AstTranslator.{cpp,h}`, `RamCondition.h`, `AstProgram.h`,
  `AstTransforms.h`, `SynthesiserRelation.cpp`, `ReorderLiteralsTransformer.cpp` are all GONE. Only
  `Synthesiser.cpp` (→ `src/synthesiser/`) and `ExplainProvenanceImpl.h` (→ `src/include/souffle/provenance/`)
  survive by name, and both are rewritten internally. Modern surface: `src/ast` (101 files), `src/ram` (85),
  `src/ast2ram` (5), `src/synthesiser` (8) — the fork's flat `Ast*/Ram*` classes are entirely restructured.
- **Therefore there is no diff to apply** — every fork hunk targets a vanished file/class. `git apply`/merge is
  a non-starter; the port is a from-scratch reimplementation of the research feature on the new architecture.

### What the reimplementation actually entails (the map, for whoever takes it on)
Re-create, against modern Soufflé's pipeline + its existing provenance infra (`--provenance`):
1. **Relation annotation** — add the `@iteration`/`@current_count` columns to every relation (was
   `IncrementalTransformer.cpp`): now an `ast::transform::Transformer` over the modern AST.
2. **Diff machinery** — generate `diff_plus@_`/`diff_minus@_` relations + the re-discovery/retraction rules
   (the `actual_diff_*`/`@new_diff_*` families from `AstTranslator.cpp`): now in `ast2ram/`.
3. **The `update` subroutine** — emit the special incremental-update RAM subroutine + `incremental_update_
   clear_diffs` (was in `Synthesiser.cpp`): now in `src/synthesiser/`.
4. **Runtime/embedding** — the `commit()` driver (`Incremental.h`): the embedded path drives the diff inserts +
   `executeSubroutine("update")` from `engine_inproc` (no REPL).
This is essentially redoing the PPDP'21 implementation on the 2.x codebase — a focused multi-week effort (a
real Soufflé PR), not a session task. Modern Soufflé compiles clean on this box's g++ 15, so a successful
reimplementation would also dissolve Blocker #2 for free (modern codegen). Blocker #1 (aggregates) must be
handled explicitly in the reimplemented transformer/synthesiser (it's where the fork crashed).

### Bottom line
Both prior conclusions hold and are now reinforced with data: the elastic *mechanism* is proven (Phase A), but
obtaining it for our engine requires reimplementing the feature on modern Soufflé — there is no shorter path
(not a beefier box, not a branch swap, not a patch). Until that exists, the live engine stays inproc + cache +
delta-input. The fork + this doc give the next person the exact mechanism, API, integration point, and the
modern-codebase map to do the reimplementation.

---

## Cross-machine reproduction (Linux/aarch64 done here; ARM Mac notes for the next box)

Everything needed to reproduce on another machine. **What's validated by this is the MECHANISM (Phase A);**
Phase B's two blockers above will recur on any modern toolchain until strategy (B) is done.

### Prereqs (both platforms)
- A C++17 compiler, `make`, `bison` (>=3.0.4), `flex`, `m4`, `pkg-config`.
- GNU autotools: `autoconf`, `automake`, `libtool`.
- `mcpp` (Matsui C preprocessor) — Soufflé needs the **binary on PATH** at BOTH configure-time and run-time.
- libs the fork's `configure` wants: `ncurses`, `zlib`, `sqlite3`, `libffi` (dev/headers).

### Linux / aarch64 (Fedora 42 Asahi — what was done here)
- Root (Fedora repos): `dnf install autoconf automake libtool ncurses-devel zlib-ng-devel sqlite-devel libffi-devel`
  (bison/flex/m4/g++/pkg-config already present).
- **`mcpp` is NOT packaged on Fedora** → build from source (no root needed; `~/.local`). On GCC 15/aarch64 it
  needs four fixes (all in the mcpp recipe in the UPDATE section above): the `--build/--host=aarch64` triplet,
  `CFLAGS="-std=gnu11 -fcommon -fpermissive -w"`, and the `config.h` `LL_FORM="ll"` patch.
- Fork: `./bootstrap && ./configure --prefix=$PWD/install && make -j2` (memory-capped) → `src/souffle`.

### ARM Mac (Apple Silicon, macOS) — for whoever takes strategy (B)
- `brew install autoconf automake libtool mcpp bison flex gcc ncurses sqlite libffi`
  — **`mcpp` IS in Homebrew** (prebuilt), so SKIP the Linux from-source mcpp fixes entirely (the LL_FORM /
  aarch64-triplet / gnu11 fixes are specific to building *mcpp from source on Linux*; brew's binary just works).
- brew's `bison`/`flex` are keg-only — put them on PATH before `./configure`
  (`export PATH="$(brew --prefix bison)/bin:$(brew --prefix flex)/bin:$PATH"`), since Apple's system bison is
  too old.
- The fork is written for `g++`; with Apple clang expect divergence — use `brew`'s `g++-15` (`CXX=g++-15`) or
  be ready to patch. **Blocker #2 (generated incremental C++ vs modern stdlib) will be AT LEAST as bad under
  clang/libc++** — so on Mac too, a clean build of *our generated program* requires the strategy-(B) modern-
  Soufflé port, not just installing deps.
- Submodule + toy sanity (machine-independent): `git submodule update --init third_party/souffle-elastic`;
  build; then `third_party/toy_elastic_test/` (`souffle --incremental -o tc tc.dl`, bootstrap, REPL
  `remove edge(2, 4)` + `commit`, compare `out/path.csv` to the recompute oracle).

### Running the incremental engine (the API, once a usable build exists)
Standalone REPL (what Phase A used): run the `--incremental`-compiled binary; on stdin
`insert/remove R(args)` then `commit` (= `executeSubroutine("incremental_update_clear_diffs")` then
`("update")`); read outputs from the `-D` dir. Embedded (Phase D target): drive the same diff relations +
subroutines from `engine_inproc.mtg_run_delta` — see §5 below, and mind the `@iteration`/`@count` output
columns (strip + filter `count<=0`).

---

## (Original Phase-A findings below — the toolchain block is now resolved; kept for reference)

---

## 1. Submodule setup (DONE)

Vendored as a git submodule, pinned to the target branch:

```
.gitmodules:
[submodule "third_party/souffle-elastic"]
    path = third_party/souffle-elastic
    url = https://github.com/davidwzhao/souffle.git
    branch = incremental-with-provenance-eager-diffs
```
- Pinned commit: `540cf8d331e07ca9e2c7d9b44556b867c2df46b4`
  ("Don't return from subroutines immediately otherwise it crashes for parallel").
- The exact branch name in the plan (`incremental-with-provenance-eager-diffs`) DOES exist on the remote
  (`git ls-remote` confirmed `540cf8d3`). Note there is a *sibling* family of branches
  `incremental-evaluation-iterupdate-eager-diffs[-aggregates|-factor-loops|...]`; the plan's branch is the
  `incremental-with-provenance-eager-diffs` one — vendored exactly that.
- Build system: **autotools only** (`bootstrap`, `configure.ac`, `Makefile.am`, `aminclude.am`, `m4/`) —
  **no `CMakeLists.txt`**, confirming the plan's "pre-2.0 autotools" characterization.

## 2. Build attempt — BLOCKED at the toolchain layer (not at compile, not at memory)

The fork's `bootstrap` regenerates `configure` via the GNU autotools chain:
```
bootstrap:  libtoolize --force ; aclocal -I m4 ; autoheader ; automake --gnu --add-missing ; autoconf
```
On this box **every one of those generators is missing**, and there is **no root, no conda, no brew**:

| tool        | status   | needed for                                              |
|-------------|----------|---------------------------------------------------------|
| libtoolize  | MISSING  | `bootstrap` step 1                                      |
| aclocal     | MISSING  | `bootstrap`                                             |
| autoheader  | MISSING  | `bootstrap`                                             |
| automake    | MISSING  | `bootstrap`                                             |
| autoconf    | MISSING  | `bootstrap` (the `pip install autoconf` pkg is a stub, NOT GNU autoconf) |
| **mcpp**    | MISSING  | `configure.ac:128` HARD `AC_MSG_ERROR([mcpp not found.])` AND runtime `which("mcpp")` (`main.cpp:340`) |
| g++ 15.2.1  | present  | the C++17 compile (would work)                          |
| bison 3.8.2 | present  | parser (configure wants >=3.0.4 — OK)                   |
| flex 2.6.4  | present  | lexer — OK                                              |

- `dnf5 install mcpp` → **"No match for argument: mcpp"** (not in the Fedora-Asahi repos at all), and we have
  no sudo regardless. autoconf/automake/libtool are also un-installable here (no root).
- Therefore `./bootstrap` cannot run → no `configure` → no `Makefile` → **`make` was never reached.** No
  compile happened, so the MemoryMax cap / `-j1` discipline never came into play. The build is gated by
  *missing generators + a missing, unpackaged preprocessor*, not by RAM.

### `mcpp` is a double dependency (important for whoever builds this)
`mcpp` (the Matsui C preprocessor) is required **twice**:
1. **build-time**: `configure.ac` hard-errors without it.
2. **runtime**: `main.cpp` / `souffle2lb.cpp` / `souffle2bdd.cpp` all do `::which("mcpp")` and
   `throw std::runtime_error("failed to locate mcpp pre-processor")` — souffle shells out to `mcpp` to
   preprocess `.dl` files before parsing. So even a built `souffle` binary needs `mcpp` on PATH at run time.
   On the mac mini, install `mcpp` (brew has it: `brew install mcpp`) BEFORE building.

### What a build WOULD look like (for the mac mini), unverified here:
```
cd third_party/souffle-elastic
./bootstrap
./configure --prefix=$PWD/install   # add --disable-64bit-domain etc. as needed; provenance on by default
make -j1                            # KEEP LOW PARALLELISM; this is a big C++ compile
make install
```
Known to be fiddly (`davidwzhao/souffle-fault-localization#1`). Expect to chase old-toolchain breakage
(this is 2019-era source; g++ 15 may flag things older souffle ignored — but we never got to find out).

## 3. The incremental insert / delete / commit API (pinned from source — this is the real Phase-A win)

The fork **does NOT add an incremental method to the `SouffleProgram` C++ interface.** `SouffleInterface.h`
on this branch is the familiar 2.x-era surface: `getRelation(name)`, `Relation::insert(tuple)`,
`Relation::purge()`, `run(stratumIndex=-1)`, `purge{Input,Internal,Output}Relations()`,
**`executeSubroutine(name, args, ret, err)`**. The incremental machinery is layered on top of these.

The reference driver is `src/Incremental.h` — an interactive REPL (`startIncremental` / `processCommand`)
emitted into the **standalone** generated `main()` when compiled with `--incremental`. The protocol it
implements (this is the API to replicate from an embedded caller):

**(a) Mark an EDB insertion** — REPL `insert R(args)`:
- target relation = **`"diff_plus@_" + R`**
- tuple = `args` **plus two trailing annotation columns**: `iteration = 0`, `count = +1`
  (`insertTuple()` pushes `"0"` then `"1"`).
- i.e. `getRelation("diff_plus@_R")->insert( <args..., 0, 1> )`.

**(b) Mark an EDB deletion** — REPL `remove R(args)`:
- target relation = **`"diff_minus@_" + R`**
- tuple = `args` plus `iteration = 0`, `count = -1` (`removeTuple()` pushes `"0"` then `"-1"`).
- i.e. `getRelation("diff_minus@_R")->insert( <args..., 0, -1> )`.

  (Both diff relations carry the 2 extra incremental columns `@iteration` / `@current_count` that
  `IncrementalTransformer` appends to every relation — confirmed in `IncrementalTransformer.cpp` ~L291-329.)

**(c) Apply the change (the ELASTIC UPDATE)** — REPL `commit` = exactly two subroutine calls
(`Incremental.h::commit()`):
```cpp
std::vector<RamDomain> args, ret;  std::vector<bool> retErr;
prog.executeSubroutine("incremental_update_clear_diffs", args, ret, retErr); // clear last epoch's diffs
// (insert/remove the diff_plus@_/diff_minus@_ tuples for THIS epoch here)
prog.executeSubroutine("update", args, ret, retErr);                         // <-- THE incremental update
```
`"update"` is a first-class RAM subroutine the Synthesiser emits specially (it even wraps it in an
`update-time:` timer — `Synthesiser.cpp:3146-3174`). This is the elastic re-derivation: it propagates the
diffs, runs the re-discovery/retraction rules (multi-support deletes via `actual_diff_minus@_` /
`@new_diff_*`), and lands the IDB at its new fixpoint **without** a full recompute. The provenance counts
make the "deleted-but-still-derivable" case correct — the multi-support retraction the toy below exercises.

**Bootstrap vs update:** the initial full evaluation is the ordinary `runAll(...)` / `run()` (emitted in the
standalone `main` BEFORE `startIncremental`). "Bootstrap" in the paper's adaptive sense = that full run; the
incremental path = the `update` subroutine. The fork's adaptivity is internal to how the update rules fire;
from the embedding's view the two entry points are **`run()` (bootstrap/full)** vs
**`executeSubroutine("update")` (incremental)**.

## 4. Toy sanity (Phase-A end) — oracle computed, elastic side NOT runnable here

`third_party/toy_elastic_test/` holds the canonical 3-rule transitive-closure program + the recompute oracle.
Because the elastic fork didn't build, the elastic `update` could not be executed; the **full-recompute
oracle was computed with the INSTALLED 2.x souffle** (`/usr/local/bin/souffle`, `c3861e0`) to nail down the
target the elastic update must match:

```
edge = {(1,2),(2,3),(2,4)}  ->  path = {(1,2),(1,3),(1,4),(2,3),(2,4)}
delete edge(2,4)            ->  path = {(1,2),(1,3),(2,3)}
```
Deleting `edge(2,4)` retracts exactly `path(1,4)` and `path(2,4)`; `path(1,3)` **survives** on its alternate
support `edge(1,2),path(2,3)`. That is precisely the multi-support retraction the elastic `update` exists to
get right, and it is the shape of the `delta == full` byte-identity gate. The REPL transcript that would
drive the elastic engine to the same result is documented in `third_party/toy_elastic_test/README.md`
(`remove edge(2,4)` then `commit`). **Not executed — pending a working build (mac mini).**

## 5. Integration sketch — what replaces `p->run()` in `engine_inproc.mtg_run_delta`

Our shim (`engine_inproc.py`, `_SHIM_CPP`) today, in `mtg_run_delta(h, purge, facts)`:
```cpp
// purge changed input relations -> purgeInternal/Output -> insert_blob(reloaded rows) -> p->run();
```
The `p->run()` is a **full fixpoint over the patched inputs** (correct, but no incremental saving). Under the
elastic fork it would become a diff + `update` call. Two facts make this clean:

1. **The embedding API is the SAME calls we already use.** The elastic fork still exposes
   `getRelation` / `Relation::insert(tuple)` / `executeSubroutine` on the generated `SouffleProgram` (verified
   in `SouffleInterface.h`). The `-D__EMBEDDED_SOUFFLE__` path still emits *only* the `ProgramFactory`
   (`Synthesiser.cpp:3224`) — i.e. **no `main`, no REPL, no auto-bootstrap** in the `.so`. So an embedded
   caller must drive bootstrap and update **itself** (exactly what `Incremental.h::commit()` does in the
   standalone binary). `ProgramFactory::newInstance` / `SouffleProgram*` / `mtg_create` all carry over.

2. **The diff protocol is data, not a new ABI.** We already TSV-marshal rows into relations. We change *which*
   relation and append 2 columns.

### Sketch of the new shim entry (replaces the body of `mtg_run_delta`'s "...then run()"):
```cpp
// FIRST CALL (bootstrap): load full EDB into the BASE input relations + run() once (today's mtg_run).
// SUBSEQUENT CALLS (elastic update):
//   for each input relation R whose rows changed between states:
//     added  rows -> getRelation("diff_plus@_"  + R)->insert(<cols..., /*iter*/0, /*count*/ +1>)
//     removed rows -> getRelation("diff_minus@_" + R)->insert(<cols..., /*iter*/0, /*count*/ -1>)
//   p->executeSubroutine("incremental_update_clear_diffs", a, r, e);  // BEFORE inserting this epoch's diffs
//   ... (do the diff inserts) ...
//   p->executeSubroutine("update", a, r, e);                          // <-- replaces p->run()
//   serialize_outputs(p);   // read the now-updated output relations exactly as today
```
Ordering note mirrored from `commit()`: call `incremental_update_clear_diffs` to wipe the *previous* epoch's
diff rows, THEN insert this epoch's `diff_plus@_/diff_minus@_` rows, THEN `update`. (The base EDB relations
are NOT purged on the incremental path — that is the whole point; only the diff relations carry the delta.)

The Python side (`engine_inproc.evaluate`) already computes `to_purge` = relations whose row-set changed and
`new[rel] - old[rel]` is implicit — for elastic it must instead compute, per changed relation, the **added**
and **removed** row sets (a set-difference both ways) and marshal them into the two diff blobs. The existing
`_LOADED` snapshot is exactly the state needed to diff against. `MTG_NO_DELTA` / a new `MTG_NO_INCREMENTAL`
escape hatch keeps the full `mtg_run` path as the falsifiable oracle.

### Gaps / risks to validate on the mac mini (Phase B), framed against `delta == full` byte-identity:
- **Annotation columns leak into outputs.** Under `--incremental`, EVERY relation (incl. outputs) gains the
  `@iteration` / `@count` columns. `serialize_outputs` must **strip the trailing 2 columns** (and filter
  `count <= 0` tuples, which represent retracted/absent rows) to reproduce the plain 2.x output rows that
  `test_engine_native`'s oracle compares. This is the single most likely source of a byte-diff and must be
  handled in the shim's serializer. Verify the exact column semantics empirically once built.
- **Relation-name mangling.** Diff relations are literally named `diff_plus@_<rel>` / `diff_minus@_<rel>`
  (the `@` is legal in souffle-internal relation names). Confirm `getRelation` accepts those exact strings
  from the embedded interface (the REPL uses exactly these).
- **Echoed inputs / NONKEEP.** Our `_NONKEEP` set (input∩head, input∩output) interacts with how the elastic
  pipeline treats input relations that are also rule heads; re-derive the NONKEEP logic under incremental.
- **Codegen divergence.** The fork is 2019-era pre-2.0 codegen; the generated `.cpp` it emits differs from
  our current 2.x (`c3861e0`) generator. The plan's strategy (A) = port the shim to the old codegen;
  (B) = merge upstream into the elastic branch to keep modern codegen + the shim. The embedding *calls* are
  identical (good news for B); the *generated source shape* is what diverges.

## 6. Phase B feasibility verdict

- **This laptop: NO.** Cannot produce a `configure` (no autotools) and cannot get `mcpp` (not packaged, no
  root). Build never starts. Independent of memory.
- **Mac mini: YES, expected.** `brew install autoconf automake libtool mcpp bison flex`, then the autotools
  flow above with `make -j` modest. Then compile `engine_rules.dl` under `--incremental`, drive the toy +
  real-transition replay, and assert incremental == full byte-identical (Section 4 oracle generalizes).
- **The non-negotiable invariant** (`delta == full`, byte-identical, every transition) is unaffected by any
  of the above — and Section 5's gap #1 (strip/filter the `@iteration`/`@count` columns in `serialize_outputs`)
  is the concrete thing most likely to break that invariant on first integration. Keep the full-`run()` path
  as the always-available falsifiable oracle behind `MTG_NO_INCREMENTAL`.

## Appendix — exact source references (commit 540cf8d3)
- `src/Incremental.h` — REPL: `processCommand` (insert/remove/commit), `commit()` (the two `executeSubroutine`
  calls), `insertTuple`/`removeTuple` (the `+1`/`-1` annotation), `startIncremental`.
- `src/IncrementalTransformer.cpp` — appends `@iteration`/`@current_count` columns; the diff-rule rewriting.
- `src/AstTranslator.cpp` ~L253-278 — the relation-name families: `diff_minus@_`, `diff_plus@_`,
  `@new_diff_*`, `actual_diff_*`.
- `src/Synthesiser.cpp` — `--incremental` codegen; special `"update"` subroutine emission (L3146); embedded
  vs standalone `main` split (L3224, L3291 `startIncremental`); `executeSubroutine` dispatch (L3137).
- `src/main.cpp:187` — the `--incremental` (`'\6'`) CLI flag; `:340` — `which("mcpp")` runtime dep.
- `src/SouffleInterface.h` — unchanged 2.x-style embedding surface (`getRelation`/`insert`/`run`/
  `executeSubroutine`/`purge*`).
- `configure.ac:122-133` — flex/mcpp/bison build-time requirements (mcpp hard error).
