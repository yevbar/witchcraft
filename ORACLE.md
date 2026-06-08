# Card-oracle interpretation → grounded Datalog → C++ engine

Companion campaign to the rules.txt interpretation (now **100.0%**, see HANDOFF.md). Goal: interpret
every Magic card's oracle text into **grounded** Datalog facts, then generate a C++ program that
contains the entirety of Magic. Inspired by Forge's card-script DSL, but the IR is the SAME Datalog
fact model the rules pipeline produces — so cards and rules share one semantic substrate.

## Prime directive (cards) — grounding
**Every card fact must ground in a rules-defined term.** rules.txt is 100% interpreted, so the
definitions already exist as datalog facts; `ground.py` reads them back and the interpreter REFUSES to
emit anything it can't ground:
- a keyword grounds in the §702 roster (`keyword_ability_index`), an action in §701
  (`keyword_action_index`), a color/mana symbol in §105/§107.4 (`symbol_color`).
- if a card says a word the rules don't define, we **abstain** — never invent a fact.
This inherits the rules directive: *a wrong fact is worse than no fact.*

## One shared pipeline — rules + cards, no duplication, cards can't mutate rules
The card side REUSES the rules engine, it doesn't fork it:
- `ground.py` (grounding vocab) and `transpile.py` (the spaCy/lark engine: masking, retags, the
  _imperative/_action/_effect patterns) are shared. `card_spacy.py` is a thin bridge that DELEGATES to
  `transpile.transpile_rule` — it never reimplements parsing.
- card-SPECIFIC code (`card_effects.py` templates, `transpile_card.py` structure) covers only what the
  rules engine doesn't model: mana symbols, P/T, the cost:/trigger ability skeleton, precise
  amount/target slots the executable IR needs.
- **Isolation:** every card relation is `card_*` (card_effect/card_ability/…); NONE collides with a
  rules relation (validate.py check: 23 card vs 760 rules, 0 collisions). So a card fact can never land
  in `effect`/`ability` that the rules engine reasons over — interpreting cards cannot change the game's
  rules. Rules-changing cards (max hand size, extra lands, "can't gain life") are `card_static_player`/
  `card_static`, which the engine applies ONLY when the card is in play; the rules datalog never reads them.

**`validate.py`** is the single validator for both: (1) ISOLATION — no card/rules relation collision;
(2) SOUNDNESS — cards.dl compiles + conformance=0 (rules side: build.py determinism gate); (3)
FAITHFULNESS — every card effect clause is re-parsed by the RULES spaCy engine and the verb cross-checked
(agreement = independent confirmation; conflicts, mostly the engine's own imperative mis-parses, are the
audit list).

## Pipeline (mirrors the rules side)
| rules side | card side | role |
|---|---|---|
| `rules_parser.split` | `card_corpus.units_of` | decompose text into atomic UNITS (rule→subrule ≈ card→ability line) |
| `transpile.transpile_rule` | `transpile_card.transpile_unit` | a unit → grounded facts, or None (abstain). Pattern registry, first hit wins |
| `coverage.py` | `card_coverage.py` | honest coverage; abstentions stay uncovered |
| `build_*.py` → `datalog/*.dl` | `build_cards.py` → `datalog/cards.dl` | emit grounded facts (souffle-checked) |
| (rules.txt, committed) | `build_oracle_corpus.py` (MTGJSON, NOT committed) | source corpus |

`ground.py` is the new shared layer: the grounding vocabulary loaded from the generated rules datalog.

## Coverage unit & status
Two honest denominators (`card_coverage.py`): **template** (unique normalized ability lines — "how
much of Magic's ability vocabulary") and **instance** (weighted by printings — "how much real card
text"). Normalization: strip reminder text, self-ref→`~`, symbols→`{S}`, ints→`N` (so one ability on
2000 cards is one template).

Corpus: 34,128 unique cards, 33,771 with oracle text → 62,860 ability-unit instances, ~35,000 unique
templates (a long tail, bigger than rules).

**HEADLINE METRIC — CARDS FULLY INGESTED (every oracle line parses, no partial credit): 73.5%**
(25,077 / 34,128).

### Latest batch (cross-cutting families)
Climbed 56.1% → 59.0% per-card by broadening the static-anthem SUBJECT grammar (`_SUBJ`: multi-word
adjective chains + trailing set-qualifiers "of the chosen type / with flying / that are enchanted",
covering "Other green creatures you control", "Creatures you control of the chosen type", etc.); adding
"they"/possessive-controller targets and has/have keyword grants to the effect engine; the §701.12
**fight**, §500.7 **extra turn**, §400.7 **zone-move** (put into hand/graveyard, compound-split),
reveal-from-among-them, skip-step, and §700.2 choose-from templates; a **Prototype** (§702.160)
keyword handler; "during your turn" static P/T; and a faithful last-resort `_static_effect` fallback
for bare grounded static lines (gated to REFUSE replacement effects "…would…instead", die/level table
rows, and conditionals — those parse only lossily, so we abstain per the prime directive). All
souffle-clean, 0 collisions, no regressions. Then added §614 **replacement effects** ("If X would
EVENT, REPLACEMENT instead" → a `replacement`-kind ability whose replaced event is a descriptive slug
and whose replacement body must parse into grounded effects, else abstain — quantitative replacements
"twice that many / plus N" faithfully fall through) and fixed a latent faithfulness bug ("put it on
top of its library" had emitted the `put_on_bottom` verb; now a distinct grounded `put_on_top`, §401.1).

### Honest ceiling (overnight run)
This run climbed **56.1% → 64.6%** per-card by exhausting the GENERAL cross-cutting families: the
static-anthem subject grammar and "<subj> gets +N/+N and <conjunct>" rewrite-dispatch; §614
replacement effects; **recursing every clause wrapper** (may / if-cond / if-you-do / unless / until /
trailing-if) through a shared `_combine` so nested conditions stack instead of abstaining (the single
biggest lever, +214); leading "As long as <cond>, <effect>" statics (rewrite→re-dispatch, +200);
divided-damage and target-first "deals damage to X equal to Y"; zone-moves (put into hand/graveyard/
battlefield, compound-split); scoped prevent-all-damage; bare/for-each/for-as-long-as pumps; §724
end-the-turn, §506.4 remove-from-combat, §701.40 exert, §702 Station bands, Prototype, clone ETB,
keyword-with-symbol-param ('ward {2}'); and many subject/target generalizations ('they', possessive
controllers, 'you don't control', type-list targets). All souffle-clean, 0 rules/cards collisions,
rules stay 100% (transpile.py untouched).
What remains (~35%) is the genuine long tail: ~10.4k cards blocked by a single UNIQUE clause plus
~1.8k blocked by 2+, dominated by heterogeneous triggered-ability BODIES (one bespoke effect each),
acorn/sticker (ticket {TK}) cards, quantitative replacements ("twice that many / plus N"), becomes-a-
creature-but-still-a-land back-references, and genuinely un-grounded mechanics (Specialize, Double
team, Starting intensity, spellbook/draft — not in this CR). Pushing past here means either per-card
hardcoding (against the "general grammar patterns" directive) or emitting lossy facts (against the
prime directive). Continue by ranking uninterpreted clauses by CARDS-UNLOCKED (cards they SOLELY
block), not raw frequency (`card_coverage.py` reports it; the blocker ranking is the worklist). cards.dl ~89k grounded facts, conformance_fail=0; validate.py: 0 rules/cards relation
collisions. Faithful-or-abstain: keywords NOT in the §702/§701 roster (megamorph/specialize/prepared —
not standalone CR headings) are abstained, never invented. Patterns landed:
- `kw_line` / `kw_param` — keyword abilities incl. landwalk variants & daybound/nightbound families → §702
- `mana_ability` — `{T}: Add {G}` → §605/§107, abstaining on variable production
- `spell` / `activated` / `triggered` — ability decomposition (§602/§603); bodies parsed by the shared
  **effect engine** `card_effects.py` into grounded `(verb, amount, target)` tuples
- effect verbs grounded in §701 keyword actions + verified core actions (draw/deal_damage/gain_life/
  modify_pt/tap/add_mana/put_counter/grant_keyword/…); targets normalized (any_target, target_creature,
  all_creatures, creatures_you_control, it=anaphor, …); effects carry `extra` (counter kind, token
  spec, mana produced, granted keyword) and `cond` (optional/conditional) slots —
  `effect(card, aid, seq, verb, amount, target, extra, cond)`
- optional/conditional riders: 'you may <effect>' → cond=may; 'if you do, <effect>' → cond=if_you_did;
  'if <condition>, <effect>' → cond=<condition slug> (a descriptive predicate, like a trigger slug)
- compound until-EOT buffs → multiple effects ('gets +N/+N and gains trample', 'gains flying and lifelink')
- static keyword grants ('Enchanted creature has flying', 'Other creatures you control have trample');
  subject restrictions ('Enchanted creature can't attack or block'); additional costs (§601.2b)
- flip_coin (§705), look top-N (§401), put_on_bottom (§401), return_to_battlefield (§614),
  cant_attack/block/be_blocked-this-turn (§508/509), sacrifice a/another <type>, permanent keyword
  grants ('It gains haste'), mana of the chosen color, modal 'Choose one or more —'
- cant_be_regenerated (§701.19), search your library for <X> (§701.18), subject keyword-actions
  ('it explores', 'it connives'), 'Then <effect>' discourse-lead strip, fetch sequences split on ', put'
- broader self-reference normalization (this Aura/Equipment/Vehicle/Saga/… → ~)
- '<effect> unless you/its-controller pays <cost>' → cond=unless_pay_<cost> (counters, upkeep
  sacrifices); '<effect> at the beginning of the next upkeep/end step' → cond=delayed
- card-level statics: '~ can(\'t) be your commander', Doctor's companion, Choose a Background, Partner
- exile-until ('exile <t> until ~ leaves the battlefield', O-Ring); copy (§707); roll a dN (§706);
  remove a counter (§122); 'discards that card'; delayed 'at end of combat'; before-attackers modifier
- complex static combat restrictions with a qualifier (card_restriction): 'can't be blocked by …',
  'can block only …', 'can't attack unless …' (§508/509); 'may choose not to untap' static
- play permission ('you may play that card [this turn]', §601/§116); mana 'any combination of colors';
  'roll a/two N-sided die/dice'; 'return <t> to the battlefield [under owner's control][ tapped]';
  'up to N target …' targets; put-on-bottom 'in any order'
- choose new targets for the copy (§707.10); cast <X> without paying its mana cost; experience counters
  (§122); gain life equal to <X>; doesn't-untap for 'that creature' / next untap step
- 'Until <timing>, <effect>' prefix (cond=until_…, preserving an inner may); choose a color/type (§700.2);
  '<t> reveals their hand'; doesn't-untap as an effect clause
- granted abilities (§613.6, card_grants_ability): '<subj> has/gains "<ability>" [until end of turn]' —
  whole-unit grants recorded as a slug of the (self-normalized) ability text; coarse but faithful (its
  'self' resolves to the holder). In-body grants now survive too: the body splitters mask "…" regions
  to a sentinel before sentence/and-splitting, so a quoted ability's internal punctuation can't corrupt
  the parse (transpile_card._mask_q / _sentences).
- ability-modifier clauses ('Activate only as a sorcery', 'triggers only once each turn') recorded as
  ability_modifier facts instead of blocking the body
- `loyalty` planeswalker abilities `[+N]:`/`[−N]:` (§606); `saga_chapter` `I —`/`I, II —` (§714)
- `modal` + `mode_option` (§700.2), `cant` restrictions (§508/509/601), `static_pt` (§613),
  `etb_tapped` / `enters_with_counters` (§614/§122), `doesnt_untap` (§502), `attacks_each_combat` (§508)

The interpreter REQUIRES every effect sub-clause in a unit to parse, else abstains the whole unit (no
half-facts) — e.g. Wrath of God abstains because "They can't be regenerated" isn't yet handled.

## Simulation — the facts are executable
Two layers, both consuming the grounded facts as their IR (no per-card logic — one handler per grounded
verb; cards whose text wasn't interpreted act as their vanilla characteristics, never faked):

- **`sim.py`** — a minimal shim: loads cards.dl, applies effects to a tiny state in a scripted scenario
  (Llanowar Elves taps; Lightning Bolt kills a creature + burns a player; Divination; Giant Growth;
  Healing Salve modal life). The smallest reviewable proof that the facts execute.
- **`engine.py`** — a fuller game engine: a real turn loop (untap → upkeep → draw → main → combat →
  main2 → end), mana system (basic-land §305.6 + interpreted mana abilities, cost payment), the stack,
  casting from hand, ETB triggers, combat (blocking, flying/reach evasion, deathtouch, lifelink,
  vigilance, trades), and state-based actions. Two simple-AI real-card decks (green ground vs blue
  flyers) play a complete game to a win — e.g. blocks trade Grizzly Bears for Wind Drake, blue flyers
  connect because green has no reach, and the game ends with a winner.

This is the executable proof that the grounded fact IR is the substrate for the eventual C++ engine.

## Worklist (top uncovered templates, by instance count — the high-leverage next slices)
1. `~ enters tapped.` (502) — ETB self-replacement → ground in §614/§603
2. `Choose one —` (327) / `Choose one or both —` — modal spells → §700.2
3. `Draw a card.` (262) — one-shot effect → ground "draw" in turn-based/§120
4. `Equipped/Enchanted creature gets +N/+N.` (165/129) — static P/T mod → §613 layers, `equip`/`enchant`
5. `{S}: ~ gets +N/+N until end of turn.` (134) — activated pump (reuses cost parser + a P/T effect)
6. `~ deals N damage to any target.` (96) — SVO effect → ground "deals damage" in §120
7. `When ~ enters, …` (98/73/58) — triggered ETB → ability(triggered) + effect (§603)
8. combat restrictions `~ can't block / be blocked / attacks each combat if able` (89/60/53) → §509/§508

Method per slice: pick the highest-count grounded frame, write a pattern that abstains on anything
not clean, re-run `card_coverage.py`, souffle-check `build_cards.py`. Reuse `transpile.py`'s grammar
patterns (imperative/SVO/copula) for prose effects so we don't reinvent the parser.

## Path to C++
The Datalog facts are the IR. The C++ engine is generated FROM facts: rules facts (turn structure,
SBAs, layers, the keyword/zone/action definitions) become the engine's fixed machinery; card facts
(`card_keyword`, `card_mana_ability`, `card_*_effect`, …) become per-card data the engine interprets.
Because every card fact references a rules-defined term, the codegen has a closed vocabulary to switch
on. Sequence: broaden card coverage (this campaign) → define the C++ fact-schema ABI → codegen the
static engine from rules facts → codegen card data → execute.

## Reproducibility (bulk data is gitignored)
```
mkdir -p mtgjson && curl -L -o mtgjson/AllPrintings.json.gz \
  https://mtgjson.com/api/v5/AllPrintings.json.gz && gunzip -kf mtgjson/AllPrintings.json.gz
python3 build_oracle_corpus.py     # -> mtgjson/oracle_corpus.json
python3 card_coverage.py           # current %
python3 build_cards.py             # -> datalog/cards.dl (gitignored; souffle-checkable)
```
`mtgjson/` and `datalog/cards.dl` are gitignored — they derive from external bulk data, not the repo.
