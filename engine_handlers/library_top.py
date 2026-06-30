"""engine_handlers/library_top.py — VERBS: scry, surveil, look, put_on_top, put_on_bottom.

Top-of-library manipulation (§701.17 scry, §701.42 surveil, §120/§701 look-and-arrange). The engine's
library is a list where the TOP is library[-1] (pop() draws). n = how many cards.
  - scry n      : look at top n; the AI keeps good cards on top, bottoms lands it's flooded on (a simple,
                  deterministic heuristic is fine — keep it cheap and faithful to "may put on bottom").
  - surveil n   : look at top n; put any number into the graveyard, rest back on top.
  - look n      : reveal/look at top n (often paired with a follow-up effect) — at minimum, a no-op-safe
                  peek; only mutate if the effect clearly says to reorder/move.
  - put_on_top / put_on_bottom : move the named card(s) (from hand/graveyard/reveal per tgt/extra) to the
                  top/bottom of the owner's library.

Faithful-or-no-op: if the destination/source can't be resolved, do nothing. Keep AI choices
deterministic (use game.rng or a fixed heuristic) so the demo stays reproducible.

Owner: ONE agent. Implement and @register(...) each verb. Helpers/signature: engine_handlers/__init__.py.

MODELS (deterministic, no game.rng needed — a fixed land-vs-nonland rule keeps the demo reproducible):
  library layout: top = library[-1], bottom = library[0]. To bottom a card we move it to index 0; to
  top it we append to the end. The top n cards (in top->down order) are library[-1], library[-2], ...

  scry n / surveil n: the controller is `pl` (tgt == "you"). We look at the top n cards. The standard
  "am I flooded?" heuristic: a LAND on top is undesirable once we already control >=4 lands (mana flood),
  so scry sends those lands to the bottom and surveil sends them to the graveyard; non-lands (and lands
  while still ramping) stay on top, preserving their relative order. This is faithful to "may put on
  bottom / into graveyard" — keeping everything is always legal, and the rule is fixed so it's
  reproducible. Order among kept cards is preserved; bottomed/binned cards keep relative order too.

  look n: a no-op-safe peek. We log what we see (top->down) but never reorder/move — the slug carries no
  reorder/move instruction (look's followups are separate effects), so mutating would be a guess.

  put_on_top / put_on_bottom: only fire when the SOURCE zone is resolvable to concrete card objects.
  The only zone these slugs name unambiguously is a graveyard ("..._from_a_graveyard" /
  "..._from_your_graveyard"): we pull the matching card(s) from that graveyard and move them to the
  owner's library top/bottom. Every other spec ("them", "that_card", "self", "target_creature",
  "two_cards", "any_order"…) refers to a prior-effect object / reveal pile this minimal engine never
  tracked, so source is unresolvable -> abstain (faithful no-op).
"""

from __future__ import annotations

from engine_handlers import register


def _flooded(pl, threshold=4):
    """Deterministic 'I have enough lands' test driving scry/surveil disposal of lands."""
    return sum(1 for p in pl.bf if p.card.is_land) >= threshold


def _top_n(pl, n):
    """The top n cards as a list in top->down order (library[-1] first). Fewer if the library is short."""
    if n <= 0:
        return []
    return list(reversed(pl.library[-n:]))


@register("scry")
def scry(game, pl, opp, amt, tgt, extra, source, n):
    """§701.17 — look at the top n; bottom the lands we're flooded on, keep the rest on top in order."""
    if n <= 0 or not pl.library:
        return
    top = _top_n(pl, n)                       # top->down
    k = len(top)
    del pl.library[len(pl.library) - k:]      # lift the looked-at cards off the top
    flooded = _flooded(pl)
    keep, bottom = [], []
    for c in top:                             # top->down
        (bottom if (flooded and c.is_land) else keep).append(c)
    # bottom: prepend (cards go UNDER the library), preserving their relative order
    pl.library[0:0] = list(reversed(bottom))
    # keep: put back on top so keep[0] (highest priority) ends up on top (library[-1])
    pl.library.extend(reversed(keep))
    game.log(f"{pl.name} scries {k} (kept {len(keep)} on top, {len(bottom)} to bottom)", 2)


@register("surveil")
def surveil(game, pl, opp, amt, tgt, extra, source, n):
    """§701.42 — look at the top n; mill the lands we're flooded on into the graveyard, rest back on top."""
    if n <= 0 or not pl.library:
        return
    top = _top_n(pl, n)                       # top->down
    k = len(top)
    del pl.library[len(pl.library) - k:]
    flooded = _flooded(pl)
    keep, bin_ = [], []
    for c in top:
        (bin_ if (flooded and c.is_land) else keep).append(c)
    pl.grave.extend(bin_)
    pl.library.extend(reversed(keep))         # keep[0] back on top
    game.log(f"{pl.name} surveils {k} (kept {len(keep)} on top, {len(bin_)} to graveyard)", 2)


@register("look")
def look(game, pl, opp, amt, tgt, extra, source, n):
    """§120 — a no-op-safe peek at the top n of the named player's library. Never reorders/moves: the
    look slug carries no move instruction, so any mutation would be a guess. Faithful = just log."""
    if n <= 0:
        return
    who = opp if ("opponent" in tgt or tgt.startswith(("target_player", "that_player"))) else pl
    if not who.library:
        return
    seen = _top_n(who, n)
    names = ", ".join(c.name for c in seen) or "(none)"
    game.log(f"{pl.name} looks at the top {len(seen)} of {who.name}'s library: {names}", 2)


def _gy_source_side(pl, opp, tgt):
    """Which graveyard a put_on_* spec names, or None if the source zone isn't a resolvable graveyard."""
    if "graveyard" not in tgt:
        return None
    if "your_graveyard" in tgt:
        return pl
    if "opponent" in tgt or "their_graveyard" in tgt:
        return opp
    return pl                                 # 'a_graveyard' / unqualified -> the controller's own


def _matches(card, tgt):
    """Faithful type filter from a 'target_<type>_card_from_..._graveyard' spec; no type word -> any card."""
    from mtg.engine.engine import _PERM_TYPES
    types = {_PERM_TYPES[w] for w in tgt.split("_") if w in _PERM_TYPES}
    return not types or bool(card.types & types)


def _move(game, pl, opp, tgt, to_top):
    """Shared put_on_top/put_on_bottom: pull the matching card(s) from the named graveyard onto the
    owner's library top/bottom. Abstains (no-op) when the source zone isn't a resolvable graveyard."""
    src = _gy_source_side(pl, opp, tgt)
    if src is None:                           # 'them'/'that_card'/'self'/reveal pile — not tracked, abstain
        return
    cands = [c for c in src.grave if _matches(c, tgt)]
    if not cands:
        return
    # single 'target_card' spec moves one (deterministic: the first match in graveyard order); a mass
    # spec would move all, but graveyard put_on_* slugs are single-target in this corpus.
    mass = any(w in tgt for w in ("all", "each", "every"))
    chosen = cands if mass else cands[:1]
    where = "top" if to_top else "bottom"
    for c in chosen:
        src.grave.remove(c)
        if to_top:
            pl.library.append(c)              # top = end of list
        else:
            pl.library.insert(0, c)           # bottom = front of list
        game.log(f"{pl.name} puts {c.name} on the {where} of their library", 2)


@register("put_on_top")
def put_on_top(game, pl, opp, amt, tgt, extra, source, n):
    _move(game, pl, opp, tgt, to_top=True)


@register("put_on_bottom")
def put_on_bottom(game, pl, opp, amt, tgt, extra, source, n):
    _move(game, pl, opp, tgt, to_top=False)
