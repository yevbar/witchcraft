"""engine_handlers/counters_resources.py — VERBS: remove_counter, regenerate, get_energy, investigate, double.

Counters, regeneration, and resource tokens.
  - remove_counter : remove +1/+1 (or other) counters from targets. The engine tracks net +1/+1 counters
        as perm.counters (int). Decrement faithfully (extra names the counter kind / amount; n the count).
  - regenerate : raise a regeneration shield — add 'regen_shield' to perm.flags on each target (the
        destruction chokepoint _destroy/sba already consumes it to tap+heal instead of dying). Default
        target is the source/self.
  - get_energy : add energy counters — pl.resources['energy'] += n (Player.resources is a Counter).
  - investigate : create n Clue artifact tokens — reuse game._make_token (try _make_token('clue') or build
        a Card('Clue', ...) artifact and append Perm(...) to pl.bf). Faithful if the token is created.
  - double : context-dependent ("double" your life / a creature's power / energy / counters). Inspect
        tgt/extra; implement the clear, faithful cases (e.g. double a creature's +1/+1 counters via
        perm.counters *= 2; double your life pl.life *= 2 if tgt says so) and ABSTAIN on the ambiguous
        ones rather than guess.

Faithful-or-no-op. Owner: ONE agent. Contract & helpers: engine_handlers/__init__.py.
"""

from __future__ import annotations

from collections import Counter

from mtg.engine.engine import Card, Perm
from engine_handlers import register

# The only counter kinds the engine models numerically: net +1/+1 lives in Perm.counters (int), where a
# +1/+1 is +1 and a -1/-1 is -1 toward that net. Every other counter kind (time, depletion, oil, study,
# growth…) has no modeled state, so removing it is a faithful no-op.
_PLUS = "+1/+1"
_MINUS = "-1/-1"


def _self_or_targets(game, pl, opp, tgt, source, pred=None):
    """Resolve a permanent-scoped target, defaulting self/it to the source permanent (if still on bf)."""
    if tgt in ("self", "it", "enchanted_creature", "equipped_creature") and source in pl.bf:
        return [source] if (pred is None or pred(source)) else []
    return game._perm_targets(pl, opp, tgt, pred=pred)


@register("remove_counter")
def remove_counter(game, pl, opp, amt, tgt, extra, source, n):
    # extra names the counter kind. Only +1/+1 and -1/-1 map onto the engine's net-int perm.counters;
    # any other kind is unmodeled state, so abstain.
    if extra not in (_PLUS, _MINUS):
        return
    sign = 1 if extra == _PLUS else -1     # removing a +1/+1 lowers net by 1; removing a -1/-1 raises it
    for p in _self_or_targets(game, pl, opp, tgt, source):
        if amt == "all":
            # Remove every counter of that kind. With only a net int we can faithfully zero a net that is
            # entirely of this kind (same sign), else abstain on that permanent rather than guess the mix.
            if (sign > 0 and p.counters > 0) or (sign < 0 and p.counters < 0):
                p.counters = 0
                game.log(f"removes all {extra} counters from {p.card.name}", 2)
            continue
        k = n or 1
        p.counters -= sign * k
        game.log(f"removes {k} {extra} counter(s) from {p.card.name} "
                 f"(now {p.power}/{p.toughness})", 2)
    game.sba()                              # removing +1/+1 counters can be lethal (toughness drop)


@register("regenerate")
def regenerate(game, pl, opp, amt, tgt, extra, source, n):
    # Raise a regeneration shield; _destroy/sba consume it (tap + heal) in place of the next destruction.
    # Default target is the source itself.
    targets = _self_or_targets(game, pl, opp, tgt, source) if tgt not in ("self", "it") \
        else ([source] if source in pl.bf else [])
    for p in targets:
        p.flags.add("regen_shield")
        game.log(f"{p.card.name} gains a regeneration shield", 2)


@register("get_energy")
def get_energy(game, pl, opp, amt, tgt, extra, source, n):
    # Energy is a generic resource counter on the controller. Only a concrete count is faithful; variable
    # amounts ("that_amount", "1_per_creature…") have no evaluable value here, so abstain.
    if n <= 0:
        return
    pl.resources["energy"] += n
    game.log(f"{pl.name} gets {n} energy (now {pl.resources['energy']})", 2)


@register("investigate")
def investigate(game, pl, opp, amt, tgt, extra, source, n):
    # Create Clue artifact tokens for the controller. The number is usually unstated (one Clue); honor an
    # explicit count when present. The token has no creature characteristics, so it just sits on the bf.
    k = n if n > 0 else 1
    tok = game._make_token("clue")
    if tok is None:
        tok = Card(name="Clue", cost=Counter(), types={"Artifact"}, subtypes={"Clue"},
                   power=None, toughness=None)
    for _ in range(k):
        pl.bf.append(Perm(tok, game.p.index(pl), sick=True))
    game.log(f"{pl.name} investigates — creates {k} Clue token(s)", 2)


@register("double")
def double(game, pl, opp, amt, tgt, extra, source, n):
    # tgt is a free-text description of WHAT is doubled. Implement only the cases the engine models:
    #   - the number of +1/+1 counters on a creature  -> perm.counters *= 2
    #   - a player's life total                        -> who.life *= 2
    # Abstain on everything else (power-until-eot, team buffs, each-kind-of-counter, mana, life-loss/draw).
    t = tgt or ""

    if "1_1_counters" in t or "1/1_counters" in t:
        # Only the simple net +1/+1 model is faithful; "each kind of counter" is out of scope.
        if "each_kind" in t or "each_type" in t:
            return
        for p in _double_counter_targets(game, pl, opp, t, source):
            if p.counters > 0:                     # doubling 0 or a negative net is meaningless / unsafe
                before = p.counters
                p.counters *= 2
                game.log(f"doubles {p.card.name}'s +1/+1 counters ({before} -> {p.counters})", 2)
        game.sba()
        return

    if "life_total" in t:
        for who in _life_targets(game, pl, opp, t):
            who.life *= 2
            game.log(f"{who.name}'s life total doubles (now {who.life})", 2)
        return
    # All other 'double' forms are ambiguous or unmodeled — abstain.


def _double_counter_targets(game, pl, opp, t, source):
    """Resolve which creatures' +1/+1 counters to double from a free-text 'the number of 1 1 counters on …'
    target. Self/it/that/enchanted -> the source; an explicit creature spec -> the matched permanents."""
    if source in pl.bf and any(w in t for w in
                               ("counters_on_it", "on_that_creature", "enchanted_creature",
                                "counters_on_each_of_them", "counters_on_those")):
        # 'on it' / 'on that creature' refer back to the source/just-affected object we can't otherwise
        # resolve; bind to the source when it's still on the battlefield.
        return [source]
    # An explicit "target creature [you control] / each creature you control" — reuse the permanent picker.
    spec = t[t.index("counters_on") + len("counters_on_"):] if "counters_on_" in t else t
    pred = (lambda p: "Creature" in p.card.types)
    hits = game._perm_targets(pl, opp, spec, pred=pred)
    if hits:
        return hits
    return [source] if source in pl.bf else []


def _life_targets(game, pl, opp, t):
    """Player(s) whose life total a 'double … life total' effect hits."""
    if t.startswith("your") or "your_life" in t:
        return [pl]
    if "its_controller" in t:
        return [pl]            # the source's controller is the effect's controller here
    return game._players(pl, opp, t, default=[pl])
