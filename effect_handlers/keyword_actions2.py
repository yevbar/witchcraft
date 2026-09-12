"""effect_handlers/keyword_actions2.py — §701 KEYWORD ACTIONS that REDUCE to primitives already resolved.

Each verb here is a §701 keyword action grounded in datalog/keyword_action_index.dl and parsed by the
spaCy+Lark vocabulary (card_effects._kwaction_n / _bare_action over ground.keyword_actions()), then dropped
because no mechanic consumed it. We map each to existing driver/handler machinery — NO engine change (every
effect is CONTROLLER-scoped, so it rides the trigger_effect -> pending path), NO new driver helpers:

  * discover N (§701.57) — exile from the top of your library until you exile a NONLAND card with mana
        value ≤ N, then either CAST it without paying its mana cost OR put it into your hand (your choice);
        the other exiled cards go to the bottom in a random order. Reuses the impulse/cast-free machinery
        (free_grant + driver._cast_spell) for the free cast and library._order for the top-of-library walk.
  * manifest_dread (§701.62) — look at the top TWO cards of your library, MANIFEST one (a face-down 2/2,
        face_down.py's feeder) and put the other into your graveyard. Composes face_down's manifest path
        with the library dig pattern; the controller CHOOSES which of the two to manifest (via _choose).
  * learn (§701.48) — 'you may reveal a Lesson card … OR you may draw a card, then discard a card'. We
        resolve the always-available RUMMAGE half (draw 1, then discard 1 of your choice). The Lesson-tutor
        half ABSTAINS (no sideboard model), which is faithful: declining the Lesson and rummaging is a legal
        choice every learn offers.
  * cant_be_regenerated (§701.15g) — flag the affected permanent in state['cant_be_regenerated'], the set
        driver._consume_regen_shield already consults so a regeneration shield can't save it. Completes the
        regeneration story (effect_handlers/regeneration.py sets the shields). Faithful on self/it (the
        source / the just-affected permanent) and on a clean targeted creature (driver-picked, via _choose).

IMPERFECT INFORMATION (see observe.py): every CHOICE routes through driver._choose (policy-drivable; a policy
reasons over its own observed view). discover's outcome (a free cast OR a card moved to hand) and the
bottomed cards are about the controller's OWN library/hand — correct under hidden info. manifest_dread's
face-down body is PUBLIC existence but its IDENTITY is hidden from opponents and KNOWN to the controller
(face_down.py records known(ctrl, card)); the binned card is a public graveyard card. cant_be_regenerated is
public state. See effect_handlers/__init__.py for the @encoder/@applier contract.
"""

from __future__ import annotations

from effect_handlers import encoder, applier
from effect_handlers.impulse import _mv_of
from effect_handlers.library import _order

# CONTROLLER-acting target slugs (the verb acts on the player's own library/hand/self). Anything else
# (another player's library, a board scope) rides a path we can't player-scope here -> abstain.
_SELF_TGT = {"you", "controller", "self", "it", "-", ""}


def _int(x, default=1):
    try:
        return int(str(x))
    except (TypeError, ValueError):
        return default


# ─────────────────────────────────────────────────────────────────────────────
# discover N (§701.57) — exile top cards until a NONLAND with mana value ≤ N is exiled; cast it free OR
# put it in hand (controller's choice); the rest go to the bottom. Reuses free_grant + driver._cast_spell
# (the cast-free path) and library._order (the ordered top walk). A whiff (no eligible card before the
# library empties) is a faithful no-op for the cast, with whatever was exiled going to the bottom.
# ─────────────────────────────────────────────────────────────────────────────
@encoder("discover")
def encode_discover(verb, amt, tgt, extra):
    n = _int(amt, None)
    if n is None or str(tgt) not in _SELF_TGT:
        return None
    return ("discover", n, "controller")


def _is_land(state: dict, c: str) -> bool:
    return (c, "land") in state.get("printed_type", set())


def _is_castable_spell(state: dict, c: str) -> bool:
    """A NONLAND card that has a spell type (so driver._cast_spell can resolve it for free)."""
    if _is_land(state, c):
        return False
    return any(s == c for (s, _t) in state.get("spell_type", set()))


@applier("discover")
def apply_discover(D, state, a, n, tgt, src, ctrl):
    cap = int(n)
    order = _order(state, ctrl)
    inlib = state.setdefault("in_library", set())
    exiled: list[str] = []                                    # the cards exiled along the way (face up)
    hit: str | None = None                                    # the discovered nonland card (mv ≤ cap)
    while order:
        c = order.pop(0)
        inlib.discard((ctrl, c))
        kt = state.get("_known_top", {}).get(ctrl)           # it left the known-top window, if any
        if kt and c in kt:
            kt.remove(c)
        if not _is_land(state, c) and _mv_of(state, c) <= cap:
            hit = c
            break
        exiled.append(c)                                      # a land or too-expensive nonland -> stays exiled
    # §701.57b the discovered card: CAST it free, OR put it into hand — the controller's choice.
    if hit is not None:
        castable = _is_castable_spell(state, hit)
        mode = D._choose(state, "discover_mode", ["cast", "hand"] if castable else ["hand"],
                         "cast" if castable else "hand")
        if mode == "cast" and castable:
            state.setdefault("exile", set()).add((hit,))     # it is on its way through exile when cast
            state.setdefault("free_grant", set()).add((ctrl, hit))
            state.setdefault("may_play", set()).add((ctrl, hit))   # castable from exile
            players = [ctrl] + [p for (p,) in sorted(state.get("is_player", set())) if p != ctrl]
            print(f"    {a}: {ctrl} discovers {cap} -> casts {hit} for free (§701.57)")
            D._cast_spell(state, ctrl, hit, players)
            state.get("free_grant", set()).discard((ctrl, hit))
            state.get("may_play", set()).discard((ctrl, hit))
        else:
            state.setdefault("in_hand", set()).add((ctrl, hit))
            print(f"    {a}: {ctrl} discovers {cap} -> puts {hit} into hand (§701.57)")
    else:
        print(f"    {a}: {ctrl} discovers {cap} but finds no nonland card (mana value <= {cap})")
    # §701.57b the rest (the cards exiled along the way) go on the BOTTOM of the library in a random order.
    for c in exiled:
        inlib.add((ctrl, c))
        order.append(c)
    if exiled:
        print(f"    {a}: {ctrl} puts {len(exiled)} exiled card(s) on the bottom of their library")


# ─────────────────────────────────────────────────────────────────────────────
# manifest_dread (§701.62) — look at the top TWO cards, MANIFEST one (face-down 2/2) and bin the other.
# Composes face_down.py's manifest feeder (face_down + known + _sick) with the library dig pattern. The
# controller CHOOSES which of the top two to manifest (via _choose); the other goes to the graveyard. With
# one card left in the library you manifest that one (nothing to bin); an empty library is a no-op.
# ─────────────────────────────────────────────────────────────────────────────
@encoder("manifest_dread")
def encode_manifest_dread(verb, amt, tgt, extra):
    if str(tgt) not in _SELF_TGT:
        return None
    return ("manifest_dread", 0, "controller")


def _manifest_card(state: dict, ctrl: str, card: str) -> None:
    """§708.2 put `card` onto the battlefield FACE DOWN as a 2/2 (face_down.py's manifest body): face_down
    (engine derives the 2/2 colorless body) + known(ctrl) (the controller knows the real card) + _sick."""
    state.setdefault("on_battlefield", set()).add((card,))
    from mtg.rules_2026 import entered
    entered(state, card)
    state.setdefault("printed_control", set()).add((ctrl, card))
    state.setdefault("face_down", set()).add((card,))
    state.setdefault("known", set()).add((ctrl, card))
    state.setdefault("_sick", set()).add((card,))


@applier("manifest_dread")
def apply_manifest_dread(D, state, a, n, tgt, src, ctrl):
    order = _order(state, ctrl)
    inlib = state.setdefault("in_library", set())
    top = order[:2]
    if not top:
        print(f"    {a}: {ctrl}'s library is empty (manifest dread fizzles)")
        return
    # the controller chooses which of the (up to) two to manifest; the other is binned.
    pick = D._choose(state, "manifest_dread_pick", list(top), top[0])
    if pick not in top:
        pick = top[0]
    for c in top:                                            # pull both looked-at cards out of the library
        if c in order:
            order.remove(c)
        inlib.discard((ctrl, c))
        kt = state.get("_known_top", {}).get(ctrl)
        if kt and c in kt:
            kt.remove(c)
    _manifest_card(state, ctrl, pick)
    other = next((c for c in top if c != pick), None)
    if other is not None:
        state.setdefault("graveyard", set()).add((other,))
    print(f"    {a}: {ctrl} manifests dread — manifests one of top {len(top)} (face down 2/2)"
          + (f", puts {other} into the graveyard" if other is not None else ""))


# ─────────────────────────────────────────────────────────────────────────────
# learn (§701.48) — the RUMMAGE half: 'you may draw a card, then discard a card'. The Lesson-tutor half
# (reveal a Lesson card from outside the game) abstains (no sideboard model) — declining it and rummaging
# is a legal choice learn always offers. The whole rummage is OPTIONAL ('you may'); the discard is from
# the controller's own hand (its choice). A draw on an empty library still resolves (no card to draw).
# ─────────────────────────────────────────────────────────────────────────────
@encoder("learn")
def encode_learn(verb, amt, tgt, extra):
    if str(tgt) not in _SELF_TGT:
        return None
    return ("learn", 0, "controller")


@applier("learn")
def apply_learn(D, state, a, n, tgt, src, ctrl):
    if not D._choose(state, "learn_rummage", [True, False], True):
        print(f"    {a}: {ctrl} declines to learn (rummage)")
        return
    D._draw(state, ctrl)
    hand = sorted(c for (p, c) in state.get("in_hand", set()) if p == ctrl)
    if not hand:
        print(f"    {a}: {ctrl} learns (drew, but has no card to discard)")
        return
    card = D._choose(state, "learn_discard", hand, hand[0])
    state["in_hand"].discard((ctrl, card))
    state.setdefault(D._discard_zone(state, ctrl), set()).add((card,))
    print(f"    {a}: {ctrl} learns — draws a card, then discards {card}")


# ─────────────────────────────────────────────────────────────────────────────
# cant_be_regenerated (§701.15g) — flag the affected permanent so a regeneration shield can't save it. The
# applier adds it to state['cant_be_regenerated'], the SAME set driver._consume_regen_shield checks (no
# driver edit; it already reads this key). Faithful on self/it (the source / the just-destroyed 'it'/'them')
# and on a clean targeted creature (driver-picked among opponents' creatures — the disruptive use of a
# 'destroy ~; it can't be regenerated' rider). Subtyped/board/conditional shapes abstain.
# ─────────────────────────────────────────────────────────────────────────────
_SELF_REGEN = {"self", "it", "they", "them", "-", "", "a_creature_destroyed_this_way",
               "that_creature", "the_creature"}
_TGT_CREATURE = {"target_creature", "creature", "target_creature_an_opponent_controls",
                 "target_creature_you_dont_control"}


@encoder("cant_be_regenerated")
def encode_cant_be_regenerated(verb, amt, tgt, extra):
    t = str(tgt)
    if t in _SELF_REGEN:
        return ("cant_be_regenerated", 0, "self")
    if t in _TGT_CREATURE:
        return ("cant_be_regenerated", 0, "target_creature")
    return None                                              # subtyped/board/conditional -> abstain


@applier("cant_be_regenerated")
def apply_cant_be_regenerated(D, state, a, n, tgt, src, ctrl):
    """§701.15g add the affected permanent to state['cant_be_regenerated'] (the set the destroy chokepoint's
    _consume_regen_shield already reads). 'self'/'it' = the source; 'target_creature' = a driver-picked
    creature (the strongest creature an opponent controls — the disruptive default), via the _choose seam."""
    target = src
    if str(tgt) == "target_creature":
        out = D.run(state, ["controls", "creature", "power"])
        creatures = {c for (c,) in out["creature"]}
        on_bf = {c for (c,) in state.get("on_battlefield", set())}
        powers = {c: int(x) for (c, x) in out["power"]}
        mine = {c for (p, c) in out["controls"] if p == ctrl}
        cands = sorted(c for c in creatures if c in on_bf and c not in mine) \
            or sorted(c for c in creatures if c in on_bf)
        if not cands:
            print(f"    {a}: no creature to mark can't-be-regenerated")
            return
        target = D._choose(state, "cant_regen_target", cands, max(cands, key=lambda c: (powers.get(c, 0), c)))
    state.setdefault("cant_be_regenerated", set()).add((target,))
    print(f"    {a}: {target} can't be regenerated this turn (§701.15g)")
