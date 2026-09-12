# Comprehensive Rules update review — September 12, 2026

This records the initial findings before implementation. See [RULES_UPDATE_NOTES.md](RULES_UPDATE_NOTES.md) for the local branch changes and final validation.

## Download and scope

Updated `rules.txt` with the exact bytes linked as **TXT** on [Wizards’ official rules page](https://magic.wizards.com/en/rules): [MagicCompRules 20260819.txt](https://media.wizards.com/2026/downloads/MagicCompRules%2020260819.txt). The filename contains August 19; the document itself says **effective August 7, 2026**. The previous repository version was effective April 17, 2026.

SHA-256: `4381ad1b39ab2c05f7d03633a20f711ed37277074d3266dcba5f38cbb527423f`.

This review follows the rules splitter, rules-to-Datalog builders, card interpretation, Python engine bridge, and package staging. It is a targeted rules-update review, not an exhaustive audit of every game mechanic or the separate Arena integration.

The repository root is tooling, not an installable Python package. `packages/mtg` is the `python-mtg` distribution; it ships generated Datalog, card data, and a native engine. Replacing `rules.txt` alone does not update the installed engine. Generated files and implementation code have not been changed by this review.

## Confirmed findings, in priority order

### 1. P1 — Refreshing the keyword roster breaks Power-up and Teamwork

Locations: `interpreter/transpile_card.py:2980`, `interpreter/transpile_card.py:3004`, `packages/mtg/bridge_to_engine.py:2858`.

The new roster recognizes Power-up and Teamwork. That changes which card parser wins:

- Power-up previously passed through the ability-word stripper to the activated-ability parser. Once grounded, the stripper retains the prefix and the generic keyword-parameter parser consumes the entire cost/effect as a parameter. No activated ability is emitted. With the refreshed roster, the existing `tests/test_power_up.py` crashes at line 169 because the expected activation row is absent. With the old roster, all 15 checks pass.
- `Teamwork 3` previously emitted `teamwork(card, 3)`. The refreshed roster makes it emit `printed_keyword` plus `keyword_param` instead. The bridge derives `teamwork_cost` only from the dedicated `teamwork` field, so that path loses the optional cost.

Add explicit handling for these recognized keywords and preserve the bridge contract. Power-up also needs its actual cost reduction and activation limit; treating its prefix as a flavor label was already incomplete. Test freshly generated rules, freshly interpreted cards, bridge rows, and execution together.

### 2. P1 — Activated mana-ability classification is out of date

Locations: `interpreter/build_ability_kinds.py:39`, `interpreter/transpile_card.py:312`.

Rule 605.1a now excludes abilities whose costs or effects move cards to or from a library, with a replacement-effect qualification. The extractor still emits the same six criteria as before. A direct probe of `{T}, Mill a card: Add {G}.` still emits `mana_ability`, despite the library movement in its cost.

Update both the extracted criteria and the card classification, then verify stack/priority behavior and cost-payment timing. Cover library movement in costs and effects, and the rule’s self-replacement distinction. Adding a descriptive criterion alone does not fix classification.

### 3. P1 — The build gate ignores semantic conformance failures

Location: `build.py:25`.

`compile_check()` checks only the Soufflé process exit status. It never reads `conformance_fail.csv`, despite the stronger guarantee described in `validate.py`. The latest `sba_extra` output compiles with exit status 0 while reporting `sba / battle / defense_zero` as a conformance failure. Running the actual `compile_check()` function against that artifact returns `True`. The build gate therefore cannot catch this demonstrated regression.

Use a separate output directory for each artifact and fail on nonempty conformance output. Make missing Soufflé a failure in the required validation environment. Add a negative gate test using a program that compiles but deliberately fails conformance.

### 4. P2 — Rewording and renumbering silently remove rule facts

Locations: `interpreter/build_sba_extra.py:36`, `interpreter/build_battle.py:55`, `interpreter/build_conditionals_extra.py:87`.

- The battle zero-defense extractor requires the old wording at 704.5v. The new Siege/non-Siege split matches neither that pattern nor a new alternative. Its fact disappears and its conformance check fails. The general battle-properties extractor also reduces the distinct cases to one unqualified property.
- The spell-controller ordering rule moves from 601.6b to 601.7b. The hardcoded lookup loses `ordered_resolution("spell_controller", "other_player")`. Its current conformance checks still pass.

Represent the battle cases separately, including protector selection. Replace fragile numeric lookups with scoped content matching where practical, and report previously extracted facts that disappear. These particular tables are descriptive; their failure demonstrates information loss, not by itself a reproduced runtime battle error.

## Remaining implementation work

The refreshed heading indexes automatically discover Heal, Recruit, Power-up, Teamwork, and Storied. Recognition is not execution. Prioritize:

| Area | Required follow-through |
| --- | --- |
| Heal / Recruit | Add and test effect-handler encoding and execution; no dedicated handlers were found. |
| Storied | Implement the enduring-story designation and its persistence/timing. A recognized keyword alone does not provide this. |
| Hone / worthy | Add the equipment-counter interaction and creature predicate. |
| Vibranium | Token characteristics are extracted, but the token-definition builder ignores abilities. Verify indestructible and restricted mana production through token creation and spending. |
| Existing changed mechanics | Regression cases for conditional flash during casting, reflexive triggers with repeated payment, connive zero and last-known information, crew attribution, failed face-down transformations, and individually optional casting of multiple copies. These were identified from the document delta, not exhaustively exercised here. |

After the fixes: regenerate all rules artifacts, regenerate card facts against the new vocabulary, run semantic conformance and affected engine tests, then rebuild/stage the native engine and wheel data. Include the rules version/hash in release provenance so source text, generated data, and shipped binaries can be checked for consistency.

## Verification

- Both documents split successfully. An independent scan of numbered rule headings exactly matches the parsed IDs, with no duplicates or missing rules, including `704.5aa`.
- Rule/subrule entries: **3,138 → 3,162**. By identifier: **35 added, 11 removed, 42 changed**. Renumbering accounts for some changes; these are not counts of distinct semantic changes. Glossary entries: **730 → 739**.
- Nine selected builders were evaluated against both versions. All 18 outputs compiled. The old outputs passed their conformance checks; the latest `sba_extra` failed. `conditionals_extra` silently lost the ordering fact while passing.
- Existing tests against the repository’s old generated artifacts: keyword actions **24/24**, non-mana costs **55/55**, Teamwork **5/5**, Power-up **15/15**. These 99 passing checks do not establish compatibility with refreshed artifacts.
- An isolated refreshed-vocabulary probe reproduced the Teamwork output change and the Power-up test failure. The mana classification probe reproduced the invalid classification described above.
- A full two-pass build was started in a scratch directory and stopped before completion after the targeted probes established the failures above. Full regeneration, native-engine rebuilding, and the full test suite remain unverified.
- `git diff --check` flags upstream CRLF/trailing whitespace in the downloaded rules. The file is intentionally byte-for-byte identical to Wizards’ download rather than reformatted.

Scratch comparison outputs and logs are under `/tmp/mtg-rules-review/`; they are temporary. The previous source remains available through Git.
