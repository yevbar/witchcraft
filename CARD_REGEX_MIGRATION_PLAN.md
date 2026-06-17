# Card-regex → lark migration: pre-implementation scoping

Concrete shape of the work from `CARD_REGEX_AUDIT.md`, measured over the full corpus (lark-first routing;
`migrate_check`-style leaf diff). **Prerequisite is already met: `DIFFERS = 0` across every family** — lark
never disagrees with the regex where both ground, so there is NO bug-reconciliation phase (the old
grant_keyword/modify_pt/grant_ability "latent DIFFERS" are fixed). Migration is purely additive grammar work.

## The regex-served clauses split THREE ways (not two)

The audit's "clean" bucket actually divides — the change-shape and the validation gate differ per bucket:

| bucket | ~count | what it is | change | gate |
|--------|-------:|------------|--------|------|
| **flip-identical** | ~2,200 | regex tuple is fine; lark just lacks the rule | add a `card_lark` production + `_ToEffect` method that reproduces the tuple | `migrate_check <v>`: DIFFERS stays 0, ABSTAINS↓ |
| **improve-split** | ~400 | regex folded a 2nd clause / a for-each INTO the target (lossy) — `any_target_and_you_gain_3_life`, `target_opponent_loses_2_life_and_you`, `for_each_creature_that_died_this_turn` | lark grounds it BETTER: split the conjunction into multiple effects / demote for-each to `cond` | inspection (it CHANGES cards.dl, lossy→faithful — not byte-identical) |
| **lossy-amount** | ~830 | computed amount (`_per_`/`equal_to`/`X`) | lark rule grounds the verb + records the computed span as `cond`/`extra`; retire the lossy regex | faithful-abstain (no amount invented) |

So "migrate the 88% clean" is really: **~2,200 reproduce-the-tuple grammar adds (safe, no fact change), ~400
faithful improvements (change a lossy fact to split/demoted), ~830 demote-to-abstain.** A verb's
`card_effects._TEMPLATES` entry is deletable only at per-verb `ABSTAINS = 0`.

## Per-family triage (flip / improve / lossy), ordered by leverage

PUREST FIRST (high flip, ~0 lossy, ~0 improve → reaches ABSTAINS=0 cleanly → template deletes):

| family | flip | improve | lossy | served | note |
|--------|----:|----:|----:|----:|------|
| **prevent_damage** | 140 | 0 | 0 | 28 | purest; dedicated templates → deletable. **Do first.** |
| **return_to_battlefield** | 190 | 19 | 0 | 107 | lark barely covers it today — big coverage shift |
| **return_to_hand** | 87 | 22 | 0 | 273 | |
| **becomes** | 196 | 3 | 1 | 134 | nearly pure |
| **grant_keyword** | 210 | 5 | 0 | 670 | huge — but CHECK removability (may share catch-alls) |
| **reveal / choose / grant_ability / sacrifice / exile** | 77/78/36/33/86 | small | ~0 | — | clean mid-size |
| **create** | 226 | 0 | 98 | 965 | big flip, but 98 lossy stay abstain → won't reach ABSTAINS=0 |
| **put_counter** | 141 | 41 | 87 | 668 | mixed |
| **deal_damage** | 156 | 125 | 105 | 1083 | most improve-split (compound burn+gain/drain) |

MOSTLY-LOSSY (low value — keep-abstain/demote, little faithfulness gain): gain_life (2/26/153),
draw (24/7/123), modify_pt (11/0/69), lose_life (21/8/47), mill (7/0/15).

LONG TAIL (~50 verbs, 1–33 flip each, almost all pure-clean): lose_abilities 33, copy 30, put_in_graveyard 30,
put_in_hand 26, must_attack 25, redirect_damage 17, phase_out 17, must_block 15, skip 14, amass 14, … — these
fully migrate + delete their (often single) template in one small commit each.

## Worked example — what ONE family's change looks like (prevent_damage, the first target)

Today: `card_lark` has a TRUE-grammar `pvclause` (`PVPREVENT pvpre DMG pvtail`, the `_fog`/`_prevent`
skeletons via anchored per-operand validators `_PV_*`); `card_effects` has DEDICATED templates `_prevent` /
`_fog` / `_prevent_that` / `_prevent_scope`. The 140 abstains are `pvtail` variants the grammar doesn't carve:
- `Prevent all damage that would be dealt **to <X>**` → `prevent_damage / all / <X>`
- `Prevent all damage that would be dealt **to and dealt by** <X>` → `prevent_damage / all`
- `Prevent all damage that **<sources>** would deal …` → `prevent_damage / all`

Change = extend `pvtail` with these operand shapes + their `_PV_*` validators (same per-operand-span pattern,
NO `self._src` re-parse), each reproducing the existing `_prevent_scope` tuple. Then `migrate_check
prevent_damage` → DIFFERS 0, ABSTAINS 0 → delete the now-dead `_prevent_scope`/`_fog`/`_prevent` templates,
proven behaviour-neutral by a full-corpus `parse_clause` before/after diff = 0.

## Standing rules (from the migration memory)

- TRUE grammar productions only (distinctive terminal → operand spans → tree-read + shared `_TGT` validator).
  **No frame regex / `self._src` re-parse** — that just relocates regex and was rejected before.
- Validate every family with `python3 migrate_check.py <verb>`; **DIFFERS must be 0** before deleting anything.
- Delete a template only at per-verb `ABSTAINS = 0`; verbs grounded by SHARED catch-alls (`^(\w+) (TGT)$`,
  `_RET_DEST`, dynamic `cant_*`, `_SUBJ_OBJ_VERBS`) can't be deleted piecemeal — narrow those last.
- Abstain over a lossy fact (the lossy-amount bucket stays abstain, never invents an amount).
- Heavy-gate caution: `migrate_check` is ~3–5 min; `cards.dl` is gitignored (regenerate, `CARD_JOBS=4`).

## Recommended sequencing

1. **prevent_damage** (purest, dedicated templates) — proves the loop end-to-end incl. a template deletion.
2. The **long-tail pure-clean verbs** (lose_abilities, copy, put_in_graveyard, must_attack, redirect_damage, …)
   — each a small self-contained commit that fully migrates + deletes one template.
3. **return_to_battlefield / return_to_hand / becomes / reveal / choose / grant_ability / sacrifice / exile**
   — clean mid-size families.
4. **grant_keyword** — large; first confirm removability vs the shared catch-alls.
5. The **improve-split** work (compound clauses) as a separate, inspection-gated pass (it changes facts).
6. **lossy-amount demotion** last (lowest value; mostly gain_life/draw/modify_pt/lose_life).
