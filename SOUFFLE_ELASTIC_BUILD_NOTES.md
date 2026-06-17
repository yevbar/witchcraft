# Soufflé-elastic — Phase-A build/API/integration findings

Executes Phase A of `SOUFFLE_ELASTIC_PLAN.md` (de-risk the elastic fork in parallel). Target:
`davidwzhao/souffle` @ branch `incremental-with-provenance-eager-diffs` — the PPDP'21 "Towards Elastic
Incrementalization for Datalog" implementation.

**Go/No-go headline:** the fork is **vendored and fully analyzed**; the **incremental insert/delete/commit
API is precisely pinned**; the **`engine_inproc` integration sketch is concrete**. But the fork **did NOT
build on this box** — blocked at the *toolchain* layer (no GNU autotools, no `mcpp`, no root), not by memory.
**Phase B is NOT feasible on this laptop; it needs the mac mini (or a box where autoconf/automake/libtool/mcpp
are installable).** Memory was never the gate — the build never reached a compile.

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
