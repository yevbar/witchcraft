"""effect_handlers/army_populate.py — TOKEN-making §701 KEYWORD ACTIONS that reduce to the driver's existing
token / counter primitives (D._create_token / D._bump_counter). Three verbs, each faithful-or-abstain:

  * amass Orcs/Zombies N (§701.45) — if you control no Army, create a 0/0 black Orc/Zombie Army creature
    token; THEN put N +1/+1 counters on an Army you control. The Army is tracked by the token's 'army'
    subtype (so a later amass grows the SAME Army, never a second one), and the orc/zombie subtype lets the
    matching tribal lords reach it. Built via the explicit P/T spec '0_0_black_<tribe>_army_creature' rather
    than a named token_defs entry, because the driver's named-token path drops subtypes and amass NEEDS the
    army subtype to find the Army for the counter step.
  * populate (§701.33) — create a token that's a copy of a CREATURE TOKEN you control. With opaque ids we
    can't deep-copy an arbitrary token's full characteristics, but a token MADE by _create_token records the
    spec in its id ('<spec>#<n>'), so we faithfully recreate the SAME spec — a true copy of one of your
    creature tokens. Choose which via _choose (drivable; in imperfect info a token is PUBLIC). No creature
    token -> a legal no-op.
  * explore (§701.40) — a creature explores: reveal the top card of its controller's library; a LAND goes to
    hand; otherwise put a +1/+1 counter on the creature and you MAY bin that card (via _choose; default keep
    on top). A kept-on-top card stays KNOWN to the explorer's controller only (state['_known_top'] -> observe
    exposes library_top to that seat alone), which is exactly explore's lasting §708 information: you saw your
    own top card. (The momentary all-player reveal of §701.40a isn't persisted — a standing `revealed` row
    would over-share the card forever.) Tokens and counters are public.

Tokens and counters are PUBLIC game objects, so the created Army / populated token / +1/+1 counters read
identically in perfect and imperfect information (observe.py keeps battlefield + counters visible to all
seats). Only explore's library-top peek is private to the explorer. See effect_handlers/__init__.py for the
@encoder/@applier contract and effect_handlers/library.py (_order / _known_top) for the library-top pattern.
"""

from __future__ import annotations

from effect_handlers import encoder, applier

# target slugs denoting the CONTROLLER / the source creature acting on its OWN side. A board-scope target
# ('each_merfolk_creature_you_control' on Hakbal) is NOT a single self-explore and abstains here.
_SELF_TGT = {"you", "controller", "self", "it", "-", ""}

# amass tribe slug -> the creature subtype the Army token carries (alongside 'army'). Other slugs abstain.
_AMASS_TRIBE = {"orcs": "orc", "zombies": "zombie", "slivers": "sliver"}


def _int(amt, default=1) -> int | None:
    """A plain non-negative integer amount, else None. Variable amasses ('X_the_number_of_cards_in_your_hand',
    "X_that_spell_s_mana_value") abstain — the engine carries no live count for the counter step."""
    s = str(amt)
    return int(s) if s.isdigit() else None


def _armies_of(state: dict, ctrl: str) -> list:
    """The Army permanents `ctrl` controls (battlefield objects with the 'army' subtype), canonical order."""
    on_bf = {c for (c,) in state.get("on_battlefield", set())}
    mine = {c for (p, c) in state.get("printed_control", set()) if p == ctrl}
    return sorted(c for (c, st) in state.get("printed_subtype", set())
                  if st == "army" and c in mine and c in on_bf)


# ─────────────────────────────────────────────────────────────────────────────
# amass Orcs/Zombies N (§701.45)
# ─────────────────────────────────────────────────────────────────────────────
@encoder("amass")
def _encode_amass(verb, amt, tgt, extra):
    n = _int(amt)
    tribe = _AMASS_TRIBE.get(str(extra))
    # 'amass N' with no named tribe is faithful (a generic Army); a named tribe must be one we model. A
    # variable count abstains (no live count to feed the counter step).
    if n is None or str(tgt) not in _SELF_TGT:
        return None
    if str(extra) not in ("-", "") and tribe is None:
        return None
    return ("amass", n, tribe or "")


@applier("amass")
def _apply_amass(D, state, a, n, tgt, src, ctrl):
    """§701.45 — if `ctrl` controls no Army, create a 0/0 black Army creature token (with the named tribe's
    subtype, if any); then put n +1/+1 counters on an Army it controls (chosen via _choose; default the
    canonical-first). The token rides D._create_token, so token doublers (§614) apply; the counters ride
    D._bump_counter, so counter doublers apply."""
    armies = _armies_of(state, ctrl)
    if not armies:                                            # §701.45a no Army -> create the 0/0 Army token
        tribe = str(tgt)                                     # encoder packed the tribe subtype in the target
        subs = (tribe + "_") if tribe else ""
        spec = f"0_0_black_{subs}army_creature"
        D._create_token(state, spec, ctrl, 1)
        armies = _armies_of(state, ctrl)
    if not armies:                                           # defensive: token-doubler 0 etc. (shouldn't happen)
        print(f"    {a}: {ctrl} amasses {n} but controls no Army")
        return
    army = D._choose(state, "amass_army", armies, armies[0])
    D._bump_counter(state, army, "p1p1", int(n))
    print(f"    {a}: {ctrl} amasses {n} -> {army} (Army) gets {n} +1/+1 counter(s)")


# ─────────────────────────────────────────────────────────────────────────────
# populate (§701.33) — copy a creature TOKEN you control
# ─────────────────────────────────────────────────────────────────────────────
@encoder("populate")
def _encode_populate(verb, amt, tgt, extra):
    if str(tgt) not in _SELF_TGT:                            # 'x_times' (Full Flowering) etc. abstain — no live count
        return None
    return ("populate", 0, "controller")


def _creature_tokens_of(state: dict, ctrl: str) -> list:
    """The CREATURE token permanents `ctrl` controls, as (id, spec) — spec recovered from the token id
    ('<spec>#<n>' built by D._create_token), canonical order. Only tokens with a recoverable spec qualify
    (so populate can faithfully recreate the same characteristics)."""
    on_bf = {c for (c,) in state.get("on_battlefield", set())}
    mine = {c for (p, c) in state.get("printed_control", set()) if p == ctrl}
    toks = {c for (c,) in state.get("is_token", set())}
    creatures = {c for (c, t) in state.get("printed_type", set()) if t == "creature"}
    out = []
    for c in sorted(toks & mine & creatures & on_bf):
        spec, sep, _n = c.rpartition("#")
        if sep and spec:
            out.append((c, spec))
    return out


@applier("populate")
def _apply_populate(D, state, a, n, tgt, src, ctrl):
    """§701.33 — create a token that's a copy of a creature token `ctrl` controls. We recreate the SAME spec
    (recovered from the chosen token's id), a faithful copy of its characteristics; no creature token is a
    legal no-op. The copy rides D._create_token (so token doublers / summoning sickness apply)."""
    cands = _creature_tokens_of(state, ctrl)
    if not cands:
        print(f"    {a}: {ctrl} populates but controls no creature token")
        return
    ids = [c for (c, _s) in cands]
    chosen = D._choose(state, "populate_copy", ids, ids[0])
    spec = next(s for (c, s) in cands if c == chosen)
    D._create_token(state, spec, ctrl, 1)
    print(f"    {a}: {ctrl} populates -> a copy of {chosen} ({spec})")


# ─────────────────────────────────────────────────────────────────────────────
# dyn_create_token (§111) — 'create N tokens FOR EACH <a game quantity>'
# ─────────────────────────────────────────────────────────────────────────────
@applier("dyn_create_token")
def _apply_dyn_create_token(D, state, a, n, tgt, src, ctrl):
    """§111 create (base × live-count) tokens of a fixed spec, the count read at resolution. The bridge
    (_resolved_effect) emits this only for a CLEAN spec (P/T spec or a known token_def) paired with a count
    slug driver._dyn_count resolves — REUSING the §STRUCTURAL-#3 count vocabulary (opponents / creature_yc /
    artifact_yc / land_yc / cards_in_hand / creature_cards_in_gy). `tgt` is 'tag|spec'; `n` is the base
    multiplier (1). A zero count creates nothing (correct). The tokens ride D._create_token, so token doublers
    (§614) and summoning sickness apply, and they're PUBLIC (observe.py shows them to every seat)."""
    tag, _, spec = str(tgt).partition("|")
    count = D._dyn_count(state, tag, ctrl)
    total = int(n) * count
    if total > 0:
        D._create_token(state, spec, ctrl, total)
    print(f"    {a}: {ctrl} creates {total} {spec} token(s) (= {n}× {count} {tag})")


# ─────────────────────────────────────────────────────────────────────────────
# explore (§701.40) — a creature explores
# ─────────────────────────────────────────────────────────────────────────────
@encoder("explore")
def _encode_explore(verb, amt, tgt, extra):
    # only the SELF case (the source creature explores). A board-scope explore ('each Merfolk you control')
    # needs a per-creature loop over a derived board set the engine can't player-scope here -> abstain.
    if str(tgt) not in _SELF_TGT:
        return None
    return ("explore", 1, "self")


@applier("explore")
def _apply_explore(D, state, a, n, tgt, src, ctrl):
    """§701.40 — `src` (the exploring creature) reveals the top card of `ctrl`'s library. A LAND goes to
    ctrl's hand; anything else puts a +1/+1 counter on `src` and ctrl MAY put that card into the graveyard
    (via _choose; default keep on top). §701.40a's reveal is momentary, so we don't persist it as a public
    `revealed` row (that would over-share a kept-on-top card forever); instead we record explore's lasting
    §708 information — a kept-on-top card stays KNOWN to ctrl ONLY (state['_known_top'] -> observe library_top
    for that seat). An empty library is a faithful no-op."""
    lib = state.setdefault("_lib_order", {})
    if ctrl not in lib:                                       # materialize the ordered library (top = index 0)
        lib[ctrl] = sorted(c for (p, c) in state.get("in_library", set()) if p == ctrl)
    order = lib[ctrl]
    if not order:
        print(f"    {a}: {ctrl}'s library is empty — {src} explores into nothing")
        return
    top = order[0]

    def _pop_known_top():                                     # the explored top is leaving the library
        kt = state.setdefault("_known_top", {}).get(ctrl)
        if kt and kt[0] == top:
            kt.pop(0)

    if (top, "land") in state.get("printed_type", set()):    # a LAND -> into ctrl's hand
        order.pop(0)
        state.setdefault("in_library", set()).discard((ctrl, top))
        state.setdefault("in_hand", set()).add((ctrl, top))
        _pop_known_top()
        print(f"    {a}: {src} explores -> reveals {top} (land) into {ctrl}'s hand")
        return
    # not a land: +1/+1 counter on the explorer, then MAY bin the revealed card (default keep on top).
    D._bump_counter(state, src, "p1p1", 1)
    bin_it = D._choose(state, "explore_bin", [True, False], False)
    if bin_it:
        order.pop(0)
        state.setdefault("in_library", set()).discard((ctrl, top))
        state.setdefault("graveyard", set()).add((top,))
        _pop_known_top()
        print(f"    {a}: {src} explores -> +1/+1 counter, puts {top} into the graveyard")
    else:                                                     # kept on top -> ctrl now KNOWS its top card (§708)
        known = state.setdefault("_known_top", {}).setdefault(ctrl, [])
        if top not in known:
            known.insert(0, top)
        print(f"    {a}: {src} explores -> +1/+1 counter, keeps {top} on top (now known)")
