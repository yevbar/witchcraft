# Handoff — resuming mtg_parser on zucc (mac mini, Fedora Asahi aarch64)

This repo was migrated from the MacBook mid-session. Context did NOT transfer; orient yourself:
1. Read README.md, then `git log --oneline -8`.
2. `python3 coverage.py`  (current total ~19.8% semantic; latest work: §709 split cards, §4 zones).
3. The project interprets rules.txt -> deterministic Soufflé Datalog via build_*.py interpreters;
   build_engine.py assembles the playable engine; driver.py is the rules-free loop.
   Verify determinism with `python3 build.py` (regenerates all datalog/*.dl twice, asserts identical).

## Environment status (set up by the migration)
- Python deps installed: spacy + en_core_web_sm, lark, ijson.
- souffle: built from source in /root/souffle (check `command -v souffle`; if MISSING the build in
  /root/mtg_setup.log failed — rebuild: cd /root/souffle && cmake --build build && cmake --install build).
- AllPrintings.json (525MB, for bridge_cards.py) was NOT transferred — gitignored. rsync it if needed.

## Where the work was
Sweeping rules.txt section by section into interpreted datalog (one build_*.py per family),
keeping everything deterministic + conformance-clean. Commit per family with the Co-Authored-By trailer.

## Deferred design: multi-state ("world column") Souffle batching
search.py looks ahead by calling driver.run once per state (~55ms each, dominated by re-parsing
the 30KB engine_rules.dl). To evaluate MANY states in one Souffle fixpoint, the pattern is a
leading `w` (world/state-id) argument on EVERY relation, load N states tagged w1..wN, read outputs
tagged by w. NOT done because it's a dramatic change to the GENERATED engine: every rule, every
aggregate `N = sum X : {…}`, negation and comparison must thread `w`, plus driver run/apply and all
determinism baselines. And it's premature — deep combo SEARCH is gated on modeling activated/mana
abilities (the thin move space), not Souffle throughput; confirming a single known 10-step loop is a
linear ~2s walk already. Cheaper interim win if ever needed: precompile the engine (`souffle -o`) to
skip per-call parsing. Batching only amortizes the per-node constant; it does NOT fix the exponential
of branching search (use the transposition table on canonical_key + bounded move space for that).

## Perpetual loop: mine rules.txt for grammar FORMULAS, interpret the cleanest, commit, repeat
Method: `python3 coverage.py` to get uncovered rules, cluster them by recurring sentence template
(opening trigrams, fixed anchor phrases), pick the highest-precision/highest-volume one, build an
abstaining interpreter (emit only on anchor match), conformance-check with souffle, register, commit.
A wrong fact is worse than no fact — abstain on nuanced/qualified cases rather than flatten them.

Biggest technique found: HEADING ROSTER. In catalogue groups every rule is a short Title-Case heading
naming the thing (§701 keyword actions, §702 keyword abilities). Sweep `r.text` where it's a heading
(len<=42, no internal '.', capitalized) -> (rule, name_slug). Slug = lowercase, spaces->'_' (matches
build_keyword_taxonomy so it joins keyword_class). This vein is now EXHAUSTED (scan: only §205 sub-
headers remain, not worth it). §702.N.1 ability-type classification ("X is a static/triggered ability")
is also ~fully covered already by build_keyword_taxonomy.

Done this session (total semantic 19.9% -> 28.4%):
- 'doesn't use the stack' formula (whole book) -> build_stack.py / stack.dl. skips_stack(rule, kind);
  kind read from same sentence (turn-based/special/state-based, else generic). 33 facts.
- §702 'Multiple instances ... are redundant' -> build_redundancy.py / redundancy.dl.
  instance_stacking(keyword, scope, result); enlist is the lone 'independent'. Abstains on qualified
  per-kind/per-quality rules (landwalk/hexproof/protection). 30 facts.
- §701 keyword-action roster (headings) -> build_keyword_action_index.py / keyword_action_index.dl. 67.
- §702 keyword-ability roster (headings) -> build_keyword_ability_index.py / keyword_ability_index.dl. 190.

Done later (total 28.4% -> 29.0%):
- §310 Battles -> build_battle.py / battle.dl. Saga-like quantitative card type: battle_defense
  (printed off-battlefield / defense-counters on it), battle_subtype(Siege), battle_property
  (enters-with-defense-counters, graveyard-at-0-defense SBA, single protector, can't-attach, ...). 11 facts.
- §712 DFC -> EXTENDED build_dfc.py: dfc_kind (nonmodal/modal/meld), dfc_active_face (crisp 712.8a/b/d/e
  which-face rules), dfc_transform (meld can't / non-meld can). Abstained the 712.8c/f/g modal/melded prose. 9 facts.
- NOTE: §103.4 variant starting life (TwoHeaded 30 / Commander 40 / Brawl 25 / Archenemy 40) was already
  covered by build_starting.starting_life() — don't redo it.

Next candidates (mined, NOT yet done — all lower-yield/scattered, need careful per-family handling):
  combat evasion keywords ("can't be blocked except by ..." menace/fear/shadow/skulk) — EVALUATED and
  ABSTAINED: the except-by clauses vary too much (flying-or-reach / artifact-or-black / two-or-more /
  share-color) to flatten safely, and several are already covered by the evasion pattern. Skip unless
  modeling each exception precisely. Remaining: "only be played as a land" (300.2a/305.9); "Activate only
  once each turn" use-limits (602.5b/603.2h/702.57b); §107 X/number defaults (107.2 "uses 0 instead",
  107.1c "any number"); §201 Name (same-name-if-shared, 201.2a); §8 multiplayer (1.2%, lowest section).

Earlier families (total 19.9% -> 20.6%):
- §202 mana cost/color  -> build_color.py / color.dl (object color source, colorless, multicolor/
  hybrid combination, mana-value treatments for {X}/hybrid/Phyrexian/no-cost).
- §708 face-down         -> build_face_down.py / face_down.dl (2/2 default characteristics + the
  plainly-stated booleans: can't-turn-face-down, controller-may-look, spells-cant-turn-up, reveal).
- §714 sagas            -> build_saga.py / saga.dl (Roman numerals I/II/III, final chapter number,
  lore-counter placement, the TBA + at-final-chapter SBA both skip the stack).

Each new build_*.py is registered in THREE places: build.py (import + regenerate() + GENERATED list)
and coverage.py (import + LARK_INTERPRETED union). `count` is a reserved Soufflé keyword — don't name
a column `count`; the project convention is `n` (typed `number` when it's a genuine count, as in
build_combat/build_ending/build_deck). The playable engine/driver consume none of these yet (like split.dl);
they're standalone interpreted artifacts. Wire into build_engine.py only when the loop needs a constant.

Next candidates (deliberately NOT forced — would be lossy/redundant, so left abstained):
  §204 color indicator (==202.2e, redundant); §205.3n/p/q planar/dungeon/battle subtype lists
  (single-item or stopword-bearing — build_enumerations._ok rejects them on purpose); §706 die (procedural).
Better fresh targets: §205.1a/205.3d type-setting & subtype-correspondence rules; §107 remaining mana
symbols; §2 §203/§206/§207 illustration/expansion/text-box layout; §3 per-card-type rule families.
