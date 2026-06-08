# Plan: migrate the card interpreter from regex to the spaCy/lark (rules) pipeline

## STATUS LOG (lark-primary migration, faithful-replacement / Option A)

The card leaf is now **lark-first** (`card_effects.parse_clause`: `_lark_leaf(s) or parse_effect(s)`).
Each family below was flipped only after the regex-leaf oracle (`migrate_check.py`) showed lark is
faithful-or-better (identical tuples, or differences that are strict improvements, **0 lossy**), with
souffle conformance=0, 0 isolation collisions, coverage held, and end-to-end spot-checks clean. The
production wrapper chain (`_MAY`/`_IF`/`_UNLESS`/`_UNTIL`/`for each`) still runs ABOVE the leaf, so
lark grounds the clean residue and the wrappers become `cond`/`extra` — which is why lark often beats
the bare regex leaf (e.g. `each player may draw a card` → `draw/each_player` + `cond=may`, vs the regex
leaf's lossy `target=each_player_may`).

- **Object verbs** (destroy/exile/tap/untap/sacrifice/counter/regenerate/goad/detain) — lark-primary.
  Grammar `oclause`; abstains on the structured `exile top N of library`, suspend-style `with N
  counters on it`, and coordinated `or/and …` leads (defer to regex convention).
- **RETURN** (return_to_hand/return_to_battlefield/put_on_top/put_in_graveyard) — lark-primary.
  CONSISTENT from/to split: object stops at from/to, source→`extra=from_<zone>`, dest→verb. Fixes the
  regex's inconsistent gluing (`target_creature_card_from_your_graveyard`) and outright garbage
  (`target=from_your_graveyard`). Oracle: 241 identical + 120 differ (ALL improvements) + 0 lossy.
  Abstains on comma multi-object lists and object-internal `to` (`attached to it`, `equal to X`).
- **Player-count verbs** (draw/mill/scry/surveil/gain_life/lose_life/discard) — lark-primary.
  Grammar `pclause`: subject is a PLAYER (closed allow-list `_PLAYER`, defaults `you`), the NP is the
  AMOUNT, and the object word disambiguates the verb (gain/lose need `life`; draw/mill/discard need
  `card[s]`; scry/surveil take a bare number). Handles `up to N`/`any number of`/`at random` for
  discard. Oracle: 280 identical + **0 differ**; the cases the bare regex leaf grounds lossily
  (`each_player_may`, `each_player_who_controls_…`) lark correctly abstains on, and the wrapper chain
  feeds it clean residue in production. Net +~5 cards.

Next families (per the staged plan below): player-target object verbs (subject-first destroy/sacrifice),
P/T grants (`modify_pt`), then the wrapper chain itself (move `_MAY`/`_IF`/`_UNLESS` onto the parse).

## 0. Why, and the honest target shape

The card side (`card_effects.py` 183 `@_t(regex)` templates + `transpile_card.py` 51 unit
handlers) grounds **~99.4%** of card units by regex; the spaCy `_spacy_effect` fallback grounds
**~0.6%**. The rules side (`transpile.py`, 1,548 LOC) is the *real* spaCy+lark transpiler the card
side should have been built on:

```
preprocess() masks formal fragments ({W/U}, §refs, P/T, numbers) -> placeholders
  -> spaCy en_core_web_sm dependency parse (en_core_web_trf fallback)
  -> _retag_* fix spaCy mis-tags (game nouns, root verbs, "you")
  -> ordered _PATTERNS dependency-walkers (_imperative/_action/_effect/_keyword_action/…) emit datalog
  -> lark sublanguage grammar (_VALUE_GRAMMAR) evaluates masked comparison phrases
```

**The honest target is NOT "delete all regex".** Two kinds of text are mixed in a card clause:

1. **Natural-language structure** — verb, subject, object, prepositional roles, conditions,
   conjunctions. *This is what spaCy/lark is for*, and where regex is brittle (word order, riders,
   nesting). This is the bulk of the 183 templates and should migrate to dependency patterns.
2. **Formal/regular sublanguages** — mana symbols `{2}{G}`, P/T `+2/+2`, counters, `{X}` amounts,
   the `cost:` / trigger ability skeleton. These are *regular languages*; regex/lark is the correct
   tool and the rules engine already treats them this way via `preprocess()` masking + the lark
   `_VALUE_GRAMMAR`. We keep these as **lark sublanguages**, not ad-hoc regex.

So "replace regex with spaCy" really means: **adopt the rules engine's architecture** — mask the
formal fragments, let spaCy own the clause structure, and confine the remaining regex to a small set
of named lark/regex *sublanguage* parsers for the masked tokens. End state: ~20-30 sublanguage rules
instead of 183 surface templates, with the grammatical work done on the dependency graph.

## 1. The hard lessons (already paid for — bake them into the design)

From the `_spacy_effect` experiment (committed, gated):
- A **naive** dependency fallback added +234 cards of which **~208 were WRONG facts**: negated
  "players can't untap…" read as an untap effect; a verb lifted out of a *subordinate* clause while
  the rest of the sentence was silently dropped; activation **costs** emitted as effects.
- spaCy is trustworthy for **structure**, never for **verb choice** (gain→gain_life vs gain_control;
  put→put_counter vs put-into-zone). Verb selection must go through a grounding whitelist + slot
  disambiguation, never `root.lemma_` alone.
- Card text is **imperative** ("Destroy target creature"); `en_core_web_sm` frequently mis-roots
  imperatives as nouns. The rules engine already has `_retag_root_verb`/`_retag_game_nouns`; the card
  port needs an **imperative-mood retag pass** tuned to card phrasing.

**Design invariants** (non-negotiable, enforced by tests):
- Faithful-or-abstain (prime directive) — a wrong fact is worse than no fact.
- Every emitted verb grounds in `ground.effect_verbs()`; ambiguous verbs disambiguate by slot or abstain.
- All-or-nothing per clause — never emit a partial effect that drops conjuncts/conditions.

## 2. Regression oracle: the existing regex IS the spec

The 183 regex templates already produce a **faithful, souffle-clean** IR for 28,020 cards. That is the
**golden oracle** for the migration: a dependency pattern is only allowed to replace a regex template
when it produces the **same `Effect(verb, amount, target, extra, cond)` tuple** on every card the regex
currently grounds (plus, ideally, extra cards the regex missed). This makes the migration *measurable
and safe* — no faithfulness regressions by construction.

Concrete harness (`migrate_check.py`, to build): for a verb family, run both engines over the whole
corpus, diff the tuples, and require `dep ⊇ regex` (dep grounds everything regex did, identically) +
report the net-new cards dep adds and any disagreements (which are bugs to fix before flipping).

## 3. Target architecture

New module `card_dep.py` (mirrors `transpile.py`'s shape, reuses its helpers):

```
parse_clause_dep(clause) -> Effect | None
  text = preprocess(clause)            # REUSE rules masking: {2}{G}, +2/+2, {X}, numbers -> placeholders
  doc  = _NLP(text); _retag_card(doc)  # REUSE _retag_* + new imperative retag
  for fn in _CARD_PATTERNS:            # ordered dependency-walkers, first faithful hit wins
      e = fn(doc, legend)              # each returns a fully-sloted Effect or None
      if e: return e
  return None
```

- **Reuse from `transpile.py`**: `preprocess`, `_NLP`/`_TRF`, `_root`, `_child`, `_clean`, `_zone`,
  `_masked`, the retag passes, and the `Out`/dispatch idiom. Import them; do not fork.
- **New card slot-extractors** (the precision regex gave us, now read off the graph + legend):
  - `amount(verb_tok, legend)` — `nummod`/`{X}` placeholder/`"that many"` → amount slot.
  - `target(obj_tok)` — the dobj **subtree** (qualifiers: "target creature *you control with flying*")
    → faithful slug, compound-guarded (reject if it spans a `conj` to another predicate).
  - `zone(verb_tok)` — `to/into/onto <pobj>` → return_to_hand/battlefield/library/graveyard.
  - `duration(verb_tok)` — `until <pobj>` / `this turn` → cond.
  - `condition(root)` — leading `advcl`/`mark` ("if/unless/as long as/when") → cond, recursively.
  - `subject(root)` — `nsubj`, with the player-grammar (you/each player/target player/that player).
- **Formal sublanguages stay as lark** (extracted from the current regex, centralized):
  - mana `{…}` cost/production, P/T `±n/±n`, counters, `{X}`-amounts — one lark grammar each, fed the
    masked placeholders (exactly the `_VALUE_GRAMMAR` pattern).

## 4. Staged rollout (each stage ships independently, oracle-gated)

**Stage 0 — infrastructure (no behavior change).**
- Build `migrate_check.py` (the oracle diff harness).
- Build `card_dep.py` skeleton: `preprocess`+parse+retag+dispatch, importing rules helpers.
- Add an **imperative retag** pass + a card-tuned `_retag_game_nouns` (card nouns: "creature",
  "permanent", "counter", token types). Validate the parse roots correctly on a 500-clause sample.
- Wire `card_dep` as a **shadow** path: `transpile_card` still uses regex to PRODUCE facts, but also
  runs `card_dep` and logs tuple agreement/disagreement. Zero production risk; pure measurement.

**Stage 1 — pilot: zone-move verbs (return / put-onto-battlefield / exile-to-zone).** ~15 regex
templates. These are where the graph most clearly beats regex (word order: "Return to the battlefield
X"). Implement the `return`/`put`/`exile` dependency patterns + the `zone()` extractor. Gate: dep ⊇
regex on every zone-move card, identical tuples, + net-new cards. Flip these templates off; delete them.

**Stage 2 — simple object verbs (destroy / tap / untap / sacrifice / counter / regenerate / goad /
detain / mill / scry / discard).** ~30 templates. Pattern: ROOT verb (grounded), dobj subtree → target,
nummod → amount, no negation/cost. Reuse the `_spacy_effect` gates as the baseline; extend slots.

**Stage 3 — player-scoped verbs (draw / gain_life / lose_life / put_counter / create / choose).**
These need the **subject/player grammar** and the amount sublanguage; verb disambiguation matters
(put → put_counter only when object is "counter"; gain → gain_life vs gain_control by object). ~40
templates. Build `subject()` + the counter/amount sublanguage here.

**Stage 4 — modifiers & wrappers (the conditions, durations, conjunctions).** Migrate the wrapper
chain (`_MAY`/`_IF_COND`/`_UNLESS`/`_UNTIL`/`for each`/`When …,`) from regex to dependency structure
(`advcl`, `mark`, `cc`/`conj`, `prep "until"`). This is where the graph is *most* valuable — nesting
and ordering that regex handles with fragile `_combine` recursion become tree walks. The
`_distribute_subjects`, `_multi_damage`, `_comma_resplit` helpers become natural `conj` traversals.

**Stage 5 — statics & P/T grants (modify_pt / grant_keyword / becomes / cant / static_grant).** Needs
the P/T sublanguage + keyword grounding. Largest family; do last when the slot-extractors are mature.

**Stage 6 — ability skeleton.** Keep `cost:` / trigger splitting (`_TRIG`, activated/triggered) as
**structural regex** — it parses the formal ability *frame*, not natural language, so it stays (this is
analogous to the rules engine's line-splitting). The *bodies* are already migrated above.

After each stage: rebuild, souffle (conformance=0), run the oracle diff, commit. The regex templates
for a migrated family are deleted only after dep ⊇ regex is proven corpus-wide.

## 5. Where regex legitimately remains (the end state)

- **Formal sublanguages** as lark grammars: mana, P/T, counters, `{X}`-amounts (~5 grammars).
- **Ability-frame splitting**: `cost: effect`, trigger prefixes, mode bullets — formal structure.
- **A handful of idiom templates** spaCy can't parse (un-set/Alchemy lines we abstain on anyway).

Net: from **183 surface templates → ~25-30 dependency patterns + ~5 lark sublanguages + ~10 frame
regexes**, with grammatical work on the graph. This is the rules engine's shape.

## 6. Validation, gates, risks

- **Gate per stage**: oracle diff (dep ⊇ regex, identical tuples) + souffle conformance=0 + 0
  collisions + spot-check of net-new cards. No stage flips production until green.
- **trf fallback**: enable `en_core_web_trf` for clauses `sm` can't root (already wired in transpile).
- **Risk — coverage dip**: a migrated family might temporarily ground *fewer* cards than regex if the
  parser mis-handles a shape. Mitigation: keep both engines, dep-first **with regex as the fallback**
  during migration; only delete a template once dep strictly dominates it.
- **Risk — speed**: spaCy on 34k cards × N clauses is minutes, not seconds. Mitigation: cache parses;
  the build already tolerates ~minutes. trf only on `sm` misses.
- **Risk — faithfulness**: the experiment proved the danger. Mitigation: the oracle harness + the
  design invariants make wrong facts a *failing test*, not a silent regression.

## 7. Effort & sequencing recommendation

- Stage 0 (infra + shadow harness): foundational; do first, it de-risks everything.
- Stages 1-2 (zone-move + object verbs): the clearest wins, prove the approach, ~60 templates retired.
- Stages 3-5: the bulk; gated, incremental.
- Stage 6: minimal (frame stays).

**Recommendation:** pursue this as a *refactor toward the rules architecture*, not a rewrite — the
regex output is the spec and the safety net the whole way. Realistically the end state is the **hybrid**
the rules engine itself is (spaCy structure + lark sublanguages), which is the faithful reading of "build
off the spacy/lark pipeline". A naive "all-spaCy" port would *reduce* faithfulness; the staged,
oracle-gated refactor *increases* robustness (word-order, nesting) while holding the prime directive.
