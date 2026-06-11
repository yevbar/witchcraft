"""env.py — a referee/environment over the rules engine: enumerate the legal actions at each decision
point and step on a CHOSEN one, instead of the driver's hardcoded greedy play. This is the interface an
AlphaZero-style agent (or any search/policy) drives.

The driver is a *player*: play_game makes every choice greedily (attack with all, target the strongest,
cast the first castable). This module turns it into a *referee* by routing those choices through the
driver's `_choose` seam: legal_actions(state) lists the options at the current decision point, and
step(state, action) applies the chosen one through the driver's REAL resolution (stack, targets, modes,
combat, §613 layers) and auto-advances through the no-decision steps to the next choice.

  legal_actions(state) -> [action]   the choices to move now (cast / activate / attack / block / pass)
  step(state, action)  -> state'     PURE transition through the real engine (never mutates `state`)
  to_move(state)       -> player     whose decision it is
  is_terminal(state)   -> bool        a player has lost (or the game is a draw)
  winner(state)        -> player|None the winner of a terminal state (None = draw / non-terminal)

Action shapes (all tuples, hashable so they index a policy/MCTS):
  ("cast", ap, spell, {"mode": m?, "target": t?})   cast a castable spell with its sub-choices forced
  ("activate", ap, ability_row, {"target": t?})      activate an affordable ability
  ("attack", frozenset(attackers))                   declare attackers (a subset of the eligible)
  ("block",  frozenset((blocker, attacker)))         declare blockers (a legal assignment)
  ("pass",)                                          pass priority / end the current decision window

Scope: the action space is exactly as rich as the engine — what's modeled is offered, what abstains isn't
(same faithful-or-abstain bar as the bridge). Attacker/blocker enumeration is capped (noted below) to keep
the branching factor finite; the cap is a policy detail, not an engine limit.
"""

from __future__ import annotations

import contextlib
import io
import itertools

import driver

_MAIN = {"precombat_main", "postcombat_main"}
_MAX_SUBSET_ATOMS = 5        # enumerate every attacker subset only up to this many eligible attackers


def _clone(state: dict) -> dict:
    return driver.clone_state(state)                     # ~17x faster than deepcopy; correct for this state shape


def _active(state: dict) -> str:
    return next(iter(state["active_player"]))[0]


def _source_ids(state: dict, ap: str) -> set:
    """The ids of ap's mana-source permanents currently on the battlefield (lands, lexed rocks/dorks, and
    flagged mana_source) — used to detect a NEW source entering after a cast so the pool can re-stock."""
    bf, ctrl = state.get("on_battlefield", set()), state.get("printed_control", set())
    ptype = state.get("printed_type", set())
    precise = {t for (t, _c, _a) in state.get("source_produces", set())} \
        | {t for (t, _k, _a) in state.get("source_wildcard", set())}
    return {c for (c,) in bf if (ap, c) in ctrl
            and ((c, "land") in ptype or (c,) in state.get("mana_source", set()) or c in precise)}


def _step(state: dict) -> str:
    return next(iter(state["current_step"]))[0]


def _others(state: dict, p: str) -> list[str]:
    return sorted(q for (q,) in state["is_player"] if q != p)


def _quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


# ---- terminal / turn ----------------------------------------------------------------------------------

def is_terminal(state: dict) -> bool:
    return bool(state.get("_loser"))


def winner(state: dict) -> str | None:
    loser = state.get("_loser")
    if not loser:
        return None
    rest = _others(state, loser)
    return rest[0] if len(rest) == 1 else None      # 1v1: the other seat wins


def to_move(state: dict) -> str:
    """Whose decision it is: the defending player while blocks are being declared, else the active player."""
    if _step(state) == "declare_blockers" and state.get("attacks"):
        return _others(state, _active(state))[0]
    return _active(state)


# ---- legal actions ------------------------------------------------------------------------------------

def _target_options(state: dict, cls: str) -> list[str]:
    """Legal creatures of a single-target class (any / you_control / opponent), as the driver's _pick_target
    would constrain — but ALL of them, for the agent to choose among (not the greedy strongest)."""
    out = driver.run(state, ["controls", "creature"])
    controls = {(p, c) for (p, c) in out["controls"]}
    creatures = {c for (c,) in out["creature"]}
    on_bf = {c for (c,) in state.get("on_battlefield", set())}
    ap = _active(state)
    mine = {c for (p, c) in controls if p == ap}
    cands = [c for c in sorted(creatures) if c in on_bf]
    if cls == "you_control":
        return [c for c in cands if c in mine]
    if cls == "opponent":
        return [c for c in cands if c not in mine]
    return cands


def _name_choices(state: dict, spell: str) -> list:
    """The "choose a card name" options for a spell carrying a name_exile_lib effect (Demonic Consultation /
    Spoils of the Vault): every distinct library name plus the guaranteed-absent sentinel — so a search can
    explore naming an ABSENT card (empties the library: the Thassa's-Oracle combo line). [None] for a spell
    with no such effect, so the cartesian product below leaves non-naming spells untouched."""
    if not any(s == spell and e == "name_exile_lib" for (s, e, _n, _t) in state.get("spell_effect", set())):
        return [None]
    from effect_handlers import library as _lib
    return _lib.name_candidates(state, _active(state))


def _cast_choices(state: dict, spell: str) -> list[dict]:
    """The sub-choice dicts a cast of `spell` needs: one per (mode × single target × named card) combination
    the engine surfaced (spell_mode / spell_target / name_exile_lib). A spell with none yields one empty dict."""
    modes = sorted(m for (s, m) in state.get("spell_mode", set()) if s == spell) or [None]
    tcls = next((cls for (s, _v, _p, cls) in state.get("spell_target", set()) if s == spell), None)
    targets = _target_options(state, tcls) if tcls else [None]
    names = _name_choices(state, spell)
    if not targets:                                  # a target is required but none is legal -> uncastable
        return []
    choices = []
    for m in modes:
        for t in targets:
            for nm in names:
                c = {}
                if m is not None:
                    c["mode"] = m
                if t is not None:
                    c["target"] = t
                if nm is not None:
                    c["name"] = nm
                choices.append(c)
    return choices


def _activate_choices(state: dict, ability_row: tuple) -> list[dict]:
    """Sub-choices for an activated ability: a creature-targeted ability (ctarget sentinel) enumerates its
    legal targets; everything else is a single no-choice activation."""
    eff, tgt = ability_row[4], ability_row[6]
    if eff == "ctarget":
        cls = str(tgt).split("|")[-1]
        opts = _target_options(state, cls)
        return [{"target": t} for t in opts] if opts else []
    return [{}]


def _attack_options(state: dict, ap: str) -> list[frozenset]:
    """Declare-attackers choices: subsets of the eligible attackers. Every subset up to _MAX_SUBSET_ATOMS
    creatures; beyond that, {none, all, each singleton} to keep the branching factor finite (a policy cap)."""
    sick = state.get("_sick", set())
    haste = {c for (c, k) in driver.run(state, ["has_keyword"])["has_keyword"] if k == "haste"}
    eligible = sorted(c for (c,) in driver.run(state, ["may_attack"])["may_attack"]
                      if (c,) not in sick or c in haste)
    if not eligible:
        return [frozenset()]
    if len(eligible) <= _MAX_SUBSET_ATOMS:
        return [frozenset(s) for r in range(len(eligible) + 1) for s in itertools.combinations(eligible, r)]
    return [frozenset(), frozenset(eligible)] + [frozenset([c]) for c in eligible]


def _legal_block_pairs(state: dict, ap: str) -> list[tuple[str, str]]:
    attackers = sorted(a for (a, _) in state.get("attacks", set()))
    blockers = [b for b in driver._creatures_of(state, ap) if (b,) not in state.get("tapped", set())]
    pairs = []
    for b in blockers:
        for a in attackers:
            probe = dict(state); probe["blocks"] = {(b, a)}
            if (b, a) not in driver.run(probe, ["illegal_block"])["illegal_block"]:
                pairs.append((b, a))
    return pairs


def _block_options(state: dict, ap: str) -> list[frozenset]:
    """Declare-blockers choices: the no-block, the greedy one-per-attacker assignment, and each single
    legal block. (A representative, capped slice of the assignment space — not the full product.)"""
    pairs = _legal_block_pairs(state, ap)
    opts = [frozenset()]
    greedy: dict = {}
    for (b, a) in pairs:
        if a not in greedy and b not in greedy.values():
            greedy[a] = b
    if greedy:
        opts.append(frozenset((b, a) for a, b in greedy.items()))
    for pr in pairs:
        fs = frozenset([pr])
        if fs not in opts:
            opts.append(fs)
    return opts


def legal_actions(state: dict) -> list[tuple]:
    """The choices available to move now, at the current decision point (post auto-advance)."""
    if is_terminal(state):
        return []
    ap = _active(state)
    step = _step(state)
    if step in _MAIN:
        probe = _clone(state); probe["has_priority"] = {(ap,)}
        castable = sorted(s for (p, s) in driver.run(probe, ["can_cast"])["can_cast"] if p == ap)
        actions: list[tuple] = []
        for spell in castable:
            for ch in _cast_choices(state, spell):
                actions.append(("cast", ap, spell, ch))
        for cmd in driver.can_cast_commander(state, ap):     # §903.6 — cast the commander from the command zone
            actions.append(("cast_commander", ap, cmd))
        for ab in driver._activatable(state, ap):
            for ch in _activate_choices(state, ab):
                actions.append(("activate", ap, ab, ch))
        actions.append(("pass",))
        return actions
    if step == "declare_attackers":
        return [("attack", s) for s in _attack_options(state, ap)]
    if step == "declare_blockers":
        return [("block", b) for b in _block_options(state, _others(state, ap)[0])]
    return [("pass",)]


# ---- step ---------------------------------------------------------------------------------------------

def _develop_if_main(state: dict) -> None:
    if _step(state) in _MAIN:
        _quiet(driver._develop_mana, state, _active(state))     # §305 land drop + mana refresh on phase entry


def _advance_one(state: dict) -> None:
    """Advance exactly one step (the no-cast core of driver.play_game's inner loop), honoring any forced
    combat choice via the _choose seam; develop mana when a main phase is entered."""
    ap = _active(state)
    step = _step(state)
    if step == "declare_attackers":
        _quiet(driver.declare_attackers, state, ap)
    elif step == "declare_blockers":
        _quiet(driver.declare_blockers, state, ap)
    if step == "cleanup":
        _quiet(driver._end_of_turn, state)
    out = driver.run(state, driver.OUTPUTS)
    loser = _quiet(driver._apply_outputs, state, out, ap)
    if loser:
        state["_loser"] = loser
        return
    if out["advance_to"]:
        state["current_step"] = out["advance_to"]
    else:                                                       # past cleanup -> next player's turn (§500.6)
        players = sorted(p for (p,) in state["is_player"])
        nxt = driver._next_active_player(state, ap, players)     # next player — or an extra turn (§500.7)
        state["_turn"] = state.get("_turn", 0) + 1               # a turn counter (for lookahead horizons)
        state["active_player"] = {(nxt,)}
        state["current_step"] = {("untap",)}
        state["attacks"], state["blocks"] = set(), set()
        state["_land_played"] = set()                           # §305.2 — a fresh land drop next turn
        ctrl = {c for (pp, c) in driver.run(state, ["controls"])["controls"] if pp == nxt}
        state["_sick"] = {row for row in state.get("_sick", set()) if row[0] not in ctrl}  # §302.6 wears off
    _develop_if_main(state)


def _has_decision(state: dict) -> bool:
    """A real choice exists now: a castable/activatable in a main phase, or a combat declaration."""
    if is_terminal(state):
        return False
    step = _step(state)
    if step in _MAIN:
        acts = legal_actions(state)
        return len(acts) > 1                                    # more than just ("pass",)
    if step == "declare_attackers":
        return any(_attack_options(state, _active(state)))      # always at least {none}; a real choice if eligible
    if step == "declare_blockers":
        return bool(_legal_block_pairs(state, _others(state, _active(state))[0]))
    return False


def _advance_to_decision(state: dict, max_steps: int = 200) -> None:
    """Auto-resolve no-choice steps until a real decision point (or terminal), so the agent only ever sees
    meaningful choices. Combat steps with eligible attackers/blockers count as decisions."""
    for _ in range(max_steps):
        if is_terminal(state) or _has_decision(state):
            return
        _advance_one(state)


def start(state: dict) -> dict:
    """Advance a fresh state to its first decision point — the environment's reset()."""
    s = _clone(state)
    with contextlib.redirect_stdout(io.StringIO()):
        _advance_to_decision(s)
    return s


def step(state: dict, action: tuple) -> dict:
    """Pure transition: apply `action` through the driver's real resolution, then auto-advance to the next
    decision point. Leaves `state` untouched."""
    s = _clone(state)
    players = sorted(p for (p,) in s["is_player"])
    kind = action[0]
    with contextlib.redirect_stdout(io.StringIO()):
        if kind == "cast":
            _, ap, spell, choices = action
            s["_forced"] = dict(choices)
            srcs_before = _source_ids(s, ap)
            driver._cast_spell(s, ap, spell, players)
            s["_forced"] = {}
            # §106 if the spell that resolved was a mana SOURCE (a Mox/Petal/rock entering the battlefield),
            # the §601.2g pool refresh inside _cast_spell ran BEFORE it entered, so it isn't yet spendable.
            # Re-stock the pool from the board so the new source can pay the NEXT cast this same turn. Only
            # when a source actually entered — never after a ritual, so floating mana (Dark Ritual) survives.
            if _source_ids(s, ap) - srcs_before:
                driver._refresh_mana_pool(s, ap)
        elif kind == "cast_commander":                          # §903.6 — cast commander from the command zone
            _, ap, cmd = action
            driver._develop_mana(s, ap)                         # §305 land drop + mana (mirrors _cast_phase entry)
            driver.cast_commander(s, ap, cmd, players)
        elif kind == "activate":
            _, ap, ab, choices = action
            s["_forced"] = {"target": choices["target"]} if "target" in choices else {}
            # mirror driver._activate_phase for a CHOSEN ability row
            a, src, cost, taps, eff, amt, tgt = ab
            if int(cost):
                driver._spend_ability_mana(s, ap, int(cost))
            if taps == "T":
                s.setdefault("tapped", set()).add((src,))
            s.setdefault("_ability_effect", {})[a] = (eff, int(amt), tgt, src, ap)
            driver._stack_push(s, a, ap)
            driver._resolve_stack(s, ap, players)
            s["_forced"] = {}
        elif kind == "attack":
            s["_forced"] = {"attackers": action[1]}
            _advance_one(s)
            s["_forced"] = {}
        elif kind == "block":
            s["_forced"] = {"blocks": action[1]}
            _advance_one(s)
            s["_forced"] = {}
        else:                                                   # pass: leave the current window
            _advance_one(s)
        _advance_to_decision(s)
    return s
