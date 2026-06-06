# Progress / loop state — mtg_parser rules.txt → Datalog interpretation

**Read this first each loop.** Then `git log --oneline -10` and `python3 coverage.py`.

## Current coverage (primary semantic body)
**94.1%** — 2663 / 2830 interpretable rules. ~167 primary misses remain.
Tier-0 non-semantic units now excluded from the denominator via structural_kind():
  superseded ("Previously, …" historical), list_intro ("There are several ways to …"),
  physical_note (pile-keeping / paper notes / card-illustration orientation).
MODEL TEST: en_core_web_md does NOT fix NP-head mis-roots (same CNN parser as sm, only
  bigger vectors) — net noise. en_core_web_lg is the same architecture, so also won't help.
  Only en_core_web_trf (transformer parser) would; still needs approval (torch, re-validation).
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
