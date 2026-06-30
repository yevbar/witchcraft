# Audit: which remaining card regex is INTERPRETATION (migrate to lark) vs STRUCTURAL (keep)

Investigation for the mac mini. Question: of the regex still in the card pipeline, which goes **input →
datalog fact** (interpretation — should move to the spaCy+lark approach the rules side and the bulk of the
card pipeline already use) vs which merely **plucks a substring / normalizes a token** (structural — keep)?

## TL;DR — the "24% is faithful end-state residue" claim is over-stated

`SPACY_MIGRATION_PLAN.md` frames the remaining ~24% regex as *"faithful end-state residue, NOT unfinished
work."* Measured, that's only partly true. Of the clauses still grounded by the **`card_effects.py` regex
templates** (`_TEMPLATES`, clause → `Effect(verb, amount, target)`):

- **~88% are CLEAN** — simple verb/amount/target shapes lark *could* own but doesn't yet. **These are real
  migration targets.**
- **~12% are genuinely LOSSY** abstain-tails (`_per_`, `equal_to`, `X`, `that_amount` computed amounts) where
  the regex itself is already lossy — migrating them wins no faithfulness.

So the bulk of the residual regex is **interpretation that hasn't been migrated**, not irreducible residue.

## Method & numbers (6,000-card sample, leaf-clause level)

Each effect clause is routed **lark-first** (`card_lark.parse_clause_lark`) → **regex templates**
(`card_effects.parse_clause`) → **spaCy dependency-graph fallback** (`transpile_card`). Counting which layer
grounds each clause:

| layer | clauses | share | note |
|-------|--------:|------:|------|
| lark (`card_lark`)            | 4,196 | 37.9% | the migrated bulk — the target style |
| **regex templates (`card_effects`)** | **2,242** | **20.3%** | **the non-structural regex still interpreting** |
| neither at leaf               | 4,631 | 41.8% | falls to the spaCy dep-graph fallback (already spaCy) or abstains — *separate question* |

Within the 2,242 regex-grounded clauses:

| | clauses | share of regex | |
|---|--------:|------:|---|
| **CLEAN (migratable to lark)** | 1,968 | **88%** | simple amount/target |
| LOSSY (genuine abstain-tail)   | 274   | 12%  | `_per_` / `equal_to` / `X` amounts |

Top verb families still falling to regex (migration targets, by clause count in the sample):
`draw 159 · choose 144 · create 140 · gain_life 134 · put_counter 119 · return_to_battlefield 109 ·
modify_pt 103 · exile 90 · deal_damage 86 · return_to_hand 83 · becomes 78 · pay 75 · sacrifice 69 ·
prevent_damage 53 · discard 53` — all nominally "migrated," all with residual regex-only shapes.

Representative CLEAN clauses lark should own (regex grounds them today):
- `Prevent all damage that would be dealt to <X>` → `prevent_damage/all`
- `All combat damage that would be dealt to you … is dealt to <Y> instead` → `redirect_damage`
- `Each creature you control can block an additional creature` → `grant_ability`
- `Put target Aura card from a graveyard onto the battlefield …` → `return_to_battlefield`

Representative LOSSY clauses (keep as explicit abstain — migrating is cosmetic):
- `you gain 1 life for each card in your graveyard` → `gain_life/1_per_card_in_your_graveyard`
- `you gain life equal to its toughness` → `gain_life/equal_to_its_toughness`
- `~ deals X damage to any target and you gain X life` → `deal_damage/X`

## What is STRUCTURAL (keep as regex) vs SEMANTIC (migrate)

**KEEP — structural / substring-plucking (this is the legitimate residue):**
- `card_effects.py` helpers: `_SYM_RE` (mana `{G}` symbols), `_target()` normalization, `_RET_DEST` /
  `_RET_BF` lookup tables, `_split_modifiers`, `_mana_production` (the formal mana sublanguage).
- `transpile_card.py` ability-frame layer: `_TRIG` trigger split, the `cost:effect` colon split, sentence
  splitting, P/T-static & keyword-ability & mana-ability handlers — the analogue of the rules engine's
  line-splitting. Parses the *frame*, not the natural language.
- `transpile.py` (rules side) is already spaCy(`sm`+`trf`)+lark with only 2 incidental regex — **not a
  target**; it's the gold standard to match.

**MIGRATE — semantic / clause→fact interpretation:**
- `card_effects.py` `_TEMPLATES` (the ~26-family regex leaf) for the **CLEAN 88%**. These map a whole English
  clause to `Effect(verb, amount, target)` — exactly the interpretation lark is meant to own.

## Recommended approach (reuse the proven pipeline)

1. **Per verb family, oracle-gate with `migrate_check.py`** — the same gate the 26 migrated families used:
   flip a clause shape to lark only when `parse_clause_lark` reproduces the regex `Effect` **identically**
   (0 diff) over the corpus. The CLEAN shapes should flip cleanly; that's the whole point of being "clean."
2. **Explicitly mark the LOSSY 12% as abstain** (a lark rule that grounds the verb + records the
   computed-amount span as a `cond`/`extra`, rather than a regex emitting a lossy `_per_`/`equal_to` amount).
   This matches the doc's own observation that lark often beats the lossy regex leaf by demoting the wrapper
   to `cond`/`extra`. No faithfulness is lost; the regex template is then retired.
3. **Retire each migrated template** from `card_effects.py` once lark owns it (the file should shrink toward
   only the structural helpers above) — and update `SPACY_MIGRATION_PLAN.md`'s "76/24" headline as it moves.

## Honest caveats (don't over-read the 88%)

- "Clean" here = the regex produced a non-computed amount. It is an **upper bound** on what's trivially
  migratable: a clean-amount clause may still have a structural reason lark abstains (target ambiguity,
  overlap with another family). The real migratable count per family comes from running `migrate_check.py`,
  not this heuristic.
- The 41.8% "neither at leaf" is **not** uninterpreted — it reaches `transpile_card`'s spaCy dependency-graph
  fallback. Whether that layer should also fold into lark is a separate question this audit didn't measure.
- This is a 6k-card sample (deterministic order), not the full 34k; family proportions should hold but exact
  counts will scale.

## Repro

```python
# the per-layer split + clean/lossy classification used above
python3 - <<'EOF'
import card_corpus, ground, re
from transpile_card import _sentences, _TRIG, _split_modifiers, transpile_unit
import card_lark, card_effects
# ... (route each clause lark-first then card_effects.parse_clause; bucket the regex hits
#      by whether the amount is computed (_per_/equal_to/X) = lossy, else clean) ...
EOF
```

---

## Review validation (ran the oracle gate the audit recommended but hadn't)

The audit's structural claims **reproduce** (5,792-clause sample): lark ~36% (vs 37.9%), regex templates
~20–27%, neither ~37%; within regex ~84% clean / 15% lossy (vs 88/12). Methodology is sound. The core thesis
holds: the residual regex is overwhelmingly **unmigrated interpretation**, not "faithful end-state residue" —
the `SPACY_MIGRATION_PLAN.md` "24% residue" framing IS over-stated.

CRUCIAL ROUTING FACT the headline math glosses over: production is **lark-FIRST**
(`card_effects.py:2126` — `parse_clause = _lark_leaf(s) or parse_effect(s)`). So a clause lark can ground is
ALREADY served by lark; the regex leaf is only reached when **lark abstains**. That means the audit's "regex
still interpreting 20.3%" bucket = exactly the clauses **lark abstains on** — the genuine migration TODO — and
the "88% clean" is a property of THAT bucket, not of all regex-capable clauses.

`migrate_check.py` over the 6 biggest families (create / deal_damage / draw / gain_life / modify_pt /
put_counter), comparing the regex LEAF (`parse_effect`) to `parse_clause_lark` on every clause the regex leaf
grounds (**4,854**):

| | clauses | share | meaning |
|---|--------:|------:|---|
| lark **IDENTICAL** (0-diff) | 3,460 | **71%** | lark ALREADY serves these in production (lark-first) — already migrated |
| lark **DIFFERS** | **0** | **0%** | lark never produces a WRONG tuple |
| lark **ABSTAINS** | 1,394 | 29% | the genuine regex-served remainder (the TODO) — ~84–88% clean (fillable grammar), ~12% lossy |
| lark-only (regex missed) | 1,646 | — | net-new: lark already grounds anthems/`add_mana`/`doesnt_untap`/grants the regex doesn't |

1. **Re-frame the "88%": it's not "88% of regex is migratable now."** 71% of regex-capable clauses are *already*
   lark-served (lark-first); only the 29% lark-abstains actually reach the regex templates. The audit's 88%-clean
   correctly describes THAT abstain remainder (my layer-split repro: regex-served ≈ 84% clean / 15% lossy) — so
   the real work is **closing those ~1,394 lark grammar gaps** (≈88% are clean amounts → fillable; ≈12% lossy →
   keep as explicit abstains). It's "fill grammar to retire templates," not "flip an already-clean switch."
2. **`DIFFERS = 0` is the decisive safety property the audit understates.** Across all 4,854 clauses lark NEVER
   disagrees with the regex tuple — it only matches or abstains. So growing the grammar to absorb the abstains is
   **byte-identical by the oracle, zero faithfulness risk**, and is a **code-hygiene refactor** (the grounded
   facts don't move — only where they're produced), NOT a coverage change.
3. **Template removal is per-verb and gated on `ABSTAINS=0`** (lark answers first, so a template is dead code
   only once lark grounds *every* shape of its verb). The 71%/29% split means most families are partway: lark
   owns the bulk, the templates persist solely for the abstain tail.

Bottom line: audit's **thesis is sound** (residual regex is unmigrated interpretation, "24% residue" is
over-stated) and its `migrate_check`-gated approach is right and provably safe (DIFFERS=0). The correction is
**framing**: the remaining work is filling the ~29% lark-abstain grammar gaps per family (then deleting the
now-dead templates), not flipping 88% of clean clauses — and it's faithfulness-neutral, not a coverage win.
