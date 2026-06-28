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

# §107.16 doubling a CHARACTERISTIC quantity (a one-shot at resolution: read the current value, add the same
# again). Distinct from §614 replacement doublers (Doubling Season etc. — those live on the driver._doubler /
# _life_gain_mods machinery). We own only sub-cases whose base quantity the engine can READ at resolution AND
# whose object is choice-free (the SOURCE / the controller) or a deterministic beneficial pick:
#   • POWER until EOT          — 'double <this>'s power' (Devilish Valet, Tifa, Casey Jones, Overclocked
#                                Electromancer, Two-Handed Axe): read the source's live power P, add a +P/+0
#                                eff_mod_power until EOT (P doubles). 'target creature's power' (Bulk Up,
#                                Neyith, Legion Leadership…) is ALWAYS a buff, so a single legal beneficial
#                                pick (the controller's strongest creature) is faithful — no sign flip.
#   • POWER AND TOUGHNESS EOT  — 'double <this>'s power and toughness' (Targ Nar, Reckless Amplimancer,
#                                Grunn): double both via +P/+0 and +0/+T eff_mod until EOT.
#   • A PLAYER'S LIFE TOTAL    — 'double your life total' (A Good Thing, Angelic Enforcer) / 'target player's'
#                                (Beacon of Immortality, a beneficial gain -> the controller): set life to 2×
#                                current via _adjust_life(delta = current).
# ABSTAIN on: 'double the number of +1/+1 counters on TARGET/it/that/those/enchanted creature' (a chosen or
# back-referenced object — the existing double_counters owns only self/scope); 'each KIND of counter' (not
# +1/+1-only); doubling a creature's power 'X times' / 'the power of EACH creature' / 'team' (a board scope
# the engine must enumerate); doubling unspent mana / time / growth counters; and any 'double and …' chain
# whose base quantity is itself variable. A wrong object or an un-readable base is worse than a dropped clause.
# A 'target creature['s] / the power[…] of target creature' object — the spell/ability picks a creature; doubling
# power[/toughness] is always a buff, so the faithful single pick is the controller's strongest creature.
_DOUBLE_PWR_TGT = ("target_creature_s_power_until_end_of_turn", "target_creature_s_power",
                   "the_power_of_target_creature_until_end_of_turn",
                   "the_power_of_target_creature_you_control_until_end_of_turn")
_DOUBLE_PT_TGT = ("target_creature_s_power_and_toughness_until_end_of_turn",
                  "the_power_and_toughness_of_target_creature_you_control_until_end_of_turn")
_DOUBLE_LIFE = ("your_life_total", "target_player_s_life_total")


# Object PREFIXES that mean a creature OTHER than the source (a target / a back-reference / a host) — those
# don't resolve to `src`, so the self matchers must EXCLUDE them (they go to the target case or abstain).
_NOT_SELF = ("target_creature_", "the_power_", "equipped_creature_", "enchanted_creature_")


def _is_double_pwr_self(t: str) -> bool:
    # 'this creature's / <name>'s power until end of turn' (Devilish Valet -> bare 's_power…'; Casey Jones ->
    # 'casey_jones_s_power…'). The card name grounds INTO the slug as a possessive '…_s_power…'. Power-ONLY
    # tail (NOT '…and_toughness…'). EXCLUDE non-source objects (target / host / back-reference). We DELIBERATELY
    # abstain on the bare back-reference 'its_power…': the slug drops the subject, so 'its' means SELF on a
    # creature ("whenever this creature attacks, double its power" — Overclocked Electromancer) but the
    # EQUIPPED creature on an Equipment ("whenever equipped creature attacks, double its power" — Two-Handed
    # Axe). The encoder can't tell which from the slug, so resolving it would mis-target half the cases.
    if t.startswith(_NOT_SELF) or "and_toughness" in t:
        return False
    return t == "s_power_until_end_of_turn" or t.endswith("_s_power_until_end_of_turn")


def _is_double_pt_self(t: str) -> bool:
    # 'this creature's / <name>'s power and toughness' (Reckless Amplimancer, Targ Nar). EXCLUDE non-source
    # objects (target / equipped / enchanted) and — like the power case — the ambiguous 'its_' back-reference
    # (Grunn's 'double its power and toughness' is self, but on an attachment 'its' would be the host).
    if t.startswith(_NOT_SELF):
        return False
    return t == "s_power_and_toughness_until_end_of_turn" or t.endswith("_s_power_and_toughness_until_end_of_turn")


@encoder("double")
def _encode_double(verb, amt, tgt, extra):
    t = str(tgt)
    if t == _DOUBLE_SCOPE:
        return ("double_counters", 0, "creatures_you_control")
    if t == _DOUBLE_SELF:
        return ("double_counters", 0, "self")
    if _is_double_pt_self(t):                                # double the source's power AND toughness until EOT
        return ("double_power", 0, "self_pt")
    if _is_double_pwr_self(t):                               # double the source's power until EOT
        return ("double_power", 0, "self")
    if t in _DOUBLE_PT_TGT:                                  # double a target creature's power AND toughness (buff)
        return ("double_power", 0, "target_pt")
    if t in _DOUBLE_PWR_TGT:                                 # double a target creature's power until EOT (a buff)
        return ("double_power", 0, "target")
    if t in _DOUBLE_LIFE:                                    # double a player's life (beneficial -> the controller)
        return ("double_life", 0, "you")
    return None                                             # target counters / back-reference / each-kind -> abstain


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


@applier("double_power")
def _apply_double_power(D, state, a, n, tgt, src, ctrl):
    """§107.16 'double <creature>'s power [and toughness] until end of turn'. Read the creature's LIVE power
    P (and toughness T) — which already folds in counters and other pumps via the §613 layers — and add a
    continuous +P/+0 (and +0/+T) eff_mod that wears off at cleanup (§611.2). Adding the current value again
    doubles it. The object is:
      • 'self' / 'self_pt'     — the SOURCE permanent (choice-free).
      • 'target' / 'target_pt' — a target creature; doubling P/T is unconditionally beneficial, so the single
                                 faithful pick is the controller's strongest creature (no harmful sign flip).
    The '…_pt' variants double toughness too. A creature with power 0 (or no readable stat) is a legal no-op."""
    do_tough = tgt in ("self_pt", "target_pt")
    out = D.run(state, ["controls", "creature", "power", "eff_toughness"])
    creatures = {c for (c,) in out["creature"]}
    powers = {c: int(x) for (c, x) in out["power"]}
    tough = {c: int(x) for (c, x) in out["eff_toughness"]}
    on_bf = {c for (c,) in state.get("on_battlefield", set())}

    if tgt in ("target", "target_pt"):
        mine = [c for (p, c) in out["controls"] if p == ctrl and c in creatures and c in on_bf]
        if not mine:
            print(f"    {a}: {ctrl} has no creature to double the power of")
            return
        greedy = max(mine, key=lambda c: powers.get(c, 0))
        obj = D._choose(state, "target", sorted(mine), greedy)  # §601.2c the (beneficial) target choice
    else:
        obj = src
        if obj not in creatures or obj not in on_bf:
            print(f"    {a}: {src} isn't a creature on the battlefield — no doubling")
            return

    eid = f"{a}__dbl__{obj}"
    dp = powers.get(obj, 0)
    if dp:                                                   # +P/+0 (the live power added again)
        state.setdefault("eff_mod_power", set()).add((eid, obj, dp))
    dt = tough.get(obj, 0) if do_tough else 0
    if dt:                                                   # +0/+T (the live toughness added again)
        state.setdefault("eff_mod_toughness", set()).add((eid, obj, dt))
    state.setdefault("until_eot", set()).add((eid,))         # §611.2 wears off at cleanup
    what = "power and toughness" if do_tough else "power"
    print(f"    {a}: {ctrl} doubles {obj}'s {what} until end of turn (+{dp}/+{dt})")


@applier("double_life")
def _apply_double_life(D, state, a, n, tgt, src, ctrl):
    """§107.16 'double <player>'s life total'. Read the controller's CURRENT life L and gain L more (life ->
    2L) via _adjust_life, so §614 life-gain doublers (Alhammarret's Archive) and life-gain triggers ride it
    correctly. 'target player' resolves to the controller (doubling life is beneficial, so the only faithful
    choice — never an opponent). Non-positive life (0 or below) doubles toward 0: _adjust_life(delta=L)
    leaves it unchanged at <=0, which is correct (2× a non-positive total is no better)."""
    cur = next((v for (p, v) in state.get("life", set()) if p == ctrl), None)
    if cur is None:
        return
    after = D._adjust_life(state, ctrl, cur)                 # gain L -> total doubles to 2L
    print(f"    {a}: {ctrl} doubles life total {cur} -> {after}")


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


# ── cant_block (§509.1b a turn-scoped block restriction) ──────────────────────────────────────────────
# 'Creatures without flying can't block this turn.' (Sundering Eruption). A symmetric, choice-free turn
# restriction: every non-flying creature loses the ability to be declared as a blocker until end of turn.
# We own ONLY the 'creatures_without_flying' filter (the printed wording) — any narrower/odder restriction
# abstains rather than guess which creatures it hits. The driver's declare_blockers reads _cant_block and
# drops matching creatures from the eligible-blocker pool; the turn cleanup clears it like prevent_all_combat.
@encoder("cant_block")
def _encode_cant_block(verb, amt, tgt, extra):
    if str(tgt) != "creatures_without_flying":
        return None
    return ("cant_block", 0, "without_flying")


@applier("become_copy")
def _apply_become_copy(D, state, a, n, tgt, src, ctrl):
    """§707.2 Mirage Mirror '{2}: becomes a copy of target artifact, creature, enchantment, or land until end
    of turn'. The driver targets the controller's most valuable OTHER permanent (highest mana value — a
    deterministic, beneficial self-copy, e.g. doubling a mana rock or a bomb). eff_copy(eid, src, target, ts)
    makes the engine derive the copied type/P-T/abilities; cleared at end of turn (the latest ts wins)."""
    bf = {c for (c,) in state.get("on_battlefield", set())}
    mv = {c: v for (c, v) in state.get("mana_cost", set())}
    mine = [c for (p, c) in state.get("printed_control", set()) if p == ctrl and c != src and c in bf]
    if not mine:
        print(f"    {a}: {src} has no permanent to copy")
        return
    target = max(mine, key=lambda c: (mv.get(c, 0), c))
    ts = state.get("_copy_ts", 0) + 1
    state["_copy_ts"] = ts
    eid = f"mirage__{src}"
    state["eff_copy"] = {r for r in state.get("eff_copy", set()) if r[1] != src} | {(eid, src, target, ts)}
    state.setdefault("until_eot", set()).add((eid,))
    print(f"    {a}: {src} becomes a copy of {target} until end of turn")


@applier("protect_team")
def _apply_protect_team(D, state, a, n, tgt, src, ctrl):
    """§702 Veil of Summer — the controller's permanents gain hexproof until end of turn (the 'from blue and
    from black' colour restriction is approximated as general hexproof, a superset that still dodges blue/black
    targeted removal). 'You' (player hexproof) is recorded as _player_hexproof."""
    mine = sorted(c for (p, c) in D.run(state, ["controls"])["controls"] if p == ctrl)
    for c in mine:
        eid = f"veil__{c}"
        state.setdefault("eff_grant_keyword", set()).add((eid, c, "hexproof"))
        state.setdefault("until_eot", set()).add((eid,))
    state.setdefault("_player_hexproof", set()).add((ctrl,))
    print(f"    {a}: {ctrl} and {len(mine)} permanent(s) gain hexproof until end of turn")


@applier("return_as_enchantment")
def _apply_return_as_enchantment(D, state, a, n, tgt, src, ctrl):
    """§603 the ENDURING mechanic (Enduring Vitality) — when the source dies it returns from the graveyard to
    the battlefield under its owner's control as a NONcreature ENCHANTMENT (§613: remove the creature type, add
    enchantment, PERMANENTLY — no until_eot), keeping its static ability. A no-op if it isn't in the graveyard."""
    if (src,) not in state.get("graveyard", set()):
        return
    state["graveyard"].discard((src,))
    state.setdefault("on_battlefield", set()).add((src,))
    state["printed_control"] = {(p, x) for (p, x) in state.get("printed_control", set()) if x != src} | {(ctrl, src)}
    eid = f"enduring__{src}"
    state.setdefault("eff_remove_type", set()).add((eid, src, "creature"))
    state.setdefault("eff_add_type", set()).add((eid, src, "enchantment"))
    print(f"    {a}: {src} returns to the battlefield as a noncreature enchantment (Enduring)")


@applier("cant_block")
def _apply_cant_block(D, state, a, n, tgt, src, ctrl):
    """§509.1b set a turn-scoped block restriction. tgt is the filter tag the driver's declare_blockers
    honors ('without_flying' -> a creature with no flying keyword can't be declared as a blocker this turn)."""
    state.setdefault("_cant_block", set()).add((str(tgt),))
    print(f"    {a}: creatures ({str(tgt).replace('_', ' ')}) can't block this turn")


# §509.1b the SELF-scope 'this creature can't be blocked' reading needs a bridge ENCODE entry or it never
# reaches the applier (the applier writes cant_be_blocked(src), which is correct only when the resolving
# source IS the creature). 'self' and 'it' both denote the source in these single-clause self-referential
# abilities ("whenever this attacks, it can't be blocked this turn"); a TARGET_* scope (driver target-pick)
# or the anaphoric 'that_creature' ABSTAINS here — those belong to the driver's target-verb path, not us.
@encoder("cant_be_blocked")
def _encode_cant_be_blocked(verb, amt, tgt, extra):
    if str(tgt) not in ("self", "it"):
        return None
    return ("cant_be_blocked", 0, str(tgt))


@applier("cant_be_blocked")
def _apply_cant_be_blocked(D, state, a, n, tgt, src, ctrl):
    """§509.1b SELF-scope 'this creature can't be blocked' (a triggered/activated/spell self-effect — e.g.
    Glassdust Hulk's combat trigger, Frilled Sea Serpent's static-style ability). Writes the engine input
    cant_be_blocked(src); illegal_block(B,src) then forbids any blocker, honored by BOTH the engine combat
    and env._legal_block_pairs. Turn-scoped: end-of-turn cleanup clears the relation (like prevent_all_combat).
    A spell with a 'self' target (no battlefield creature) just writes a row that matches no attacker — inert."""
    state.setdefault("cant_be_blocked", set()).add((str(src),))
    print(f"    {a}: {src} can't be blocked this turn")
