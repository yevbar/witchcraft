# Progress / loop state — mtg_parser rules.txt → Datalog interpretation

**Read this first each loop.** Then `git log --oneline -10` and `python3 coverage.py`.

## Current coverage (primary semantic body)
**98.0%** — 2764 / 2820 interpretable rules. ~56 primary misses remain.
More Tier-0 false positives excluded via structural_kind: non_gameplay (rules that SAY they have "no
effect on game play" — expansion symbol, set/type icons, flavor/decorative text, DFC hint bars; carve-out
for "marker"/"other than" so art-stickers stay), advisory ("The most commonly chosen …"), and "There are
different/several kinds of X." intros (numeric enumerations like "six types of mana:" are NOT excluded —
they're real, handled by build_enumerations). NOTE: these only fix the coverage % accounting; the
transpile-based builders still emit a (harmless, spurious) print fact for the previously-"covered" ones —
a future cleanup could have those builders skip structural_kind rules.
build_card_terms: glossary "Some cards/effects refer to '<term>'…" rules now CAPTURED as
card_text_term(term) — they define a card-text term (descended, crime, warped, playing, coin_flip),
so crediting the term gives an accurate remaining-to-tackle count (cf. how structural_kind discounts
headers). Existential "Some X are Y" CLAIMS are NOT touched — those stay real misses.
More tangled negations + complex conditionals reified (extraction was the blocker, NOT Souffle
expressiveness — Souffle has stratified negation/aggregates/rich relations). New relations:
requires / follows_rules (conditionals_extra), only_characteristics / color_cardinality
(restrictions_extra); more happens_when (ETB→new object, on-battlefield→continuous applies,
specifies-targets→check-legal, planar-die→trigger, blocked/unblocked, sticker setup) and
distinct_action (regen-shield ≠ regenerate).
More residual negations reified (build_restrictions_extra): is_not / not_part_of / only_means added
(spells aren't mana abilities, additional cost not part of mana cost, only ways to destroy, only
activated abilities can be activated, sideboard cards outside the game). Complex conditionals
captured (build_conditionals_extra): happens_when / distinct_action / ordered_resolution (SBA-check
on priority, copiable-revert on flip, daybound day-flip, phasing of attached permanents, transform≠
turn-face-up, controller-goes-first).
Negation/restriction + multiplayer enrichment: build_restrictions_extra reifies negatively-phrased
rules as positive tuples — cannot(subject,action,SCOPE) / does_not(...) / at_most_one(thing); the
SCOPE field is what keeps them faithful (priority denied only during the turn-ending process, events
only between steps — without scope they'd over-claim). build_multiplayer extended (+3: defending
players, APNAP-modified-by-shared-team-turns, archenemy-free-for-all).
TRF INTEGRATED: transpile_rule now uses en_core_web_sm first and falls back to en_core_web_trf
(transformer parser) ONLY when sm yields no fact — recovers the NP-head mis-roots. Strictly additive
(every sm fact unchanged; +160 new facts, of which ~46 net primary after a quality guard). trf is now
a PIPELINE DEPENDENCY (install: pip install spacy-transformers && python -m spacy download
en_core_web_trf; ~917MB torch — a +cpu wheel is lighter). If trf is absent the pipeline degrades to
sm-only (lower coverage) — so reproducible builds require trf installed.
Quality guards added with trf (caught wrong facts trf surfaced from quoted ability text / idioms):
  _action abstains on relation-verb + no object ("X means/refers -" — removed 23 lossy sm facts too);
  _isa/_not_isa abstain on a DEMONSTRATIVE subject ("THIS permanent is/ isn't a Y" — a specific ref,
  not the class, e.g. hexproof's quoted text); _is_property "short" idiom blocklisted.
Tier 1/4/2-3 enrichment also landed: build_sba_extra, build_multiplayer, build_card_misc (descriptive).
Tier-0 non-semantic units now excluded from the denominator via structural_kind():
  superseded ("Previously, …" historical), list_intro ("There are several ways to …"),
  physical_note (pile-keeping / paper notes / card-illustration orientation).
MODEL TEST: en_core_web_md does NOT fix NP-head mis-roots (same CNN parser as sm, only
  bigger vectors). en_core_web_lg is the same architecture.
TRF PROVEN (installed + tested on this aarch64 box): en_core_web_trf FIXES the NP-head mis-roots
  (106.12a/122.7/603.6a now root on the main VERB). Loads 2.8s, ~0.06s/parse, DETERMINISTIC across
  parses. Bounded proof: as a MISS-ONLY fallback it newly parses 89 of the 152 misses -> ~97.8%.
  Deps installed: torch 2.12 (917MB; pulled unused CUDA cu13 libs — a +cpu wheel is ~10x lighter),
  spacy-transformers, en_core_web_trf.
INTEGRATION DECISION PENDING (user): trf as a miss-only fallback in transpile_rule (sm primary,
  trf retried only when sm yields no fact — so existing 2678 facts are untouched). Tradeoffs:
  (a) trf becomes a pipeline dependency (like sm/lark already are); (b) the 89 new facts need
  per-fact faithfulness vetting (a few are marginal: 700.15 is_property(term,short),
  702.22b isa(band,banding)); (c) within-machine determinism holds (gate passes), but transformer
  float matmuls may not be byte-identical ACROSS machines/torch versions — weaker cross-machine
  reproducibility than the current CNN pipeline. Do NOT swap unilaterally; needs go-ahead.
Per-section floor: §4 Zones 88.5%, §6 Spells/Abilities 91.7%, §5 Turn 91.8%.
Last clean cluster captured: build_card_props (attached_controller_independent, subtype_single_word).
A full lead-phrase + grammatical-bucket re-scan after this found NO further clean clusters.

## IN PROGRESS: enriching tiers 1-4 so meaningful uncovered rules aren't lost info
The user cares about non-1v1 formats too. Capturing meaningful misses as descriptive facts via
content-driven builders (parse-independent), tier by tier:
  Tier 1 SBAs (704.5r/s/v, 704.6f, 702.2b): counter-cap, Saga final chapter, battle defense 0,
    phenomenon planeswalk, deathtouch. Tier 2 keyword tail. Tier 3 special-card layouts.
    Tier 4 multiplayer/variant. Emit (rule, subject, condition, outcome)-style facts; the executable
    engine side (build_sba) stays transpile-only.
The ~167 STILL-uncovered after enrichment are irreducible in en_core_web_sm: NP-head mis-roots
(buried verb), pronoun/quantifier subjects, negations, disjunctive predicates, vague sentences.
DO NOT inflate % with lossy facts. Only further parser lever (needs approval): en_core_web_trf.

## Prime directive (never violate)
**A wrong fact is worse than no fact.** Abstain rather than emit a lossy/over-claimed fact.
100% is NOT truthfully reachable: a large share of misses are spaCy mis-parses (NP-head roots on
long sentences where the main verb is buried) or sentences that genuinely must abstain (negations,
"some …", "there are several ways to win"). Do not inflate the % by emitting wrong facts.

## Methodology (every change)
1. Two kinds of work: (a) content-driven `build_*.py` builders for recurring topic frames;
   (b) general grammatical refinements to `transpile.py` shared patterns (_isa, _is_property, _action…).
2. For shared-pattern edits ALWAYS diff: dump all `transpile_rule` facts before/after to /tmp, inspect
   every ADDED/REMOVED/CHANGED. Keep only faithful additions + improvements; zero unexplained regressions.
3. New builders: register in `build.py` (import + main call) AND `coverage.py` LARK_INTERPRETED.
   Souffle field names can't be reserved words (`count`, `min`, `max`, `sum`) — use `n`/`players`.
4. Gate before every commit: `python3 build.py` (byte-identical determinism + every artifact compiles +
   conformance_fail=0). Commit with the `Co-Authored-By: Claude Opus 4.8 (1M context)` trailer.
5. Update THIS file's coverage line + "Done so far" each loop.

## Done so far (this campaign)
Builders: enumerations restatement-credit, §8/§9 variants (player_count/command_zone/roi_exempt),
§702 daybound temporal triggers, §5 combat templates + combat_phase, §1 ability function zones,
§7 card-layout (alt-characteristics + residual: decompose/half-unlock/transform), §613-616 replacement
(+ templates), §602/603 ability_class, §105/§103 concept_defs.
Grammar: _isa negation-scoping + "only"-skip + partitive "one of the Y"; _is_property modal-coordination
+ universal-quantifier (each/every/all) + "subject"/"due" idiom blocklist; _ASIDE "see rule" stripping;
_action partitive-subject recovery ("most of the area …").

## What's left — by grammatical bucket (the high-leverage lens)
- `NOUN:V []` (~36) — NP-head mis-roots, long subject NPs, main verb buried. MOSTLY IRREDUCIBLE.
- `AUX:be [nsubj+attr]` (~26) — copular defs; remaining are disjunctive predicates (UNSAFE, over-claim
  subset) or bare-generic-head (ISA_BAD_PRED, deliberately weak). Little safe left.
- `VERB:V [dobj]/[nsubj+dobj]` (~28) — SVO; mostly mis-parses (see-ref roots, relcl-roots, NP-head).
- `AUX:be [nsubj+acomp]` (~8) — enumerations (members not properties) or complex. Little safe left.

## Next ideas to try (diminishing returns — verify yield before investing)
- Copular property-with-complement: "X is exempt FROM Y" / "independent OF Y" → property(subj, adj, obj).
- "X is short for Y" / term abbreviations (700.15).
- Topic builders for any small clean clusters still found by per-group scan (§4 zones defs, §724 ending).
- A general "main verb buried under noun-root" retag IF a safe heuristic exists (high risk).

## Honest ceiling estimate: ~94.5%. Past that needs a stronger dependency parser or genuine abstention.
