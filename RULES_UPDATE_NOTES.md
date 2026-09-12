# August 2026 rules compatibility branch

Local branch: `codex/rules-20260807`. No push or pull request.

## Rules source

`rules.txt` is the exact TXT download linked by [Wizards' rules page](https://magic.wizards.com/en/rules):
[MagicCompRules 20260819.txt](https://media.wizards.com/2026/downloads/MagicCompRules%2020260819.txt).
Its effective date is **August 7, 2026** (the previous source was April 17).
The SHA-256 is `4381ad1b39ab2c05f7d03633a20f711ed37277074d3266dcba5f38cbb527423f`.
The upstream BOM, line endings, and whitespace are preserved.

`datalog/rules_version.json` records that source, effective date, and the generated engine hash.
Wheel staging rejects a mismatch between the manifest, source text, and engine.

## Changes

- Regenerated the rules vocabulary, extracted facts, and engine from the new document.
  The splitter finds 3,162 rule/subrule entries and 739 glossary entries.
- Kept Power-up as an activated ability with its keyword modifier. Implemented its
  entry-turn mana reduction and once-per-object activation restriction in both driver
  and environment actions. Preserved Teamwork's existing bridge representation.
- Updated mana-ability classification for library movement in costs and effects.
  Milling costs are paid at activation; qualifying abilities resolve on the stack.
  Compound abilities retain their effect order and use a single activation/payment.
- Added Heal and Recruit handlers. Retained nonlethal direct and combat damage for
  healing, and clear marked damage on regeneration, zone reentry, and cleanup.
- Added the enduring-story designation, hone equipment power bonus, worthy creature
  predicate and Equip worthy restriction, and Vibranium's indestructibility and
  restriction against paying for nonartifact spells.
- Made entry-turn, Power-up usage, and marked damage part of public state and engine
  cache keys so game snapshots and search can distinguish them.
- Prevented transformation while face down; conniving with a departed source still
  draws/discards without placing counters on an absent permanent. Covered connive zero.
- Repaired Siege/non-Siege battle extraction and the renumbered spell-controller
  ordering rule. These extraction tables describe rules; they do not add a complete
  battle gameplay subsystem.
- Strengthened the build gate to reject semantic conformance failures as well as
  compilation failures, and to require Souffle. Determinism covers every rules artifact.
  Card validation disables only the optional quadratic clause-minimization pass;
  compilation and conformance still run.
- Fixed five missing relation declarations in the card-data generator. Rebuilt the
  local card facts against the refreshed keyword vocabulary.
- Prefer checkout Datalog over an old wheel staging directory during development.
  Installed wheels continue to use bundled data and their matching native engine.

## Validation

- Two complete rules generations are byte-identical. The final engine allowlist
  adjustment also passed a separate two-generation comparison.
- All 105 standalone Datalog artifacts compile and pass semantic conformance.
- 21 new rules integration tests and two negative build-gate tests pass.
- The affected regression run covers 20 scripts. Fourteen pass; the six failures
  below reproduce with exactly the same check counts on an isolated copy of the
  original commit. The counted legacy checks are 452/474; scripts using unittest or
  bare assertions are additional to that total.

| Existing failing suite | Updated branch | Original branch |
| --- | ---: | ---: |
| Colored mana | 30/37 | 30/37 |
| Fast mana | 33/37 | 33/37 |
| Face down | 32/39 | 32/39 |
| Stack | 27/28 | 27/28 |
| Targeting | 71/73 | 71/73 |
| Schema | 8/9 | 8/9 |

These are not a green full-suite result. This branch updates the package's existing
simulation model; it does not establish complete Comprehensive Rules conformance.
Broader interactions identified in the initial review, including repeated-payment
reflexive triggers, crew attribution, conditional flash, and optional casting of
multiple copies, have not been exhaustively validated.

The native universal2 library was rebuilt and loaded successfully. A fresh wheel
was installed into a clean virtual environment outside the repository. With no
compiler on PATH, it passed engine availability, game creation/play, provenance,
worthy, parsed Storied, and handler-loading checks. This installation check ran
on the current Apple Silicon Mac; the Intel slice was built but not executed.

## Local sanity check

```sh
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 python3 build.py
OMP_NUM_THREADS=1 MTG_NO_SPACY=1 python3 tests/test_rules_2026.py
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 python3 tests/test_rules_build.py
OMP_NUM_THREADS=1 MTG_NO_SPACY=1 python3 tests/test_power_up.py
OMP_NUM_THREADS=1 MTG_NO_SPACY=1 python3 tests/test_teamwork.py
OMP_NUM_THREADS=1 MTG_NO_SPACY=1 python3 tests/test_nonmana_costs.py
```

Card facts, staged package data, and native binaries remain generated local files,
as in the existing repository layout. A checkout on another machine needs its own
card corpus and card-data generation. The full source and initial findings are in
`RULES_UPDATE_REVIEW.md`. Temporary comparison, test, and packaging logs are under
`/tmp/mtg-rules-review/`; they are not part of the branch.
