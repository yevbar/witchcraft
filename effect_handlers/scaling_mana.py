"""effect_handlers/scaling_mana.py — §106.x SCALING-MANA mechanic (the 'equal_to' arm).

WHAT THIS OWNS — the dynamic-amount mana ability whose size is a LIVE COUNT read at resolution:
    "Add an amount of {C} equal to <a game quantity>"   (40ca209 shapes 1+2)
        -> card_effect add_mana(equal_to_<slug>, you, <color>)
The bridge already routes the SIBLING '<N> mana FOR EACH <thing>' shape (add_mana <N>_per_<slug>) to the
driver's existing `dyn_mana` applier (bridge `_mana_qty`; effect_handlers/library `_apply_dyn_mana`). The
'equal_to_<slug>' arm had no route — it fell through to the fixed-amount add_mana encoder, which abstains on
a non-integer amount, so EVERY 'amount equal to' mana ability DROPPED. This file wires it.

THE SPLIT WITH library.add_mana: the add_mana ENCODER (library._encode_add_mana) detects the 'equal_to_'
prefix and delegates here via `encode_scaled_mana` (one import, no second @encoder('add_mana')). When that
returns a clean (tag, color) it emits a `scaled_mana` effect; otherwise it falls through to the existing
fixed/abstain logic. The `scaled_mana` APPLIER lives here and computes the count + adds the mana.

FAITHFUL-OR-ABSTAIN — we resolve a 'equal_to' count ONLY when:
  * the COLOR is a single concrete WUBRG-or-colorless color (NOT a choice rider — 'any color' / 'any one
    color' would let the player pick, which we can't), and
  * the target is the caster's own pool ('you'/'controller'/'self'/'it'/'-'), and
  * the count TAG is one we can resolve from PUBLIC board / the controller's own zones / counters on the
    source — reusing driver._dyn_count's vocabulary where it fits, plus a few mana-specific dims.
We ABSTAIN (return None -> the clause drops, never a WRONG amount) on:
  * EVENT-CONTEXT counts — 'the sacrificed creature's mana value', 'that spell's mana value', 'that
    creature's power', '<N> plus the …' — no event payload reaches a player-scoped resolution here;
  * the SOURCE's own power/toughness ('equal_to_s_power', 'marwyn_s_power', 'its_power', 'vhal_s_toughness')
    — those are modeled as a `source_dyn_power` mana SOURCE by the bridge, not as a one-shot add;
  * OPPONENT-scoped or 'greatest among …' / 'shares a creature type' superlatives we can't count cleanly;
  * DEVOTION (needs per-permanent colored-pip counting) and any unrecognized slug.
"""

from __future__ import annotations

from effect_handlers import applier

_MANA_COLORS = {"white", "blue", "black", "red", "green", "colorless"}
_SELF_TGT = {"you", "controller", "self", "it", "-", ""}

# 'equal_to_the_number_of_<X>' count slugs we resolve. Values that name a driver._dyn_count tag are routed
# there (battlefield counts come from the engine's DERIVED controls/has_type, so casting-entered permanents
# and §613 layers are included); the rest are resolved locally in `scaled_mana_count`.
#   tag forms: 'dyn:<dyn_count_tag>'      -> reuse driver._dyn_count
#              'type:<printed_type>'      -> permanents of that printed type the controller controls
#              'counters'                 -> total counters on the SOURCE (any kind)
#              'counters:<kind>'          -> counters of one kind on the SOURCE
_EQUAL_TO_TAGS = {
    "the_number_of_creatures_you_control": "dyn:creature_yc",
    "the_number_of_artifacts_you_control": "dyn:artifact_yc",
    "the_number_of_lands_you_control": "dyn:land_yc",
    "the_number_of_cards_in_your_hand": "dyn:cards_in_hand",
    "the_number_of_creature_cards_in_your_graveyard": "dyn:creature_cards_in_gy",
    "the_number_of_enchantments_you_control": "type:enchantment",
    "the_number_of_planeswalkers_you_control": "type:planeswalker",
}


def _counter_tag(slug: str) -> str | None:
    """A '<kind>_counters_on[_it|_<name>]' slug -> a counters-on-the-SOURCE tag, else None. The trailing
    anchor is 'on', 'on_it', or 'on_<source name>'; the count is over the source regardless, so we only need
    the <kind>. 'counter(s)_on' with no kind -> all counters on the source."""
    body = slug
    if body.startswith("the_number_of_"):                   # 'the_number_of_charge_counters_on_it' -> kind+anchor
        body = body[len("the_number_of_"):]
    # strip the trailing on-anchor: '..._counters_on', '..._counters_on_it', '..._counters_on_<name>'
    if "_counters_on" in body:
        head = body.split("_counters_on", 1)[0]              # '<kind>' e.g. 'time', 'charge', 'petal'
    elif "_counter_on" in body:
        head = body.split("_counter_on", 1)[0]
    else:
        return None
    head = head.strip("_")
    if not head:
        return "counters"                                   # 'the number of counters on ~' (any kind)
    if head.isalpha():                                      # a single-word counter kind (time/charge/petal/…)
        return f"counters:{head}"
    return None                                             # multi-word / qualified -> abstain


def _resolve_tag(slug: str) -> str | None:
    """Map a bare 'equal_to' quantity slug (the part AFTER 'equal_to_') to an internal count tag, or None to
    abstain. EVENT-CONTEXT / source-P-T / superlative / opponent / devotion slugs are intentionally absent."""
    named = _EQUAL_TO_TAGS.get(slug)
    if named is not None:
        return named
    return _counter_tag(slug)


def encode_scaled_mana(amt, tgt, extra):
    """library._encode_add_mana delegate. amt is the cards.dl amount ('equal_to_<slug>'); on success returns
    ('scaled_mana', 1, '<tag>|<color>') for the driver's scaled_mana applier, else None to abstain (the
    caller then handles the fixed-amount / drop case)."""
    s = str(amt)
    if not s.startswith("equal_to_"):
        return None
    if tgt not in _SELF_TGT:                                # only the caster's own pool
        return None
    color = str(extra)
    if color not in _MANA_COLORS:                           # a color CHOICE rider (any/any_one) -> abstain
        return None
    tag = _resolve_tag(s[len("equal_to_"):])
    if tag is None:
        return None
    return ("scaled_mana", 1, f"{tag}|{color}")


def scaled_mana_count(D, state, tag: str, ctrl: str, src: str) -> int:
    """Live value of an 'equal_to' count tag for the controller, at resolution (§106). Reuses
    driver._dyn_count for its shared dimensions; resolves type/counter dims locally. Unknown -> 0."""
    kind, _, rest = tag.partition(":")
    if kind == "dyn":
        return D._dyn_count(state, rest, ctrl)
    if kind == "type":
        out = D.run(state, ["controls", "printed_type"])
        typed = {c for (c, ty) in out["printed_type"] if ty == rest}
        return sum(1 for (p, c) in out["controls"] if p == ctrl and c in typed)
    if kind == "counters" and not rest:
        return sum(cnt for (o, k, cnt) in state.get("counter", set()) if o == src)
    if kind == "counters" and rest:                        # 'counters:<kind>'
        return sum(cnt for (o, k, cnt) in state.get("counter", set()) if o == src and k == rest)
    return 0


@applier("scaled_mana")
def _apply_scaled_mana(D, state, a, n, tgt, src, ctrl):
    """§106 'add an amount of <color> equal to <count>' — evaluate the count tag against live PUBLIC board /
    the controller's own zones / counters on the source, and add (count) mana of the fixed color to the
    controller's FLOATING pool (persists across spells this step, surfaced into mana_pool for affordability).
    `tgt` is '<tag>|<color>'; `n` is the per-unit multiplier (always 1 for 'equal_to'). A 0 count adds
    nothing (correct)."""
    tag, _, color = str(tgt).rpartition("|")
    count = scaled_mana_count(D, state, tag, ctrl, src)
    total = int(n) * count
    if total > 0:
        D._add_floating(state, ctrl, {color: total})
        D._refresh_mana_pool(state, ctrl)                  # surface floating into mana_pool / mana_available
    print(f"    {a}: {ctrl} adds {total} {color} mana (= {count} {tag.replace(':', ' ')})")
