"""effect_handlers/move_counter.py — §122 MOVE a counter between permanents.

'Move a counter from <src> onto <dst>' (Nesting Grounds: '{1}, {T}: Move a counter from target permanent you
control onto a second target permanent'). The parser grounds it as a put_counter on the dst with cond
moved_from_<src>; the bridge routes that to a `move_counter` activated_ability slot carrying
'<kind>|<src_class>|<dst_class>'. Here we resolve the RELOCATION: take one counter off a source permanent the
controller controls (a counter it actually has) and put it on a destination permanent. Both picks route through
the _choose seam (drivable by a policy); the greedy default takes a counter off the controller's least valuable
body and moves it onto the strongest creature they control (beneficial for the common +1/+1 case) — a stable,
always-legal choice. No legal source (nothing of ours carries a counter) or no destination -> a faithful no-op.
"""

from __future__ import annotations

from effect_handlers import applier


@applier("move_counter")
def apply_move_counter(D, state, a, n, payload, src, ctrl):
    """`payload` is '<kind>|<src_class>|<dst_class>'. kind 'any' = any counter present on a source; else only
    that kind. Move ONE counter from a controlled source that has it onto a destination permanent."""
    parts = str(payload).split("|")
    kind = parts[0] if parts else "any"
    out = D.run(state, ["controls", "creature", "power"])
    mine = sorted(c for (p, c) in out["controls"] if p == ctrl)
    powers = {c: int(x) for (c, x) in out["power"]}
    counters = state.get("counter", set())

    def kinds_on(c):
        return {k for (o, k, v) in counters if o == c and int(v) > 0 and (kind == "any" or k == kind)}

    sources = sorted(c for c in mine if kinds_on(c))
    if not sources:
        print(f"    {a}: no {kind} counter on a permanent {ctrl} controls to move")
        return
    # default source: the controller's least valuable body (lowest power) carrying a movable counter
    s = D._choose(state, "move_counter_src", sources, min(sources, key=lambda c: powers.get(c, 0)))
    k = sorted(kinds_on(s))[0]
    creatures = {c for (c,) in out["creature"]}
    dests = sorted(c for c in mine if c != s)
    if not dests:
        print(f"    {a}: no second permanent for {ctrl} to move the {k} counter onto")
        return
    # default destination: the controller's strongest CREATURE (else any other controlled permanent)
    best_creature = max((c for c in dests if c in creatures), key=lambda c: powers.get(c, 0), default=None)
    d = D._choose(state, "move_counter_dst", dests, best_creature or dests[0])
    D._bump_counter(state, s, k, -1)
    D._bump_counter(state, d, k, 1)
    print(f"    {a}: moves a {k} counter from {s} onto {d} (§122)")
