# Plan: eliminate the `interpreter` dependency inside `mtg` (Increment 7)

## STATUS: DONE — the mtg engine driver imports ZERO interpreter Python

A1 (`ground.slug` → `mtg._text.slug`), A3 (`_amount` → `mtg._text.amount`), and A2
(`_mana_production` → the `mana_source` artifact baked by `build_cards.py`) are all landed, plus the
increment-6 data decouple (`card_corpus` → `mtg._corpus`). `test_no_interpreter_import` guards it.
`mtg.analysis` (B) is exempt by design — interpreter-based analysis tools, an optional extra never imported
by the core. **Remaining = packaging only** (the "complete shim + binary" install: `importlib.resources`
for `datalog/`, bundle the souffle backend, wire the extras) — a separate effort, not a dependency issue.

## Goal / end-state

`mtg` is the **driver** — it runs the Datalog/Soufflé build the way Python drives a Stockfish
binary. The English→Datalog `interpreter` is the **compiler** that *produces* that build. They are
separate packages, and the driver must depend only on the **build artifact**
(`datalog/*.dl` + the Soufflé binary), never on `interpreter`'s Python.

Concretely, after this work:

```
pip install python-mtg            # a COMPLETE shim + binary: the engine driver + python-chess API,
                                  # the datalog/ artifact, and the compiled souffle backend.
                                  # imports ZERO interpreter Python.
pip install python-mtg[analysis]  # + the interpreter-based card/deck analysis (card_spacy, card_synergy)
pip install python-mtg[parser]    # + the interpreter itself (regenerate the datalog build from card text)
pip install python-mtg[learn]     # + torch value nets
```

## Current coupling (what `mtg` still imports from `interpreter`)

The data read (`card_corpus.load_cards`) was already severed in increment 6 (`mtg._corpus`). What
remains, split by tier:

### A. The engine DRIVER (must become pure) — `bridge_to_engine`, `engine/engine`, `game`

| Symbol | Sites | What it is | Already in the artifact? |
|---|---|---|---|
| `ground.slug(name)` | 12 (bridge/engine/game) | 1-line regex slugify of a **card name** → fact key | **YES** — `cards.dl` has `name(id: symbol, name: symbol)`, 33,150 rows (slug↔display) |
| `card_effects._mana_production(text)` | 1 (`_emit_mana_sources`) | parse `"Add {W}{W}"` → concrete colors (uses `ground.symbol_color`, reads `mana_symbols.dl`) | **YES** — `cards.dl` has `adds_mana(card, cost, produces)` with production resolved (`birds_of_paradise → any_color`) |
| `card_effects._amount(word)` | 1 (life-gain doubling tags) | `"seven"→7`, `"twice_that_amount"` tag | **YES** — `that_amount_plus`/`twice_that_amount` already appear in `cards.dl` effect facts |

**Key insight:** the interpreter *already emits* every fact the driver re-derives at runtime
(`name`, `adds_mana`, the amount tags). The driver calls `interpreter` Python only for historical
reasons — it predates reading these from the artifact. So most of the decouple is **"read the
artifact," not "rebuild."** A rebuild is needed only for a fact that turns out NOT to be emitted yet.

### B. The ANALYSIS tier — `analysis/{card_spacy,card_synergy,interaction_evaluator,deck_evaluator}`

These are interpreter-**based** tools: `card_spacy` calls `transpile`/`transpile_card`,
`card_synergy`/`interaction_evaluator` use the corpus + `ground`. They cannot be "decoupled" without
losing their function — they *are* interpreter consumers. They are not the driver.

## The plan, per symbol — "how/where"

### A1. `ground.slug` → `mtg._text.slug` — DONE (this branch, no rebuild)

Implemented as a private `mtg._text.slug` (the build's name->id contract; generic text normalization,
not card interpretation) replacing all `ground.slug` in `bridge_to_engine`/`engine/engine`/`game`,
because `game.py` builds its map over the FULL corpus (35,033) while the `name` relation covers only the
33,150 cards in the build — so the artifact-read would change coverage. `mtg._text.slug` is byte-identical
to `interpreter.ground.slug`. `engine/engine.py` and `game.py` now import zero interpreter Python. (The
pure artifact-read below stays available as a future refinement if `name` is emitted for all cards.)

> Original artifact-read sketch (deferred): `cards.dl` maps slug↔display via `name(id, name)`. The driver loads `cards.dl` anyway
(`mtg.sim.load_db()`), so build the inverse map once and look up there instead of re-slugging:

- Add `mtg._corpus.name_to_id()` (or extend `sim`) → `{display_name: slug}` built from the `name`
  relation in the loaded db. Cached on the db signature like `load_cards`.
- Replace the 12 `ground.slug(name)` sites in `bridge_to_engine`/`engine/engine`/`game` with that
  lookup. `game.py:63`'s `{slug(name): name}` map becomes a direct read of `name`.
- **Edge cases:** `slug(name.split(",")[0])` (legendary first-name) and any name *not* in `cards.dl`
  (a vanilla card with no ability facts may have no `name` row). For those, either (a) have the build
  also emit `name` rows for every corpus card incl. vanillas (tiny build-emit, one rebuild), or
  (b) keep a single private `mtg._text.slug()` — the exact 1-line transform — used ONLY for the
  derived/absent cases. `slug` is a frozen, trivial, deterministic transform; a private copy is
  low-drift, but the artifact-read is preferred where the name exists.

**Where:** `mtg/_corpus.py` (new `name_to_id`), `mtg/bridge_to_engine.py`, `mtg/engine/engine.py`,
`mtg/game.py`. **Rebuild:** none (unless we choose to emit `name` for vanillas).

### A2. `_mana_production` → `mana_source` artifact — DONE (this branch; one rebuild)

Implemented: `interpreter/build_cards.py` now bakes `mana_source(card, cost, produces, n)` (resolved
production WITH count, reusing `_mana_production` at build time); `sim.load_db` parses it;
`bridge_to_engine._mana_source_outputs` reads it (keeping the mechanical `_parse_ability_cost` + fixed/wild
mapping); the `_mana_production` import is gone. Emit-per-row is set-equivalent to the old per-line yields
downstream, and keeps Elfhame-Druid-style {G}/{G}{G} (green×1 AND ×2). cards.dl regenerated (gitignored).
**Full-corpus engine-state parity is EXACT** (35032 identical; the 1 diff is an Un-set Land both paths skip).

> Original sketch (wrong — adds_mana is lossy on count, see below):

### A2 (original sketch). `_mana_production` → read `adds_mana`

`_emit_mana_sources` re-parses each `"{cost}: Add {what}"` line with `_mana_production`. But
`adds_mana(card, cost, produces)` already carries the resolved production for rocks/dorks
(`llanowar_elves, "{T}", "green"`). **FINDING (this branch): `adds_mana` is LOSSY on the production
COUNT and so CANNOT replace `_mana_production` without a rebuild.** `_mana_production("{C}{C}")` ->
`["colorless","colorless"]` (a multiset the pool needs), but `adds_mana("sol_ring","{T}","colorless")`
stores a single descriptor — Sol Ring's *two* colorless is not recoverable. The pool model
(`source_produces`/`source_wildcard` with per-color amounts) needs the count.

So this is a genuine **build-emit + rebuild** step (your domain), specified as:

1. **Interpreter build:** emit the resolved production WITH multiplicity into `cards.dl` — either a new
   `mana_source(card, cost, produces, n)` relation (one row per distinct descriptor with its count) or
   add a count column to `adds_mana`. The values are exactly what `card_effects._mana_production(what)`
   returns, grouped+counted, paired with the activation `cost` string. Emit from wherever `adds_mana`
   is built (the mana-ability pass), reusing `_mana_production` at BUILD time so the runtime needs it no
   longer.
2. **You regenerate `cards.dl`.**
3. **Driver read-side:** `_mana_source_outputs` reads `mana_source` rows from the loaded db (count ->
   `fixed`/`wild`), keeping the mechanical `_parse_ability_cost` on the `cost` string. Land/quoted/{X}
   filtering stays. Drop `from interpreter.card_effects import _mana_production`. Ship it behind a
   fallback (use `mana_source` if the relation is present, else the current `_mana_production` path) so
   the same code works before and after the rebuild and can be verified in your loop.

**Where:** interpreter mana-ability emitter (build side); `mtg/bridge_to_engine.py`
(`_mana_source_outputs`, ~line 4093-4133, read side). **Rebuild: REQUIRED.** This is the last interpreter
import in the driver; once it lands the driver imports zero interpreter Python.

### A3. `_amount` → `mtg._text.amount` — DONE (this branch, no rebuild)

CORRECTION to the original sketch: the one driver use of `_amount` is NOT a doubling-tag read — it
converts the `enters_with_counters` amount, which `cards.dl` stores as a WORD
(`enters_with_counters("clockwork_beast","1_0","seven")`), into an int. That's generic English number
parsing, not card interpretation, so it moved to `mtg._text.amount` (byte-identical to
`interpreter._amount`). The pure-artifact form would emit `n` as an int (a rebuild). Original (wrong)
sketch follows for the record:

> The doubling tags (`twice_that_amount`, `that_amount_plus_1`) are already in the effect facts. The
driver's `_amount` call re-derives them from text; instead read the tag straight off the
`card_effect`/`ability_modifier` payload it's already iterating. If a numeric word→int is genuinely
needed at runtime for a value NOT in the facts, that is a build-emit gap (rebuild) — but the current
two uses are tag comparisons already present in `cards.dl`.

**Where:** `mtg/bridge_to_engine.py` (~line 2851-2893). **Rebuild:** none expected.

### A4. fallout — `ground.symbol_color` and the whole `interpreter` import in the driver

`symbol_color` is only reachable via `_mana_production`; once A2 lands it's gone. After A1–A3, delete
`from interpreter import ...` from `bridge_to_engine`, `engine/engine`, `game`. The driver then
imports **zero** interpreter Python. Add an import-guard test:
`assert no module under mtg/ (excluding mtg/analysis) imports interpreter`.

### B. Analysis tier → optional extra (no code rewrite)

- `mtg/__init__` is already lazy, so `import mtg` / the driver never load `mtg.analysis`.
- Declare an extra in `packages/mtg/pyproject.toml`: `analysis = ["python-mtg-interpreter @ ..."]`
  (or, pre-packaging, document that `mtg.analysis` requires the repo's `interpreter/` on the path).
- Optionally relocate `card_spacy`/`card_synergy` next to the interpreter later; not required for the
  pure-shim goal because they are never imported by the core.

## Packaging end-state (the "complete shim + binary")

1. **Ship the artifact as package data.** Move (or copy at build) `datalog/*.dl` — at least
   `engine_rules.dl` + `cards.dl` + their includes — under `packages/mtg/` and load via
   `importlib.resources` instead of the cwd-relative `Path("datalog/...")` the backends use now
   (`mtg.sim._DL`, `engine_native._RULES`, `engine_schema`, `bridge_to_engine`). This removes the
   last "run from repo root" requirement.
2. **Ship the Soufflé backend.** Bundle the compiled engine `.so`/native binary (built from
   `engine_rules.dl` via the fork) as a platform wheel artifact, with the interpreter fallback only
   as a dev convenience. `engine_native`/`engine_inproc` locate it via `importlib.resources`.
3. **Extras:** `[analysis]`, `[parser]` (the interpreter, for regenerating the build), `[learn]`
   (torch). Base install pulls none of them.
4. `oracle_corpus.json`: keep as optional package data or an `[corpus]` extra — the engine itself runs
   off `cards.dl`; `_corpus` is only needed for name metadata not in the datalog world (shrinks as
   A1 lands).

## Verification

- **Behavior is unchanged by construction:** A1–A3 swap "compute X via interpreter" for "read the same
  X from the artifact the interpreter already wrote." So the engine's outputs must be byte-identical.
- Per step: run the datalog suite (`tests/`), the mtg suite, and the inthearena suite; the
  `test_translate` parity checks are a ready oracle.
- Add the import-guard test (A4) so the decoupling can't silently regress.

## Sequencing (each step independently committable + verifiable)

1. **A1 `ground.slug` → `name` read** — no rebuild; verify name-map parity over the corpus.
2. **A3 `_amount` → baked tags** — no rebuild.
3. **A2 `_mana_production` → `adds_mana` read** — run the coverage parity diff; if clean, no rebuild;
   else emit the gap into `cards.dl` (one rebuild — your domain) and read it.
4. **A4** — drop the interpreter imports from the driver; add the import-guard test.
5. **B** — analysis as an extra.
6. **Packaging** — `importlib.resources` for `datalog/`, bundle the souffle binary, wire the extras.

Steps 1–2 (and 3 if the diff is clean) need **no `cards.dl` rebuild**. Only a coverage gap in step 3,
or emitting `name` for vanilla cards in step 1, would require you to regenerate `cards.dl`.
