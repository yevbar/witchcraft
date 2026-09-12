# August 2026 rules coverage: implementation and verification

Reviewed locally on September 12, 2026 against the bundled **rules.txt effective August 7, 2026**. This is not a claim about a newer upstream rules document or complete implementation of every Comprehensive Rule.

The fixes are on `codex/rules-20260807`, through `b7743fd`, for local merge into `master`. The original findings are retained below as a historical record. Their line numbers describe the earlier revision.

## Work completed

| Audit area | Implemented and tested |
| --- | --- |
| Library movement / 605.1a | Hand-to-library movement prevents mana-ability classification; mana and library movement resolve as one stack activation. |
| Power-up | Variable X costs and effects, explicit X choices, entry discounts, Hulk's generic discount, Wonder Man's extra use, immediate mana production with activation limits, and independent values for multiple activations on the stack. |
| Heal | Natural “heal all damage” wording, Wolverine's damage replacement, direct/combat marked damage, and target protection. |
| Explore | A departed source still explores but receives no counter. |
| Connive | Positive and zero event distinction, events after impossible actions, APNAP sequencing with captured controllers, and connive triggers. |
| Battles | Printed defense on entry, Siege/type-less protector choices, protector changes removing the attacked object from combat, legal blockers, battle combat damage, zero-defense SBAs, and Siege defeat on the stack including transformed casting and countered-trigger cleanup. |
| Casting choices / Teamwork | Declining Teamwork does not require rider-only targets; paying exposes those target choices. |
| Conditional flash | The parsed legendary-creature condition grants permission, and losing that condition during payment does not invalidate casting. |
| Repeated optional payments | Multiple payments produce one reflexive trigger. Generic ability payment now actually consumes floating mana. |
| Crew | Chosen creatures pay by tapping; every activation retains its own crew attribution; a subtype-based intervening-if checks that activation. |
| Card copies | Each created card copy gets an independent cast/decline choice, cast bookkeeping and retained choices. |
| Damage-source information | Departed sources retain deathtouch, lifelink, wither and infect information for later direct damage. |
| Face-down restrictions | Double-faced/melded objects and merged objects containing an ineligible component reject the operation without changing characteristics. Face-down base characteristics restored in the generated engine. |
| Storied | Granted Storied is observed before subsequent effects and persists after qualification is lost. |
| Departed-player history | Turn history is retained until the departed seat's next scheduled turn; game-wide action history remains available. |
| Silent omissions | Card generation retains unparsed units; the runtime bridge reports them. A clean list can no longer hide an entirely unparsed unit. |

These are concrete supported cases, not a universal card-text interpreter. General damage-division choices, arbitrary crew conditions, full merged-permanent creation, and complete multiplayer departure processing remain broader engine limitations. The new face-down and history helpers provide the relevant rule operations; they do not constitute those entire subsystems. Existing immediate-resolution shortcuts for some triggered effects also remain.

Actual corpus checks confirm the Power-up additions on Stature, Wonder Man and Hulk, and the healing replacement on Wolverine. Wolverine's separate optional fight clause remains reported as unparsed. Birgi's boast modification, Kinnan's mana trigger and Mindbreak Trap's alternative cost are also explicitly reported as unsupported; their tests no longer incorrectly assert full-card support.

## Commits

- `99aa862`: library movement, healing replacement and departed explore.
- `d49e073`: variable Power-up and activation modifiers.
- `f29006c`: connive events and parser omission reporting.
- `1de967b`: face-down characteristics and granted Storied.
- `6d6dbf4`: battle gameplay and Siege defeat.
- `d9b41c7`: crew, casting choices, source information and turn history.
- `b7743fd`: verification fixes, separate activation values, priority-only mana execution and honest card coverage checks.

## Verification

- New audit regressions: **24/24**, normal and incremental engines.
- Existing August rules regressions: **27/27**.
- Face-down: **39/39**; targeting: **73/73**; stack: **28/28**; schema: **9/9**; fast mana: **46/46**; game: **27/27**; core engine: **48/48**.
- Engine generation is byte-for-byte deterministic; source/engine hashes match the manifest; Souffle conformance output is empty.
- Rebuilt the card corpus: 35,033 cards; 30,911 parse every unit (88.2%). Parsing every unit does not guarantee runtime support.
- Rebuilt and installed a wheel outside the checkout. Its bundled native engine and manifest load without a compiler or repository imports; face-down characteristics, battle SBA, Vibranium and new runtime modules pass the smoke check. This is a local validation wheel, not a published release.
- Full final run: **193/212 test files pass**. All **19** remaining failing files also fail in an isolated checkout of `master` at `6b70396`. This is not a fully green repository. The comparison establishes the failing-file baseline, not proof that every assertion in an already-failing suite is unchanged.

Remaining failing files:

- `tests/test_anthem_filters.py`
- `tests/test_board_scopes.py`
- `tests/test_combat_requirements.py`
- `tests/test_combat_restrict2.py`
- `tests/test_combat_restrictions.py`
- `tests/test_conditional_static.py`
- `tests/test_creature_zone.py`
- `tests/test_deck_evaluator.py`
- `tests/test_event_map.py`
- `tests/test_initiative.py`
- `tests/test_kinnan.py`
- `tests/test_lose_abilities.py`
- `tests/test_lose_abilities_scope.py`
- `tests/test_monarch.py`
- `tests/test_transform.py`
- `tests/test_translate.py`
- `tests/test_translate_effects.py`
- `tests/test_translate_spell2.py`
- `tests/test_translate_static_filter.py`

Local detailed logs are in `/tmp/rules-final-suite/` and `/tmp/rules-baseline-suite/`. They are temporary verification artifacts. The baseline worktree was used only for comparison.

## Historical audit before these fixes

# Rules coverage audit — September 12, 2026

Scope: `codex/rules-20260807` at `7883b2f`, compared with `master`, using the **bundled rules.txt effective August 7, 2026**. This audits the local rules update, not whether Wizards has published another document. No engine changes were made during this audit.

The branch implements meaningful parts of the update, but recognition and generated conformance checks do not establish runtime coverage. The following findings distinguish reproduced omissions from interactions that still lack targeted validation. Several gaps predate the branch; they are not all regressions introduced by it.

## Confirmed uncovered behavior

### 1. Library movement still bypasses the new mana-ability rule — CR 605.1a

**Reproduced through parser and bridge:**

`{T}: Add {G}. Put a card from your hand on top of your library.`

The bridge emits both `mana_source(probe)` and a separate `activated_ability` with effect `hand_to_top`, and reports no dropped effects. The ability must instead be one non-mana activation whose effects resolve together on the stack. The current representation allows mana production through the source machinery without resolving the hand-to-library effect.

`packages/mtg/bridge_to_engine.py:2579` detects a limited verb/target list; it misses this parsed library-moving form. Existing tests cover draw and mill, not this form. Self-replacement effects versus external replacements also remain untested.

Priority: high; this contradicts a substantive rule change the branch claims to implement.

### 2. Power-up remains incomplete — CR 702.193 and card-specific modifications

- **Variable cost:** Stature, Size Shifter has `{X}{U}{U}: Put X +1/+1 counters on Stature`. Its bridge result drops `('activated_cost', '{X}{U}{U}')` and emits no activation. The new tests cover fixed and hybrid costs, not X choice, reduction, payment, or X-sized effects.
- **Additional activations:** With Wonder Man, Hollywood Hero present, a used Serpent Specialist Power-up is still unavailable. Wonder Man's additional-activation text is absent from its interpreted ability data, yet the bridge reports no drops.
- **Additional reductions:** With Hulk, Gamma Goliath present, Serpent Specialist still costs `(3 generic, 1 green)` outside its entry turn; Hulk should reduce it to `{G}`. Hulk's reduction is stored as a keyword parameter and never applied. Its bridge result also reports no drops.
- **Mana-producing Power-up:** Synthetic `Power-up — {1}: Add {G}.` becomes a mana source without an activated row. The source path bypasses the new once-only activation bookkeeping. This is a valid-form probe, not a claim that a current corpus card has that exact text.

The Wonder Man/Hulk cases concern composition with card text rather than additional text in CR 702.193 itself. They still prevent general support for the newly implemented keyword.

Relevant code: `packages/mtg/bridge_to_engine.py:2388`, activated-ability translation, and `packages/mtg/rules_2026.py:34`.

### 3. Heal support does not cover its natural wording or replacement use — CR 701.69

- **Reproduced parser/bridge omission:** `{T}: Heal all damage on target creature.` reports `('effect', 'heal')` as dropped. The existing integration test instead uses the narrower `Heal target creature.` shape.
- **Real card omission:** Wolverine, Fierce Fighter's “all other damage already dealt to him is healed” replacement is absent from its interpreted card data. The bridge reports no drops because the text was lost before that stage. Its data contains only haste; other missing text on the card is not necessarily attributable to this rules update.
- **Reproduced targeting defect:** Calling the heal handler with `target_creature` for Bob removes marked damage from Alice's hexproof creature. Target selection checks battlefield/type/control but not target legality.

Relevant code: `effect_handlers/rules_2026.py:6` and `:13`. No replacement-effect or target-protection test exists in the new rules suite.

### 4. Explore still modifies a departed object — CR 701.44c

**Reproduced:** with `gone` absent from the battlefield and a nonland card on top, resolving explore adds `('gone', 'p1p1', 1)` to the state's counters. The reveal/library decision should still happen using the appropriate controller information, but no counter should be placed on the departed permanent.

Relevant code: `effect_handlers/army_populate.py:166`, which unconditionally calls `_bump_counter` for a nonland reveal. The new departed-source test covers connive, not explore. A full controller-change/last-known-information scenario has not been tested.

### 5. Connive events remain unsupported — CR 701.50b–f

**Reproduced:** `Whenever ~ connives, draw a card.` is dropped by the bridge as `('event', None)`.

The handlers implement draw/discard/counters, but do not emit a connive event. The connive-zero test therefore checks that nothing is drawn/discarded; it does not establish the distinction between positive connive events and connive 0, or that the event occurs after an unsuccessful positive connive. Simultaneous conniving in APNAP order and full last-known-controller handling remain untested.

Relevant code: `effect_handlers/keyword_actions.py:55` and `:79`, and the bridge's trigger mapping.

### 6. Battle changes are descriptive, not playable — CR 310, 506.4, 704.5v–y

**Reproduced:** an on-battlefield non-Siege battle with defense 0 produces no `zone_change` from the runtime engine.

The updated Siege/non-Siege checks exist in `datalog/sba_extra.dl`, but the runtime engine does not consume those tables as a complete battle subsystem. There is no runtime implementation found for protector selection, protector-change removal from combat, or the non-Siege zero-defense state-based action. The current rules test checks extracted tuples only.

This is a pre-existing subsystem limitation exposed by the expanded battle rules, not a newly introduced regression.

## Changed-rule interactions still requiring targeted validation

| Rules | Coverage gap / evidence |
| --- | --- |
| 601.5; 702.194c | Targets or effect divisions that depend on later casting choices; Teamwork-only targets when the additional cost is declined. Basic Teamwork payment and rider tests do not exercise this. |
| 601.6a | Conditional flash remaining valid after its enabling condition disappears during casting. A representative conditional-flash clause (`You may cast creature spells as though they had flash if you control a legendary creature.`) is dropped as `('static', 'cast')`. No condition-disappears-during-payment regression exists. |
| 603.12a | Repeated optional payment producing one reflexive trigger. `_fire_you_do_costs` offers a Boolean pay/decline decision; no targeted test establishes the revised multiple-payment semantics. |
| 702.122c/e | Which creatures crewed a particular activation and the intervening-if trigger's attribution. No crew action or crew-attribution state was found in the runtime driver/bridge. |
| 707.12a | Independent cast/decline decisions for multiple created card copies. Existing spell-copy code puts copies directly onto the stack; that does not validate creating card copies and optionally casting each. |
| 702.2e, 702.15c, 702.80b, 702.90d | Deathtouch, lifelink, wither and infect when the damage source is no longer in its expected zone. No focused regression checks the clarified last-known-information cases. |
| 712.16; 730.2j | Attempts to turn double-faced/melded/merged permanents face down must also preserve their characteristics. The branch tests transforming an already face-down object, which is a different operation. |
| 702.195c | Storied's continuous-effect reapplication before trigger checking. Tests cover distinct qualifying objects and persistence; not transient qualification, granted Storied, or the ordering with continuous effects and triggers. |
| 800.4i | A departed player's “last turn” history expiring when their next turn would have begun. No focused runtime test or history-expiry mechanism was identified. |

These rows are not all independently reproduced runtime failures. They identify missing implementation surfaces or missing evidence for the changed interactions.

## What is covered

The existing 27-test rules suite establishes useful core behavior: fixed/hybrid Power-up payment, entry reduction, once-only use and reentry reset; draw/mill mana classification and effect order; basic Heal and Recruit; retained direct/combat damage; hone's equipped power bonus; worthy's legendary/color/non-Villain predicate and basic Equip worthy; Storied counting/persistence; face-down transformation no-op; connive-zero/basic departed-source behavior; and Vibranium restrictions including X payments. Those tests passed on both normal and incremental backends during the preceding work.

Vocabulary generation includes the new subtype names. Several textual edits are renumbering, punctuation, terminology, or clarifications; the count of changed rule identifiers is not the count of newly implemented mechanics.

The source and engine manifests match the committed local rules. Full-generation conformance and targeted runtime tests remain distinct checks. The wheel staging directory was observed to contain older engine data during the preceding readiness check, so a new packaged smoke test is still needed for release.

## Recommended order

1. Fix the library-movement classification hole and the silent parser omissions, then add end-to-end regressions using actual card text where available.
2. Complete Power-up X and card-specific modifiers; extend Heal to its replacement/natural wording and legal targeting.
3. Add explore/connive event and departed-source tests, then the casting/Teamwork/optional-copy interactions.
4. Treat battle gameplay, crew attribution, and multiplayer history as explicit scope decisions; do not present generated rule tables as runtime support.

A clean bridge `dropped` list is currently insufficient evidence of full-card coverage. A coverage gate also needs to account for original card units that produced no interpreted facts.
