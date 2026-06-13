"""effect_handlers/permanents.py — PERMANENT-STATE effects on the SOURCE or a chosen own permanent.

Own these cards.dl effect verbs (each toggles a permanent's tapped state or proliferates counters — all
resolvable WITHOUT a board scope the engine derives, so they ride the player-scoped trigger_effect path):

  - untap   (§701.20) — 'Untap this artifact' (the §602 untap-combos: Grim/Basalt Monolith, the man-lands
            that untap themselves) and 'Untap target land/permanent/artifact' (Deserted Temple). We resolve
            only the choice-free / own-board cases:
              • untap self / it          -> untap the SOURCE permanent (the activated combo piece).
              • untap target land/permanent/artifact -> untap one of the CONTROLLER'S OWN tapped permanents
                of that class (untapping your own ramp source is always a legal, beneficial choice). A
                target restricted to an OPPONENT's permanent, or a class we can't read, ABSTAINS — we won't
                guess an unfaithful target.
            tap is deliberately NOT owned here: 'tap target X' is almost always an OPPONENT-facing tempo
            play whose target the engine must choose, handled by the creature-target machinery (or abstains).

  - proliferate (§701.27) — 'for each counter on a permanent/player you choose, add another of that kind'.
            Fully deterministic and always faithful: add one more counter of each kind already present, on
            every permanent that has a counter (and every counter a player has). No choice loses value —
            proliferating EVERY eligible counter is a legal superset of any single choice's benefit, and the
            §701.27 'any number' lets you proliferate all of them.

FAITHFUL-OR-ABSTAIN: encode -> None for anything we can't resolve correctly. See effect_handlers/__init__.py
for the @encoder / @applier contract and the driver helpers reachable on D.
"""

from __future__ import annotations

import re

from effect_handlers import encoder, applier


# ── untap ───────────────────────────────────────────────────────────────────────────────────────────
_SELF_TGT = {"self", "it"}

# 'untap target <class>' slugs whose class we can read AND default to the controller's OWN board — untapping
# your own ramp/permanent is always a legal, beneficial choice (no unfaithful guess). The class restricts
# WHICH of the controller's tapped permanents we untap; 'permanent' = any.
_OWN_TARGET = {
    "target_land": "land", "target_permanent": "any", "target_artifact": "artifact",
    "target_creature_you_control": "creature", "target_land_you_control": "land",
    "target_permanent_you_control": "any", "target_artifact_you_control": "artifact",
    # §701.20 anaphoric 'untap that creature' (Cerulean Wisps, Snap-likes) — the creature a prior clause on the
    # same instant just affected; resolve to the controller's strongest creature (matches the prior pick).
    "that_creature": "creature",
    # 'untap target legendary <permanent/land/creature>' (Minamo: '{U},{T}: untap target legendary permanent'
    # — used to untap your own land for mana, or Minamo itself). The legendary restriction isn't enforced, but
    # untapping the controller's own tapped permanent of that type is the faithful, beneficial resolution.
    "target_legendary_permanent": "any", "target_legendary_land": "land", "target_legendary_creature": "creature",
}
# 'untap ANOTHER target …' — same own-board resolution, but the SOURCE is not a legal target (§601 'another'),
# so we must untap a DIFFERENT own permanent. Encoded with an 'other_' class prefix the applier honors.
_OTHER_TARGET = {
    "another_target_land": "land", "another_target_permanent": "any", "another_target_artifact": "artifact",
}


# 'untap UP TO N <lands/permanents/artifacts>' (Frantic Search 'untap up to three lands', Snap 'untap up to
# two lands') — a count-bounded own-board untap. The number word -> N; the noun -> the class. We untap up to
# N of the controller's tapped permanents of that class (a beneficial, faithful 'up to' = as many as legal).
_NUMWORD = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}
_UNTAP_NOUN = {"land": "land", "lands": "land", "permanent": "any", "permanents": "any",
               "artifact": "artifact", "artifacts": "artifact", "creature": "creature", "creatures": "creature"}


@encoder("untap")
def _encode_untap(verb, amt, tgt, extra):
    t = str(tgt)
    if t in _SELF_TGT:
        return ("untap_self", 0, "-")
    cls = _OWN_TARGET.get(t)
    if cls is not None:
        return ("untap_own", 0, cls)
    cls = _OTHER_TARGET.get(t)
    if cls is not None:
        return ("untap_own", 0, "other_" + cls)             # untap a DIFFERENT own permanent (§601 'another')
    m = re.match(r"^up_to_(\w+?)_(?:target_)?(\w+)$", t)     # 'up to three lands' / 'up to two target lands'
    if m:
        n = _NUMWORD.get(m.group(1))
        noun = _UNTAP_NOUN.get(m.group(2))
        if n is not None and noun is not None:
            return ("untap_own_n", n, noun)
    return None                                              # opponent-facing / unreadable target -> abstain


@applier("untap_self")
def _apply_untap_self(D, state, a, n, tgt, src, ctrl):
    """§701.20 — untap the SOURCE permanent (the activated untap-combo piece). A no-op if it isn't tapped."""
    if (src,) in state.get("tapped", set()):
        state["tapped"].discard((src,))
        print(f"    {a}: {src} is untapped")


def _own_tapped(state: dict, ctrl: str, cls: str) -> list:
    """The controller's tapped permanents (on the battlefield, controlled by ctrl) of class `cls` ('any' /
    'land' / 'artifact' / 'creature'), canonical order — judged from the surfaced printed identity."""
    bf = state.get("on_battlefield", set())
    own = {c for (p, c) in state.get("printed_control", set()) if p == ctrl}
    tapped = {c for (c,) in state.get("tapped", set())}
    ptype = state.get("printed_type", set())
    out = []
    for c in sorted(own & tapped & {x for (x,) in bf}):
        if cls == "any" or (c, cls) in ptype:
            out.append(c)
    return out


@applier("untap_own")
def _apply_untap_own(D, state, a, n, tgt, src, ctrl):
    """§701.20 — untap ONE of the controller's own tapped permanents of class `tgt` (a faithful, beneficial
    'untap target …' resolution). Prefer untapping the SOURCE if it qualifies (the self-untap idiom written
    as 'untap target land' on a land), else the canonical-first own tapped permanent. No-op if none."""
    cls = str(tgt)
    other = cls.startswith("other_")                        # §601 'another target …' — the source is excluded
    if other:
        cls = cls[len("other_"):]
    cands = _own_tapped(state, ctrl, cls)
    if other:
        cands = [c for c in cands if c != src]
    if not cands:
        return
    # untapping yourself is the most common intent (a self-untap ramp piece) — unless 'another' forbids it.
    pick = src if (not other and src in cands) else cands[0]
    state["tapped"].discard((pick,))
    print(f"    {a}: {ctrl} untaps {pick}")


@applier("untap_own_n")
def _apply_untap_own_n(D, state, a, n, tgt, src, ctrl):
    """§701.20 — untap UP TO n of the controller's own tapped permanents of class `tgt` (Frantic Search /
    Snap untapping lands to re-use mana). 'up to' = as many as are legal (a beneficial choice), capped at n;
    a no-op if none are tapped."""
    cands = _own_tapped(state, ctrl, str(tgt))[:n]
    for c in cands:
        state["tapped"].discard((c,))
    if cands:
        print(f"    {a}: {ctrl} untaps {len(cands)} {tgt}(s): {', '.join(cands)}")


# ── proliferate (§701.27) ────────────────────────────────────────────────────────────────────────────
@encoder("proliferate")
def _encode_proliferate(verb, amt, tgt, extra):
    # proliferate carries no useful amt/target — it always acts on every eligible counter. Always faithful.
    return ("proliferate", 0, "-")


@applier("becomes_color")
def _apply_becomes_color(D, state, a, n, tgt, src, ctrl):
    """§613 layer 5 'target creature becomes <color> until end of turn' (Crimson/Cerulean Wisps — paired with
    a haste grant on the same creature). Beneficial flavor, so the driver picks the controller's strongest
    creature (matching the haste grant's own-creature pick) and sets eff_set_color until cleanup."""
    color, _, cls = str(tgt).partition("|")
    out = D.run(state, ["controls", "creature", "power"])
    creatures = {c for (c,) in out["creature"]}
    powers = {c: int(x) for (c, x) in out["power"]}
    on_bf = {c for (c,) in state.get("on_battlefield", set())}
    mine = {c for (p, c) in out["controls"] if p == ctrl}
    cands = [c for c in creatures if c in on_bf and (c in mine if cls in ("you_control", "any") else True)]
    if not cands:
        print(f"    {a}: no creature to make {color}")
        return
    target = max(cands, key=lambda c: powers.get(c, 0))
    eid = f"{a}__color__{target}"
    state.setdefault("eff_set_color", set()).add((eid, target, color, 1))
    state.setdefault("until_eot", set()).add((eid,))
    print(f"    {a}: {target} becomes {color} until end of turn")


@applier("proliferate")
def _apply_proliferate(D, state, a, n, tgt, src, ctrl):
    """§701.27 — for every permanent/player that has any counter, add one more of each KIND already there.
    Deterministic and faithful: proliferating ALL eligible counters is the maximal legal §701.27 choice."""
    counters = state.get("counter", set())                  # (object, kind, count) — permanents AND players
    # snapshot the kinds present per object BEFORE mutating, so we add exactly one per existing kind.
    present = {(o, k) for (o, k, c) in counters if int(c) > 0}
    for (o, k) in sorted(present):
        D._bump_counter(state, o, k, 1)
    if present:
        print(f"    {a}: {ctrl} proliferates ({len(present)} counter kind(s) advanced)")


# ── double the number of +1/+1 counters (§122) ───────────────────────────────────────────────────────
# 'Double the number of +1/+1 counters on ~' (Mossborn Hydra / Primordial Hydra / Solarion) and 'on each
# creature you control' (Kalonian Hydra). Add to each in-scope creature a copy of its CURRENT +1/+1 count
# so the total doubles — fully deterministic. We own ONLY the two unambiguous +1/+1-specific shapes whose
# scope needs no driver context:
#   • '…1_1_counters_on'                      -> the SOURCE permanent itself.
#   • '…1_1_counters_on_each_creature_you_control' -> every creature the controller controls.
# Everything else ABSTAINS (faithful): a TARGET ('on target creature') needs a chosen pick; a back-reference
# ('on it' / 'on that creature' / 'on those creatures' / 'on enchanted creature') points at an object only
# the resolving context knows; and 'double each KIND of counter' isn't +1/+1-only. Guessing any of these
# would touch the wrong creature or the wrong counters.
_DOUBLE_SELF = "the_number_of_1_1_counters_on"
_DOUBLE_SCOPE = "the_number_of_1_1_counters_on_each_creature_you_control"


@encoder("double")
def _encode_double(verb, amt, tgt, extra):
    t = str(tgt)
    if t == _DOUBLE_SCOPE:
        return ("double_counters", 0, "creatures_you_control")
    if t == _DOUBLE_SELF:
        return ("double_counters", 0, "self")
    return None                                             # target / back-reference / each-kind -> abstain


@applier("double_counters")
def _apply_double_counters(D, state, a, n, tgt, src, ctrl):
    """§122 — add to each in-scope creature a copy of its current +1/+1 count (the count doubles). Scope
    'self' = the source; 'creatures_you_control' = every creature the controller controls. Snapshot the
    counts first so doubling one creature can't feed another (m1m1 counters aren't touched — the clause is
    +1/+1-specific)."""
    objs = [src] if tgt == "self" else D._creatures_of(state, ctrl)
    cur = {o: c for (o, k, c) in state.get("counter", set()) if k == "p1p1"}
    doubled = 0
    for o in objs:
        if cur.get(o, 0) > 0:
            D._bump_counter(state, o, "p1p1", cur[o]); doubled += 1
    print(f"    {a}: {ctrl} doubles +1/+1 counters on {tgt} ({doubled} creature(s) with counters)")


# ── earthbend (§701 keyword action) ───────────────────────────────────────────────────────────────────
# 'Earthbend N' (Badgermole Cub, Ba Sing Se, Earthbender Ascension): 'Target land you control becomes a 0/0
# creature with haste that's still a land. Put N +1/+1 counters on it.' We animate a land the controller
# controls PERMANENTLY (not until EOT — earthbend is a lasting change): §613 layer-4 add the creature type,
# layer-7b set base 0/0, grant haste, and §122 put N +1/+1 counters (which carry the P/T to N/N and keep the
# 0/0 alive). The land is a deterministic own-board pick (any land you control is a legal, beneficial target,
# like untap_own / proliferate) — a faithful single legal choice the search doesn't branch on. The 'when it
# dies or is exiled, return it tapped' delayed trigger is NOT modeled (a conservative omission: a dead land
# simply stays dead — we never fabricate a return); the resolvable animate+counter core is faithful.
@encoder("earthbend")
def _encode_earthbend(verb, amt, tgt, extra):
    s = str(amt)
    if not s.isdigit() or int(s) <= 0:                      # only a concrete positive count (Earthbend 1/2/…)
        return None                                          # a variable 'earthbend X' abstains
    return ("earthbend", int(s), "land_you_control")


def _own_lands(state: dict, ctrl: str) -> list:
    """The controller's lands on the battlefield, canonical order (from the surfaced printed identity)."""
    bf = {c for (c,) in state.get("on_battlefield", set())}
    own = {c for (p, c) in state.get("printed_control", set()) if p == ctrl}
    ptype = state.get("printed_type", set())
    return sorted(c for c in (own & bf) if (c, "land") in ptype)


@applier("earthbend")
def _apply_earthbend(D, state, a, n, tgt, src, ctrl):
    """§701 earthbend N — animate a land the controller controls to a 0/0 creature with haste (PERMANENTLY,
    no until_eot marker) and put N +1/+1 counters on it. Prefer a land that isn't already an earthbend
    creature (spread the value); else the canonical-first own land. A no-op if the controller has no land."""
    lands = _own_lands(state, ctrl)
    if not lands:
        return
    animated = {c for (_e, c, _t) in state.get("eff_add_type", set())}
    pick = next((c for c in lands if c not in animated), lands[0])
    eid = f"earthbend__{pick}"                               # STABLE id (per land) -> idempotent re-derivation
    # §613 permanent characteristic-setting layers (no until_eot -> the change lasts, unlike a man-land).
    state.setdefault("eff_set_power", set()).add((eid, pick, 0, 1))
    state.setdefault("eff_set_toughness", set()).add((eid, pick, 0, 1))
    state.setdefault("eff_add_type", set()).add((eid, pick, "creature"))
    state.setdefault("eff_grant_keyword", set()).add((eid, pick, "haste"))
    D._bump_counter(state, pick, "p1p1", n)                  # §122 N +1/+1 counters -> the 0/0 becomes N/N
    print(f"    {a}: {ctrl} earthbends {pick} (0/0 creature-land with haste, +{n} +1/+1 counters)")
