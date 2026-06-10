"""effect_handlers/players.py — PLAYER-SCOPED state & resource effects (controller / each opponent).

Own these cards.dl effect verbs (each targets a PLAYER or a resource a player owns, so they fit the
player-scoped trigger_effect path with target in {'controller','each_opponent'} and need NO engine change):
  - sacrifice  (a player sacrifices n creatures — pick the weakest deterministically; move to graveyard)
  - get_energy (an {E} energy resource a player holds, kept in a driver-side state relation)
  - win_game / lose_game  (set the game result the way the driver ends a game)
  - set_life   ('your/each opponent's life total becomes N' — a player-scoped life set)

MODEL
  sacrifice: amt is the COUNT, tgt is the class/scope. We only resolve the faithful, choice-free case
    'a player sacrifices N CREATURES of their own choosing': pick the N lowest-power creatures that player
    controls (ties broken by id) from the engine's DERIVED controls/creature/power, and move them to the
    graveyard via D._to_graveyard after firing any 'when ~ is sacrificed' look-back trigger (D._sacrifice).
    target='controller' (the controller sacrifices) or 'each_opponent' ('each opponent sacrifices a
    creature'). ABSTAIN on a SPECIFIC/named/subtyped/conditional class ('a Food', 'another Dragon', 'all
    creatures', 'any number of …', a named permanent), a non-creature class (land/artifact/permanent), and
    variable amounts ('X creatures', '1_per_…') — those need a choice or count the engine can't supply.

  get_energy: amt is a plain int -> the player gains N {E}. Stored in a driver-side state relation
    state['energy'] = {(player, total)}; read back / accumulated in apply. ABSTAIN on variable amounts.

  win_game / lose_game: the driver ends a game when a player's life is at/below the §704.5a loss
    threshold (driver.LIFE_LOSS_THRESHOLD), surfaced as the loser in _apply_outputs. So lose_game sets the
    losing player's life to that threshold; win_game (you win) makes EACH OPPONENT lose the same way (a
    two-player game with no opponents left is a win). These carry no condition — the engine doesn't model
    effect conditions (the bridge already drops every trigger's condition), so this matches that bar.

Faithful-or-abstain: encode -> None for anything we can't resolve correctly. See
effect_handlers/__init__.py for the @encoder / @applier contract and the driver helpers on D.
"""

from effect_handlers import encoder, applier


def _int(amt):
    """A plain integer amount, or None for '-', variable ('x_creatures', '1_per_…'), etc."""
    s = str(amt)
    return int(s) if s.lstrip("-").isdigit() else None


# ----- target slug -> player-scope vocabulary -------------------------------------------------------
# 'you'/'self'/'its_controller' = the ability's controller; opponent/that-player/each-player = others.
_SELF_TGT = {"you", "self", "its_controller", "controller"}


def _player_scope(tgt):
    """Map an effect target slug to 'controller' / 'each_opponent', or None if it isn't a clean,
    choice-free PLAYER scope (a specific named player, 'target player with exactly …', etc.)."""
    t = str(tgt)
    if t in _SELF_TGT:
        return "controller"
    # 'each opponent', 'each other player', 'each player', 'that player', 'target opponent/player'
    if t in ("each_opponent", "each_other_player", "each_player", "that_player",
             "target_opponent", "target_player"):
        return "each_opponent"
    return None


# ----- sacrifice ------------------------------------------------------------------------------------
# The only choice-free creature classes we resolve: a generic 'creature' the player sacrifices of their
# own choosing (we pick the weakest). A specific subtype / named / non-creature / 'all'/'any number' /
# conditional class abstains (needs a choice or selects a different permanent kind than we'd pick).
_SAC_CREATURE_CLASSES = {
    "a_creature", "another_creature", "a_nontoken_creature", "another_creature_you_control",
    "a_creature_of_their_choice", "a_nontoken_creature_of_their_choice",
}


@encoder("sacrifice")
def encode_sacrifice(verb, amt, tgt, extra):
    # amt = COUNT (default 1 when the count is implicit and the class names a single creature);
    # the class lives in tgt for controller-scoped ('Sacrifice a creature') and in extra for
    # player-targeted ('each opponent sacrifices a creature of their choice').
    if str(extra) not in ("-", "None") and _player_scope(tgt):
        scope, cls = _player_scope(tgt), str(extra)
        n = _int(amt)
        n = 1 if n is None and cls in _SAC_CREATURE_CLASSES else n
    else:
        scope, cls = "controller", str(tgt)
        n = _int(amt)
        n = 1 if n is None and cls in _SAC_CREATURE_CLASSES else n
    if scope is None or n is None or n < 1 or cls not in _SAC_CREATURE_CLASSES:
        return None
    return ("sacrifice", n, scope)


@applier("sacrifice")
def apply_sacrifice(D, state, a, n, tgt, src, ctrl):
    """The chosen player(s) sacrifice their n weakest creatures (lowest power, ties by id) — a faithful
    deterministic 'sacrifice a creature of their choice'. Uses the engine's DERIVED controls/creature/
    power so a creature that entered via casting counts, and D._sacrifice so any 'when ~ is sacrificed'
    look-back trigger fires before it leaves for the graveyard."""
    players = D._others(state, ctrl) if tgt == "each_opponent" else [ctrl]
    for p in players:
        out = D.run(state, ["controls", "creature", "power"])
        creatures = {c for (c,) in out["creature"]}
        power = {c: int(v) for (c, v) in out["power"]}
        mine = sorted(c for (pp, c) in out["controls"] if pp == p and c in creatures)
        mine.sort(key=lambda c: (power.get(c, 0), c))     # weakest first, deterministic tie-break
        for c in mine[:n]:
            D._sacrifice(state, c)                         # fires 'when sacrificed', then -> graveyard


# ----- get_energy -----------------------------------------------------------------------------------
@encoder("get_energy")
def encode_energy(verb, amt, tgt, extra):
    n = _int(amt)
    scope = _player_scope(tgt) or ("controller" if str(tgt) in ("-", "None") else None)
    if n is None or n < 0 or scope is None:
        return None
    return ("get_energy", n, scope)


@applier("get_energy")
def apply_energy(D, state, a, n, tgt, src, ctrl):
    """Add n {E} energy counters to the player(s) (§107.16). Energy is a player resource the engine
    doesn't model, so it lives in a driver-side state relation state['energy'] = {(player, total)}."""
    players = D._others(state, ctrl) if tgt == "each_opponent" else [ctrl]
    energy = state.setdefault("energy", set())
    for p in players:
        cur = next((e for (q, e) in energy if q == p), 0)
        energy.discard((p, cur))
        energy.add((p, cur + n))
        print(f"    trigger {a}: {p} gets {n} energy -> {cur + n}{{E}}")


# ----- win_game / lose_game -------------------------------------------------------------------------
def _make_lose(D, state, p):
    """End the game for player p the way the driver detects a loss (§704.5a): drop their life to the
    loss threshold so driver._apply_outputs surfaces them as the loser."""
    D._set_life(state, p, D.LIFE_LOSS_THRESHOLD)


@encoder("lose_game")
def encode_lose(verb, amt, tgt, extra):
    scope = _player_scope(tgt)
    return ("lose_game", 0, scope) if scope else None


@applier("lose_game")
def apply_lose(D, state, a, n, tgt, src, ctrl):
    players = D._others(state, ctrl) if tgt == "each_opponent" else [ctrl]
    for p in players:
        print(f"    trigger {a}: {p} loses the game (§104.3a)")
        _make_lose(D, state, p)


@encoder("win_game")
def encode_win(verb, amt, tgt, extra):
    # 'you win the game' — only the clean controller-scoped form (every corpus instance is tgt='you').
    return ("win_game", 0, "controller") if _player_scope(tgt) == "controller" else None


@applier("win_game")
def apply_win(D, state, a, n, tgt, src, ctrl):
    """§104.2a — the controller wins: every OTHER player loses (in a 2-player game, the lone opponent)."""
    for p in D._others(state, ctrl):
        print(f"    trigger {a}: {ctrl} wins -> {p} loses the game (§104.2a)")
        _make_lose(D, state, p)


# ----- set_life -------------------------------------------------------------------------------------
@encoder("set_life")
def encode_set_life(verb, amt, tgt, extra):
    n = _int(amt)
    scope = _player_scope(tgt) or ("controller" if str(tgt) in ("-", "None") else None)
    if n is None or scope is None:
        return None
    return ("set_life", n, scope)


@applier("set_life")
def apply_set_life(D, state, a, n, tgt, src, ctrl):
    """A player's life total BECOMES n (§118.5 / 'your life total becomes N')."""
    players = D._others(state, ctrl) if tgt == "each_opponent" else [ctrl]
    for p in players:
        D._set_life(state, p, n)
        print(f"    trigger {a}: {p}'s life becomes {n}")
