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

**Current (slice 1–2): TEMPLATE 3.8% · INSTANCE 25.8%.** Patterns landed:
- `kw_line` — `Flying` / `Flying, vigilance` / `First strike` → `card_keyword` (§702)
- `kw_param` — `Enchant creature` / `Equip {S}` / `Ward {S}` → `card_keyword` + `card_keyword_param`
- `mana_ability` — `{T}: Add {G}` → `card_mana_ability` + `card_adds_mana` (§605/§107), abstaining on
  variable production (`Add {G} for each …`)

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
