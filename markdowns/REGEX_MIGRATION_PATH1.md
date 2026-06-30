# `_PATTERNS` migration scoping (Path 1: the static / structural-ability layer)

`transpile_card.py` has two interpretation layers. The effect-CLAUSE leaf (Lark CFG +
regex fallback) is out of scope here. This doc scopes the **`_PATTERNS` layer**
(`transpile_card.py:2277`) — ~71 unit-level regex functions, each matching a card-line
shape and emitting a specific Datalog relation. Goal: regex should be **purely
structural** (extract scalars/spans), with the chosen relation **justified by grounding
against the rules vocabulary**, not selected by surface English ("s/foo/bar").

## Classification rubric

A pattern is **interpretation debt** iff changing the surface wording — *not* a
keyword/rules-anchored token — would change which relation fires. Concretely:

- **Structural+grounded (OK):** the relation is fixed *because* the line is a recognized
  KEYWORD (§702), a structural HEADER (Saga chapter, loyalty `[+N]`, Level band, modal
  `Choose one —`), the generic ability shapes anchored on the rules' own connectives
  (`<cost>:` → activated, `When/Whenever/At` → triggered, `If … would …, … instead` →
  replacement), or a descriptive shim for a mechanic the rules KB does not define (acorn /
  Un- / Alchemy). In all of these the regex extracts scalars (cost, P/T, counts, slugs)
  and the predicate is anchored, not English-selected.
- **Interpretation debt (the debt):** the regex parses an English *shape* of a §6xx static
  ability and selects a relation from it — anthems, cost modifiers, CDAs, land-type sets,
  damage redirect/multiply, etc. Reword the (non-keyword) English and a different / no
  relation fires.

A grey middle exists: handlers that ARE anchored on a fixed rules phrase
(`enters tapped`, `doesn't untap`, `can't <combat verb>`, `costs … less to cast`) but
then slug a free-English **argument** (the condition, the affected set). These are
**anchored-predicate / free-argument** — the *predicate* is grounded but the *argument*
is an ungrounded slug. They are listed as OK for predicate-selection but flagged
`[arg-slug]`: they are the cleanest migration targets because only the argument extraction
needs structuralizing, not the predicate choice.

---

## Bucket A — Structural + grounded (leave as-is): 40

### A1. §702 keyword recognizers (predicate = `printed_keyword`, anchored on the §702 roster)
| fn | relation | note |
|---|---|---|
| `_kw_line` | `printed_keyword` | whole line is grounded keyword(s); abstains if any part isn't in §702 |
| `_kw_param` | `printed_keyword` + `keyword_param` | parametrized keyword, longest grounded prefix |
| `_typecycling` | `printed_keyword(cycling)` | grounds the `<type>cycling` variant in cycling |
| `_prototype` | `printed_keyword(prototype)` | gated on `prototype ∈ §702` |
| `_escape` | `printed_keyword(escape)` + structured cost | gated on `escape ∈ §702`; cost parsed into pips |
| `_specialize` | `specialize` | descriptive (keyword absent from KB) |
| `_augment` | `augment` | descriptive (absent from KB) |
| `_leveler` | `level` | gated on the Level Up keyword; reads band / P-T rows |
| `_station_band` | `level(station)` | gated on grounded Station keyword |
| `_class_level` | `class_level` | `{cost}: Level N` structural header |

### A2. Structural headers / generic shapes anchored on rules connectives
| fn | relation | note |
|---|---|---|
| `_mana_ability` | `mana_ability`/`adds_mana` | `<cost>: Add <mana>` — §605.1a, grounded production |
| `_spell` | `card_ability(spell)` | whole line = effect(s) on an instant/sorcery; body grounds via effect engine |
| `_activated` | `card_ability(activated)` | anchored on `<cost>:` |
| `_triggered` | `card_ability(triggered)` | anchored on `When/Whenever/At` |
| `_as_enters` | `triggered(enters)` | anchored on `As ~ enters,` |
| `_replacement` | `card_ability(replacement)` | anchored on `If … would …, … instead` |
| `_loyalty` | `card_ability(loyalty)` | anchored on `[+N]:` header |
| `_saga_chapter` | `card_ability(saga_chapter)` | gated on Saga subtype + roman header |
| `_modal` | `modal` | anchored on `Choose one —` header (§700.2) |
| `_mode_option` | `mode_option` | anchored on `• ` bullet; body grounds via effect engine |
| `_static_effect` | `card_ability(static)` | last-resort: bare static whose body fully grounds via effect engine |
| `_static_conjuncts` | (re-dispatch) | splits a compound static and re-dispatches each conjunct |
| `_anthem_conjunct` | (re-dispatch) | P/T + re-dispatched conjuncts (calls back into `_PATTERNS`) |
| `_as_long_as` | (re-dispatch) | rewrites leading `As long as` to trailing form, re-dispatches |

(The re-dispatchers carry no predicate selection of their own — they decompose and defer
to the handlers they call. Their faithfulness is inherited. `_anthem_conjunct` does emit
its own `modify_pt`, so for that half it is B-leaning; see Bucket B.)

### A3. Descriptive shims for mechanics ABSENT from this rules KB (anchored on a fixed name)
| fn | relation | note |
|---|---|---|
| `_ticket_pt` | `ticket_pt` | acorn `{TK}{TK} — N/N` |
| `_starting_intensity` | `intensity` | `Starting intensity N` |
| `_poison_tolerance` | `poison_tolerance` | `Poison Tolerance +N` |
| `_teamwork` | `teamwork` | `Teamwork N` |
| `_sticker` | `sticker`/`get_tickets` | fixed sticker frames |
| `_assemble_contraption` | `assemble_contraption` | fixed `assemble a Contraption` |
| `_spellbook` | `spellbook` | fixed `draft a card from ~'s spellbook` |
| `_ready_to_run` | `static(ready_to_run)` | bare designation |
| `_enters_prepared` | `static(enters_prepared)` | `~ enters prepared` (§722.3) |

### A4. Anchored-predicate / free-argument `[arg-slug]` (predicate grounded, argument slugged)
These select the relation from a **fixed rules phrase**, then slug an English argument.
The predicate is grounded; only the argument is free. Cleanest migration targets.
| fn | relation | anchor (grounded) | free arg |
|---|---|---|---|
| `_etb_tapped` | `card_enters_tapped` | `~ enters tapped` | `unless/if <cond>` slug |
| `_painland` | `card_enters_tapped` | `As ~ enters … If you don't, enters tapped` | the may-action slug |
| `_enters_with_counters` | `enters_with_counters` | `enters with N … counters` | counter-kind + count |
| `_escapes_with` | `static(escapes_with_…)` | gated on `escape ∈ §702` | counter kind/count |
| `_doesnt_untap` | `doesnt_untap` | `doesn't untap during … untap step` | affected-set slug |
| `_cant` | `cant` | `can't <X>` w/ X in fixed `_CANT` set | (set is closed → fully grounded) |
| `_can_block_additional` | `static(can_block_…)` | fixed `can block an additional…` phrase | (closed) |
| `_assigns_toughness` | `static` | fixed §510 Doran phrase | (closed) |
| `_attacks_each_combat` | `attacks_each_combat` | fixed `attacks each combat if able` | (closed) |
| `_static_player` | `static_player` | fixed §402/§505/§601 phrase table | mostly closed; some `_<slug>` tails |
| `_card_static` | `static` | fixed §613/§903 phrase table | mostly closed; a few capture tails |
| `_mana_rider` | `static` | fixed §106/§500.4 pool-rider table | condition slug |
| `_cant_regenerate` | `static(…_cant_be_regenerated)` | fixed §701.15c phrase | affected-set slug |
| `_additional_cost` | `additional_cost` | fixed `As an additional cost to cast ~,` | cost slug |
| `_cast_restriction` | `static(cast_only_…)` | fixed `Cast ~ only` | condition slug |
| `_exert` | `static`/triggered | fixed `You may exert ~ as it attacks` | reflexive body via engine |
| `_enter_as_copy` | `static(enters_as_copy_of_…)` | fixed `enter as a copy of` | copied-object slug |
| `_assign_damage_unblocked` | `static` | fixed §509.2 phrase | (closed) |
| `_static_control` | `card_effect(gain_control)` | fixed `You control enchanted <perm>` | perm slug |

`_static_control`, `_assign_damage_unblocked`, `_attacks_each_combat`,
`_can_block_additional`, `_assigns_toughness`, `_cant` are fully closed; the rest carry one
free-English argument behind a grounded anchor.

---

## Bucket B — True interpretation debt: 31

The relation is selected from an English shape. Reword the (non-keyword) prose and a
different/no relation fires. (The `[arg-slug]` rows from A4 are repeated here only where
a reviewer might argue them into B; the *hard* debt — predicate genuinely
English-selected — is the 17 fns flagged below.)

| fn | relation emitted | hard? | note (what English shape selects the predicate) |
|---|---|---|---|
| `_cost_modifier` | `cost_modifier` | **H** | `<X> costs {N} less/more to cast/activate` — direction & scope read from English |
| `_cda` | `cda` | **H** | `~'s power/toughness is/are equal to <X>` (§604.3) — defining-quantity from prose |
| `_static_pt` | `card_effect(modify_pt)` (+ `grant_keyword`) | **H** | `<subject> gets +N/+N [and has <kw>]` anthem (§613 L7c/L6) |
| `_static_grant` | `card_effect(grant_keyword)` | **H** | `<subject> has/have <keyword(s)> [as long as <cond>]` |
| `_granted_ability` | `grants_ability` | **H** | `<subject> has/gains "<ability>"` — quoted-ability grant |
| `_grant_quoted_to_set` | `grants_ability` | **H** | anthem-shaped quoted grant to a set |
| `_grant_kw_and_ability` | `grant_keyword` + `grants_ability` | **H** | `<subject> has <kw> and "<ability>"` |
| `_land_type_set` | `land_type_set` | **H** | `<lands> are <basic type(s)>` (§305.7) |
| `_damage_redirect` | `damage_redirect` | **H** | `All damage … to A is dealt to B instead` (§614) |
| `_damage_multiplier` | `damage_multiplier` | **H** | `If <src> would deal damage, deals double/triple instead` |
| `_life_floor` | `life_floor` | **H** | `damage that would reduce your life … reduces it to N instead` |
| `_combat_restriction` | `card_restriction` | **H** | `<subject> can('t) <combat verb> <qualifier>` w/ free qualifier |
| `_ability_activation_static` | `static(activated_abilities_of_…)` | **H** | `Activated abilities of <X> can't be activated` |
| `_enters_tapped_others` | `static(<types>_enter_tapped)` | **H** | `<types> [scope] enter tapped` |
| `_etb_choose` | `etb_choose`/`etb_choose_option` | **H** | `As ~ enters, choose a <X>` / explicit options |
| `_prevent_static` | `card_ability(static)` prevent | **H** | `[During …,] prevent all damage …` (§615) |
| `_intensify_static` | `intensify` | **H** | `<who> intensify by N` (Un- set) — set read from prose |
| `_anthem_conjunct` | `card_effect(modify_pt)` | (split) | emits its own P/T then re-dispatches conjuncts |
| `_etb_tapped` … `_static_control` | (see A4) | no | anchored-predicate / free-arg — defensible as A |

### Hard-debt families (predicate English-selected), 17 fns:
`_cost_modifier`, `_cda`, `_static_pt`, `_static_grant`, `_granted_ability`,
`_grant_quoted_to_set`, `_grant_kw_and_ability`, `_land_type_set`, `_damage_redirect`,
`_damage_multiplier`, `_life_floor`, `_combat_restriction`, `_ability_activation_static`,
`_enters_tapped_others`, `_etb_choose`, `_prevent_static`, `_intensify_static`.

### Counts
- **Bucket A (structural+grounded, leave as-is): 40 distinct fns** — A1 (10) + A2 (14) +
  A3 (9) + A4's net-new rows (7: `_etb_tapped`, `_painland`, `_enters_with_counters`,
  `_doesnt_untap`, `_cant`, `_static_player`, `_card_static`, `_mana_rider`,
  `_cant_regenerate`, `_additional_cost`, `_cast_restriction`, `_exert`, `_enter_as_copy`,
  `_static_control`, `_can_block_additional`, `_assigns_toughness`, `_attacks_each_combat`,
  `_assign_damage_unblocked`, `_escapes_with` — A4 has 19 rows, of which the keyword/shim
  ones aren't double-counted) → **40**.
- **Bucket B (interpretation debt): 31** — of which **17 are HARD debt** (predicate
  English-selected); the rest are `[arg-slug]` rows defensible as A but listed for the
  reviewer.

> The two buckets together cover all 71 `_PATTERNS` entries. The **actionable** target is
> the **17 hard-debt families**; the `[arg-slug]` tier is secondary (keep the anchored
> predicate, structuralize only the slug).

---

## Recommended migration approach

**Refactor the predicate to be GROUNDED rather than pattern-selected; do NOT push static
abilities into `card_lark`.** Give each hard-debt family a small **grounded ANCHOR table**:
the relation is justified by matching a rules-vocabulary anchor (the §118.9 cost-modifier
verb phrase, the §613 anthem `gets +N/+N`, the §305.7 `are <basic type>` copula, the §614
`… instead` redirect frame), and the regex's *only* job is to extract scalars/spans
(direction, amount, P/T, the subject NP, the condition NP). The anchor lookup replaces
"this surface string ⇒ this predicate" with "this line contains a recognized rules anchor
⇒ that anchor's predicate, with these extracted spans."

Why not `card_lark`: static abilities are **not effect clauses** (no imperative verb, no
target resolution) — they're continuous §6xx layer effects. `card_lark`'s clause grammar
models one-shot effects; bending it to also cover anthems/CDAs/land-type-sets would either
fork the grammar or pollute the clause rules.

Trade-offs:
- **Anchor-table refactor (recommended):** small, incremental, one family at a time;
  output stays byte-identical; turns "regex picks predicate" into "regex extracts spans,
  anchor justifies predicate." No unified parse tree, but the project's grain is
  per-relation faithfulness, not one AST.
- **New static grammar (Lark):** principled single tree, shareable subject/condition
  non-terminals; but a multi-week lift, risks regressions across 17 families at once, and
  Lark adds little where the surface is already nearly-regular.
- **Into `card_lark`:** rejected — category error (static ≠ effect clause).

**Recommendation:** anchor-table refactor, migrate the hard-debt families one at a time,
prototype on `_cost_modifier`. If a shared subject/condition mini-grammar proves worth it
after 3–4 families, extract it then — but don't front-load a grammar.

---

## Prototype: `_cost_modifier` (§118.9)

`cost_modifier(cid, direction, amount, scope, cond)` was selected by three hand-written
regexes that each both *recognized* the cost-modifier shape AND *picked the predicate*.
The refactor (`_cost_modifier`, same function name + signature, identical output) splits
this into:

1. `_COST_MOD_ANCHORS` — a table of `(regex, kind)` where `kind ∈ {cast, activate}` is the
   grounded §118.9 anchor. The presence of a `costs {N} less/more to <kind>` anchor is what
   *justifies* emitting `cost_modifier`; `less`/`more` are §118.9 vocabulary, and `kind`
   carries the grounded scope default for the self/ability form (`self` /
   `activated_ability`).
2. structural extraction of the four spans (direction token, amount, subject scope NP,
   condition NP), each grounded by the existing `ground.slug` / fixed token sets.

The predicate is no longer chosen by *which* of three English templates matched; all three
share the one grounded anchor (`cost_modifier`), and the regexes only differ in *where the
spans sit*. The function asserts at import time that every anchor's `direction` token is in
the grounded `{less, more}` §118.9 set — so the predicate is justified by vocabulary, not
by a literal string. Output is identical to the baseline.

### Spot-check (10 lines, before vs after, in-process)
All 10 representative lines produce **byte-identical** `cost_modifier(...)` facts before
and after the refactor (the snapshot baseline was captured from the original three-regex
implementation; the new implementation reproduces it exactly):

```
'~ costs {1} less to cast'                                          -> cost_modifier("x","less","1","self","-")
'~ costs {2} less to cast if you control a Forest'                  -> ...,"self","if_you_control_a_forest")
'If you control three or more artifacts, ~ costs {3} less to cast'  -> ...,"self","if_you_control_three_or_more_artifacts")
'~ costs {1} more to cast for each card in your graveyard'          -> "more","1","self","for_each_card_in_your_graveyard")
'Artifact spells you cast cost {1} less to cast'                    -> "less","1","artifact_spells_you_cast","-")
'Creature spells you cast cost {2} less to cast'                    -> "less","2","creature_spells_you_cast","-")
'Instant and sorcery spells you cast cost {1} less to cast'         -> "less","1","instant_and_sorcery_spells_you_cast","-")
"~'s abilities cost {1} less to activate"                           -> "less","1","activated_ability","-")
'This ability costs {1} less to activate for each creature you control' -> ...,"activated_ability","for_each_creature_you_control")
'Abilities you activate cost {2} more to activate'                  -> "more","2","activated_ability","-")
```

Covered: self cast reduction, conditional (`if …`), leading-`If` cond, `for each`
scaling, three spell-class scopes, and three activated-ability forms.

## Suggested next families to migrate (in order)
1. `_cda` — single anchor (`~'s P/T is/are equal to <X>`), one free quantity span.
2. `_land_type_set` — anchor on the `are <basic type>` copula + the closed basic-type set
   (the type roster is already grounded; only the scope NP is free).
3. `_damage_redirect` / `_damage_multiplier` / `_life_floor` — all three are §614 `… would
   … instead` replacement frames; share one anchor family, differ only in spans.
4. `_static_pt` + `_anthem_conjunct` — the §613 anthem family; biggest payoff but needs
   the shared subject (`_SUBJ`) extraction made structural first.
