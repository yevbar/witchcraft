"""card_effects.py — parse a single oracle EFFECT sentence into a grounded, simulation-ready tuple.

Shared by every ability kind (spell / activated / triggered): the same "deal N damage to TARGET" is
one effect whether it's an instant or a creature's ability. An effect is (verb, amount, target):
  verb    a grounded action (ground.effect_verbs — §701 keyword actions + verified core actions)
  amount  an int, a "+x/+y" boost, or "-" when not applicable
  target  a normalized target spec (any_target, target_creature, all_creatures, you, each_player, …)

Curated, anchored templates — faithful or abstain (returns None), never a lossy guess. This mirrors
the rules-side content builders: a frame we don't recognize cleanly stays uncovered.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import ground

_NUMWORD = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
            "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "x": "X"}

# a target noun phrase the templates share. Order matters (longest first inside the alternation).
_TGT = (r"(?:any target|up to \w+ target[\w' -]*?|(?:\w+ )?target [\w' -]+?|"
        r"each [\w' -]+?|all [\w' -]+?|(?:attacking|blocking) [\w' -]+?|"
        r"(?:[\w-]+ )?[\w-]+ (?:you control|you don't control|your opponents control|an opponent controls|they control)|"
        r"enchanted \w+|equipped \w+|the exiled cards?|those [\w-]+|"
        r"that [\w' -]+?'s (?:controller|owner)|that [\w'-]+|"
        r"~|it|them|they|you|its controller|its owner|their controller)")


def _amount(s: str):
    s = s.strip().lower()
    if s in _NUMWORD:
        return _NUMWORD[s]
    if s.isdigit():
        return int(s)
    return None


_SYM_RE = re.compile(r"\{[^}]+\}")


def _mana_production(what: str):
    """Parse the object of 'Add …' into a list of GROUNDED mana descriptors, or None if it isn't a
    clean mana production. Each is a rules color (§105/§107.4), 'colorless', 'any_color', or
    'any_one_color'; a choice 'X or Y' becomes 'X_or_Y'. Conditional/variable amounts abstain."""
    w = what.strip().rstrip(".").strip()
    sc = ground.symbol_color()
    syms = _SYM_RE.findall(w)
    if syms and _SYM_RE.sub("", w).strip() == "":
        out = []
        for s in syms:
            if s in sc:
                out.append(sc[s])
            elif s == "{C}":
                out.append("colorless")
            else:
                return None
        return out
    if " or " in w and syms:
        choices = [sc.get(s) or ("colorless" if s == "{C}" else None) for s in syms]
        return ["_or_".join(choices)] if all(choices) else None
    m = re.fullmatch(r"(one|two|three|four|five|six) mana of any color", w, re.I)
    if m:
        return ["any_color"] * _NUMWORD[m.group(1).lower()]
    m = re.fullmatch(r"(one|two|three|four|five|six) mana of any one color", w, re.I)
    if m:
        return ["any_one_color"] * _NUMWORD[m.group(1).lower()]
    m = re.fullmatch(r"(one|two|three|four|five|six) mana of the chosen color", w, re.I)
    if m:
        return ["chosen_color"] * _NUMWORD[m.group(1).lower()]
    m = re.fullmatch(r"(one|two|three|four|five|six) mana in any combination of colors", w, re.I)
    if m:
        return ["any_combination"] * _NUMWORD[m.group(1).lower()]
    m = re.fullmatch(r"(?:one|a) mana of any color in your commander's color identity", w, re.I)
    if m:
        return ["commander_color_identity"]      # §903.4 color identity restricts which colors
    m = re.fullmatch(r"(?:one|a) mana of any (?:color|type) that a land (you control|an opponent controls) could produce", w, re.I)
    if m:
        return ["land_could_produce_" + ground.slug(m.group(1))]
    return None


def _target(s: str) -> str:
    s = s.strip().rstrip(".")
    if s == "~":
        return "self"                         # the card naming itself — reliably self
    if s == "it":
        return "it"                           # an anaphor (the trigger's subject etc.) — left for the
        # engine to resolve from context; collapsing it to 'self' would be wrong (e.g. 'exile it' where
        # 'it' is the sacrificed permanent, not this card).
    return ground.slug(s) or "self"


@dataclass(frozen=True)
class Effect:
    verb: str
    amount: object   # int | str("X") | str("+x/+y") | "-"
    target: str
    extra: str = "-"  # secondary arg: counter kind, token spec, mana produced, granted keyword
    cond: str = "-"   # optionality/condition: 'may' (optional), 'if_you_did' (follows an optional), '-'

    def grounded(self) -> bool:
        return self.verb in ground.effect_verbs()


# (regex, builder) — builder(match) -> Effect. Each anchored to a clean frame.
_TEMPLATES: list[tuple[re.Pattern, object]] = []


def _t(pat):
    def deco(fn):
        _TEMPLATES.append((re.compile(pat, re.I), fn))
        return fn
    return deco


@_t(rf"^(?:~ |you )?draw (a card|an|\w+) cards?$|^draw (a) card$")
def _draw(m):
    raw = m.group(1) or m.group(2) or "a"
    n = _amount(raw.split()[0]) or (1 if raw.startswith(("a", "an")) else None)
    return Effect("draw", n, "you") if n is not None else None


@_t(rf"^({_TGT}) draws? (a card|\w+) cards?$|^({_TGT}) draws? (a) card$")
def _draw_tgt(m):
    who, amt = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
    n = 1 if amt in ("a", "a card") else _amount(amt)
    return Effect("draw", n, _target(who)) if n is not None else None


@_t(rf"^(?:~|.+?) deals (\w+|\d+) damage to ({_TGT})$")
def _damage(m):
    n = _amount(m.group(1))
    return Effect("deal_damage", n if n is not None else "X", _target(m.group(2)))


@_t(rf"^(?:~|it|.+?) deals damage equal to (.+?) to ({_TGT})$")
def _damage_equal(m):
    return Effect("deal_damage", "equal_to_" + ground.slug(m.group(1)), _target(m.group(2)))


@_t(rf"^(?:~|it|.+?) deals damage to ({_TGT}) equal to (.+?)$")
def _damage_to_equal(m):
    """'<source> deals damage to <target> equal to <amount>' — the target-first phrasing of §120."""
    return Effect("deal_damage", "equal_to_" + ground.slug(m.group(2)), _target(m.group(1)))


@_t(r"^(?:~|it|.+?) deals (\d+|x|\w+) damage divided as you choose among (.+?)$")
def _damage_divided(m):
    """'<source> deals N damage divided as you choose among <targets>' — divided damage (§601.2d)."""
    n = _amount(m.group(1))
    return Effect("deal_damage", n if n is not None else ground.slug(m.group(1)),
                  ground.slug(m.group(2)), "divided")


@_t(rf"^({_TGT}) deals damage to itself equal to (.+?)$")
def _damage_self(m):
    """'<creature> deals damage to itself equal to <amount>' — self-directed damage (§120)."""
    return Effect("deal_damage", "equal_to_" + ground.slug(m.group(2)), _target(m.group(1)), "itself")


@_t(rf"^({_TGT}) reveals? the top (?:(\w+) )?cards? of (?:their|its owner's|your) library$")
def _subject_reveal_top(m):
    n = _amount(m.group(2)) if m.group(2) else 1
    return Effect("reveal", n if n is not None else 1, _target(m.group(1)))


@_t(rf"^destroy ({_TGT})$")
def _destroy(m):
    return Effect("destroy", "-", _target(m.group(1)))


@_t(rf"^exile ({_TGT})$")
def _exile(m):
    return Effect("exile", "-", _target(m.group(1)))


@_t(rf"^(?:({_TGT}) )?gains? (\w+) life$")
def _gain(m):
    n = _amount(m.group(2))
    return Effect("gain_life", n, _target(m.group(1) or "you")) if n is not None else None


@_t(rf"^(?:({_TGT}) )?gains? (\w+) life for each (.+?)$")
def _gain_foreach(m):
    n = _amount(m.group(2))
    return Effect("gain_life", (str(n) if n is not None else ground.slug(m.group(2))) + "_per_" + ground.slug(m.group(3)),
                  _target(m.group(1) or "you"))


@_t(rf"^(?:({_TGT}) )?loses? (\w+) life$")
def _lose(m):
    n = _amount(m.group(2))
    return Effect("lose_life", n, _target(m.group(1) or "you")) if n is not None else None


@_t(rf"^put ({_TGT}) into your hand$")
def _to_hand(m):
    return Effect("return_to_hand", "-", _target(m.group(1)))


@_t(rf"^return ({_TGT}) from your graveyard to your hand$")
def _regrowth(m):
    return Effect("return_to_hand", "-", _target(m.group(1)), "from_graveyard")


@_t(r"^exile the top (?:(\w+) )?cards? of ([\w' ]+?) librar(?:y|ies)$")
def _exile_top(m):
    n = _amount(m.group(1)) if m.group(1) else 1
    owner = "library" if m.group(2).lower() == "your" else "top_of_" + ground.slug(m.group(2)) + "_library"
    return Effect("exile", n, "top_of_library" if m.group(2).lower() == "your" else owner) if n is not None else None


@_t(r"^reveal the top (?:(\w+) )?cards? of ([\w' ]+?) librar(?:y|ies)$")
def _reveal_top(m):
    n = _amount(m.group(1)) if m.group(1) else 1
    tgt = "top_of_library" if m.group(2).lower() == "your" else "top_of_" + ground.slug(m.group(2)) + "_library"
    return Effect("reveal", n, tgt) if n is not None else None


@_t(r"^manifest the top card of your library$")
def _manifest_top(m):
    return Effect("manifest", 1, "top_of_library")


@_t(r"^clash with an opponent$")
def _clash(m):
    return Effect("clash", "-", "you")


@_t(rf"^({_TGT}) gets? ([+-](?:\d+|X)/[+-](?:\d+|X)) until end of turn$")
def _boost(m):
    # P/T delta may be a §107.3 variable X ('-X/-X'); recorded verbatim, still grounded in modify_pt.
    return Effect("modify_pt", m.group(2).replace(" ", ""), _target(m.group(1)))


@_t(rf"^({_TGT}) gets? ([+-]\d+/[+-]\d+) until end of turn for each (.+?)$")
def _boost_foreach(m):
    """'<X> gets +N/+N until end of turn for each <thing>' — a count-scaled pump (§107.3)."""
    return Effect("modify_pt", m.group(2) + "_per_" + ground.slug(m.group(3)),
                  _target(m.group(1)), "until_end_of_turn")


@_t(rf"^({_TGT}) gets? ([+-](?:\d+|X)/[+-](?:\d+|X))$")
def _boost_bare(m):
    # bare P/T delta with no stated duration — the duration (if any) is supplied by a wrapper such as
    # _UNTIL ('Until end of turn, <X> gets +N/+N'); on its own it is a continuous modify_pt.
    return Effect("modify_pt", m.group(2).replace(" ", ""), _target(m.group(1)))


@_t(rf"^counter (target [\w ]+? spell[\w ]*?|{_TGT})$")
def _counter(m):
    return Effect("counter", "-", _target(m.group(1)))


@_t(rf"^scry (\w+)$")
def _scry(m):
    n = _amount(m.group(1))
    return Effect("scry", n, "you") if n is not None else None


@_t(rf"^surveil (\w+)$")
def _surveil(m):
    n = _amount(m.group(1))
    return Effect("surveil", n, "you") if n is not None else None


@_t(rf"^(?:(target [\w ]+?|each [\w ]+?|you) )?mills? (\w+) cards?$")
def _mill(m):
    n = _amount(m.group(2))
    return Effect("mill", n, _target(m.group(1) or "you")) if n is not None else None


@_t(rf"^(tap|untap) ({_TGT})$")
def _taputap(m):
    return Effect(m.group(1).lower(), "-", _target(m.group(2)))


@_t(rf"^return ({_TGT}) to (?:its owner's hand|your hand|their owners' hands?|its owner's hands?)$")
def _bounce(m):
    return Effect("return_to_hand", "-", _target(m.group(1)))


@_t(r"^you get ((?:\{e\})+)$")
def _get_energy(m):
    return Effect("get_energy", m.group(1).count("{"), "you")


@_t(rf"^(?:({_TGT}) )?adds? (?:an additional |additional )?(.+)$")
def _add_mana(m):
    """'Add {G}' / '<player> adds {G}' / 'add an additional {C}' as an EFFECT (§106)."""
    prod = _mana_production(m.group(2))
    if not prod:
        return None
    return Effect("add_mana", len(prod), _target(m.group(1) or "you"), "_".join(dict.fromkeys(prod)))


@_t(rf"^({_TGT}) perpetually gets ([+-]\d+/[+-]\d+)$")
def _perpetual(m):
    return Effect("modify_pt", m.group(2), _target(m.group(1)), "-", "perpetual")


@_t(rf"^switch ({_TGT})'s power and toughness(?: until end of turn)?$")
def _switch_pt(m):
    return Effect("switch_pt", "-", _target(m.group(1)))


@_t(rf"^put (a|an|one|two|three|x|\w+) ([+-]\d+/[+-]\d+|[\w ]+?) counters? on ({_TGT})$")
def _put_counter(m):
    n = _amount(m.group(1))
    return Effect("put_counter", n if n is not None else "X", _target(m.group(3)),
                  ground.slug(m.group(2)) if "/" not in m.group(2) else m.group(2))


@_t(r"^put them back in any order$")
def _put_back_any_order(m):
    """'Put them back in any order' — reorder looked-at cards on top of the library (§401, scry-like)."""
    return Effect("put_on_top", "-", "them", "any_order")


@_t(rf"^put (its|all|all of its) counters on ({_TGT})$")
def _move_counters(m):
    """'Put its/all counters on <target>' — moving existing counters (§122) to another permanent."""
    return Effect("put_counter", ground.slug(m.group(1)), _target(m.group(2)), "moved")


@_t(rf"^put that many ([+-]\d+/[+-]\d+|[\w ]+?) counters? on ({_TGT})$")
def _put_counter_many(m):
    return Effect("put_counter", "that_amount", _target(m.group(2)),
                  ground.slug(m.group(1)) if "/" not in m.group(1) else m.group(1))


@_t(rf"^create a token that's a copy of ({_TGT})$")
def _create_copy(m):
    return Effect("create", 1, "token", "copy_of_" + _target(m.group(1)))


@_t(r"^discard your hand$")
def _discard_hand(m):
    return Effect("discard", "all", "you")


@_t(r"^(?:after this phase, )?there is an additional combat phase$")
def _extra_combat(m):
    return Effect("extra_combat", "-", "you")


@_t(rf"^(?:({_TGT}) )?skips? (?:your|its|their|his or her) (?:next )?([\w ]+? (?:step|phase)|turn)$")
def _skip(m):
    """'Skip your <step/phase/turn>' — a §500.7/§502+ skip effect (grounded skip action)."""
    return Effect("skip", "-", _target(m.group(1) or "you"), ground.slug(m.group(2)))


@_t(r"^(?:you )?create (a|an|one|two|three|x|\w+) (.+?) tokens?(?: .*)?$")
def _create_token(m):
    n = _amount(m.group(1))
    return Effect("create", n if n is not None else "X", "token", ground.slug(m.group(2)))


@_t(r"^(?:you )?(lose|win) the game$")
def _game_end(m):
    return Effect(m.group(1).lower() + "_game", "-", "you")


@_t(r"^cast (the copy|that card|it|~|that [\w ]+?)(?: this turn| without paying its mana cost)?$")
def _cast_plain(m):
    return Effect("cast", "-", _target(m.group(1)))


@_t(r"^put a ([\w ]+?) card from your hand onto the battlefield( tapped)?$")
def _put_from_hand(m):
    return Effect("return_to_battlefield", "-", ground.slug(m.group(1)) + "_card",
                  "from_hand_tapped" if m.group(2) else "from_hand")


_ZONE = {"hand": "put_in_hand", "graveyard": "put_in_graveyard"}


@_t(r"^(?:put )?((?:(?! into )(?! and ).)+?) into (?:your|its owner's|their) (hand|graveyard)$")
def _put_zone(m):
    """'Put <cards> into your hand/graveyard' — a §400.7 zone change of looked-at/revealed cards. The
    object excludes ' into '/' and ' so a compound ('… into your hand and the rest into your
    graveyard') won't be swallowed whole — it falls through to the body splitter and each half (the
    second being the verb-less 'the rest into your graveyard') parses as its own grounded zone-move."""
    return Effect(_ZONE[m.group(2)], "-", "you", ground.slug(m.group(1)))


@_t(rf"^(?:({_TGT}) )?loses? that much life$")
def _lose_that_much(m):
    return Effect("lose_life", "that_amount", _target(m.group(1) or "you"))


@_t(rf"^(?:({_TGT}) )?discards? (\w+) cards?(?: at random)?$")
def _discard(m):
    n = _amount(m.group(2))
    return Effect("discard", n, _target(m.group(1) or "you")) if n is not None else None


@_t(rf"^(?:({_TGT}) )?discards? (their hand|those cards|that card|all the cards in their hand)$")
def _discard_set(m):
    return Effect("discard", "-", _target(m.group(1) or "you"), ground.slug(m.group(2)))


@_t(r"^shuffle(?: your library| (?:it|them|.+?) into (?:your|its owner's|their owner's) library)?$")
def _shuffle(m):
    return Effect("shuffle", "-", "you")


@_t(rf"^(?:({_TGT}) )?draws? an additional card$")
def _draw_additional(m):
    return Effect("draw", 1, _target(m.group(1) or "you"), "additional")


@_t(rf"^look at (?:the top (?:(\w+) )?cards? of )?({_TGT})(?:'s)? (?:hand|library)$")
def _look_at(m):
    n = _amount(m.group(1)) if m.group(1) else 1
    return Effect("look", n if n is not None else 1, _target(m.group(2)))


@_t(rf"^(?:({_TGT}) )?(?:gains?|ha(?:s|ve)) ([\w ]+?) until end of turn$")
def _gain_kw_eot(m):
    kw = ground.slug(m.group(2))
    if kw not in ground.keyword_abilities() and kw.split("_")[0] not in ground.keyword_abilities():
        return None                           # only a real §702 keyword grant — else abstain
    return Effect("grant_keyword", "until_end_of_turn", _target(m.group(1) or "~"), kw)


@_t(rf"^prevent the next (\w+) damage that would be dealt to ({_TGT}) this turn$")
def _prevent(m):
    n = _amount(m.group(1))
    return Effect("prevent_damage", n if n is not None else "X", _target(m.group(2)))


@_t(r"^prevent all (combat )?damage that would be dealt this turn$")
def _fog(m):
    return Effect("prevent_damage", "all", "combat" if m.group(1) else "all")


@_t(r"^prevent all (combat |noncombat )?damage that would be dealt (.+?)$")
def _prevent_all_scoped(m):
    """'Prevent all [combat|noncombat] damage that would be dealt <scope>' (§615). The scope ('to ~',
    'this turn to creatures you control', 'by enchanted creature', …) is recorded as a faithful slug."""
    kind = (m.group(1) or "").strip()
    scope = ((kind + " ") if kind else "") + m.group(2).strip()
    return Effect("prevent_damage", "all", "-", ground.slug(scope))


@_t(r"^([\w' ]+?) (\d+|one|two|three|four|five|x)$")
def _kwaction_n(m):
    """A §701 keyword action that takes a number — 'Monstrosity 3', 'Amass 2', 'Proliferate'… ."""
    v = ground.slug(m.group(1))
    n = _amount(m.group(2))
    return Effect(v, n if n is not None else "-", "you") if v in ground.keyword_actions() else None


@_t(r"^([\w' ]+)$")
def _bare_action(m):
    """A bare §701 keyword action with no target — 'investigate', 'venture into the dungeon',
    'the ring tempts you', 'open an attraction'…  Whole clause must slug to a grounded action."""
    v = ground.slug(m.group(1))
    return Effect(v, "-", "you") if v in ground.keyword_actions() else None


@_t(rf"^(?:({_TGT}) )?draws? that many cards$")
def _draw_that_many(m):
    return Effect("draw", "that_amount", _target(m.group(1) or "you"))


@_t(rf"^(?:({_TGT}) )?gains? that much life$")
def _gain_that_much(m):
    return Effect("gain_life", "that_amount", _target(m.group(1) or "you"))


@_t(rf"^attach (?:~|it) to ({_TGT})$")
def _attach(m):
    return Effect("attach", "-", _target(m.group(1)))


@_t(r"^you become the monarch$")
def _monarch(m):
    return Effect("become_monarch", "-", "you")


@_t(r"^you take the initiative$")
def _initiative(m):
    return Effect("take_initiative", "-", "you")


@_t(r"^(?:you )?skip your (draw step|next draw step|untap step|combat phase|next combat phase|draw|next turn|next combat)$")
def _skip(m):
    return Effect("skip", "-", ground.slug(m.group(1)))


@_t(rf"^(?:you )?(?:gain )?control (?:of )?({_TGT})$")
def _control(m):
    return Effect("gain_control", "-", _target(m.group(1)))


@_t(rf"^(\w+) ({_TGT})$")
def _verb_target(m):
    """Generic '<grounded verb> <target>' — regenerate/goad/detain/sacrifice/tap/… target X.
    parse_effect's grounded() check rejects any first-word that isn't a rules action."""
    return Effect(ground.slug(m.group(1)), "-", _target(m.group(2)))


@_t(r"^pay ((?:\{[^}]+\})+|\w+ life)$")
def _pay(m):
    return Effect("pay", m.group(1).replace(" ", "_"), "you")


@_t(r"^flip a coin$")
def _flip(m):
    return Effect("flip_coin", "-", "you")


@_t(r"^sacrifice (a|an|another|two|three) ([\w ]+?)$")
def _sacrifice_a(m):
    n = _amount(m.group(1))
    return Effect("sacrifice", n if isinstance(n, int) else "-", ground.slug(m.group(1) + " " + m.group(2)))


@_t(r"^put (.+?) on the bottom of your library(?: in (?:a |any )?(?:random )?order)?$")
def _put_bottom(m):
    return Effect("put_on_bottom", "-", "library", ground.slug(m.group(1)))


@_t(rf"^put ({_TGT}) on the bottom of (?:its owner's|their owner's|your) library$")
def _put_bottom_tgt(m):
    return Effect("put_on_bottom", "-", _target(m.group(1)))


@_t(rf"^put ({_TGT}) on top(?: of (?:its owner's|their owner's|your) library)?$")
def _put_top_tgt(m):
    # the bare 'put that card on top' form (after a shuffle) refers to the library top by §401 default.
    return Effect("put_on_top", "-", _target(m.group(1)))


@_t(r"^look at the top (?:(\w+) )?cards? of your library$")
def _look_top(m):
    n = _amount(m.group(1)) if m.group(1) else 1
    return Effect("look", n, "top_of_library") if n is not None else None


@_t(rf"^({_TGT}) can't (be blocked|block|attack) this turn$")
def _cant_combat(m):
    return Effect("cant_" + m.group(2).replace(" ", "_"), "-", _target(m.group(1)))


@_t(rf"^return ({_TGT}) from your graveyard to the battlefield( tapped)?$")
def _reanimate(m):
    return Effect("return_to_battlefield", "-", _target(m.group(1)), "tapped" if m.group(2) else "-")


@_t(rf"^put ({_TGT}) from (?:a|your|its owner's) graveyard onto the battlefield(?: under (?:your|its owner's|that player's) control)?( tapped)?$")
def _reanimate_put(m):
    """'Put <card> from a graveyard onto the battlefield [under your control]' — reanimation (§614)."""
    return Effect("return_to_battlefield", "-", _target(m.group(1)),
                  "from_graveyard_tapped" if m.group(2) else "from_graveyard")


@_t(rf"^return ({_TGT}) to the battlefield(?: under (?:your|its owner's|that player's) control)?( tapped)?$")
def _return_bf(m):
    """'Return <X> to the battlefield [under its owner's control]' — a battlefield return (§614),
    typically a death/leaves trigger's reanimation of the just-departed object."""
    return Effect("return_to_battlefield", "-", _target(m.group(1)), "tapped" if m.group(2) else "-")


@_t(rf"^exile ({_TGT}) until ~ leaves the battlefield$")
def _exile_until(m):
    return Effect("exile", "-", _target(m.group(1)), "until_self_leaves")


@_t(rf"^exile ({_TGT}) with (\w+) (\w[\w ]*?) counters? on it$")
def _exile_with_counters(m):
    """'Exile <X> with N <kind> counters on it' — exile that arrives with counters (suspend etc.)."""
    return Effect("exile", "-", _target(m.group(1)), ground.slug(m.group(2) + "_" + m.group(3)))


@_t(r"^copy (that spell|that ability|it|target [\w ]+?|~)$")
def _copy(m):
    return Effect("copy", "-", _target(m.group(1)))


@_t(r"^choose new targets for the (?:copy|copies)$")
def _new_targets(m):
    return Effect("choose_new_targets", "-", "copy")


@_t(rf'^({_TGT}) (?:has|have|gains?) "(.+)"( until end of turn)?$')
def _grant_ability(m):
    """In-body granted ability '<who> has/gains "<ability>" [until end of turn]' (§613.6) — the
    ability text is slugged (quote-free), so even a rare bad split can't emit unbalanced quotes."""
    ab = ground.slug(m.group(2))[:160]
    return Effect("grant_ability", "until_end_of_turn" if m.group(3) else "-", _target(m.group(1)), ab) if ab else None


@_t(r"^amass ([\w ]+?) (\d+|one|two|three|x)$")
def _amass(m):
    n = _amount(m.group(2))
    return Effect("amass", n if n is not None else 1, "you", ground.slug(m.group(1)))


@_t(r"^choose (?:a|an|one) ([\w ]+?)$")
def _choose(m):
    return Effect("choose", "-", ground.slug(m.group(1)))


@_t(rf"^(?:({_TGT}) )?(?:takes?|take) an extra turn after this one$")
def _extra_turn(m):
    """'<player> takes an extra turn after this one' — an extra turn (§500.7)."""
    return Effect("extra_turn", "-", _target(m.group(1) or "you"))


@_t(r"^you choose (?:a|an|one) ([\w ]+?) from (?:it|among them|them)$")
def _choose_from(m):
    """'You choose a <card-kind> from it/among them' — a §700.2 choice over a set of cards."""
    return Effect("choose", "-", "you", ground.slug(m.group(1)))


@_t(rf"^({_TGT}) reveals? their hand$")
def _reveal_hand(m):
    return Effect("reveal", "-", _target(m.group(1)), "hand")


@_t(r"^reveal (?:a|an|one|up to \w+) ([\w ]+?) from among them$")
def _reveal_among(m):
    """'Reveal a <card-kind> from among them' — revealing a card out of a looked-at set (§701.16)."""
    return Effect("reveal", "-", "you", ground.slug(m.group(1)))


@_t(rf"^({_TGT}) (?:doesn't|don't) untap during (?:its controller's|their controller's|your|their)( next)? untap step$")
def _doesnt_untap_eff(m):
    return Effect("doesnt_untap", "-", _target(m.group(1)), "next" if m.group(1) and m.group(2) else "-")


@_t(r"^cast (.+?) without paying (?:its|their) mana costs?$")
def _cast_free(m):
    return Effect("cast", "-", _target(m.group(1)), "without_paying_mana_cost")


@_t(r"^you get (a|an|one|two|three|\w+) experience counters?$")
def _experience(m):
    n = _amount(m.group(1))
    return Effect("put_counter", n if n is not None else 1, "you", "experience")


@_t(r"^you gain life equal to (.+?)$")
def _gain_equal(m):
    return Effect("gain_life", "equal_to_" + ground.slug(m.group(1)), "you")


@_t(rf"^(?:({_TGT}) )?loses? life equal to (.+?)$")
def _lose_equal(m):
    return Effect("lose_life", "equal_to_" + ground.slug(m.group(2)), _target(m.group(1) or "you"))


@_t(rf"^({_TGT}) shuffles? (?:their|its owner's|his or her) (\w+) into (?:their|its owner's|his or her) library$")
def _shuffle_subj(m):
    """'<player> shuffles their graveyard/hand into their library' — a shuffle (§103.2/§701.19)."""
    return Effect("shuffle", "-", _target(m.group(1)), "from_" + ground.slug(m.group(2)))


@_t(r"^roll (a|an|one|two|three|\w+) (d\d+)s?$")
def _roll(m):
    n = _amount(m.group(1))
    return Effect("roll_die", n if n is not None else 1, "you", m.group(2).lower())


_SIDED = {"four": 4, "six": 6, "eight": 8, "ten": 10, "twelve": 12, "twenty": 20, "100": 100}


@_t(r"^roll (a|an|one|two|three|\w+) ([\w]+)-sided (?:die|dice)$")
def _roll_sided(m):
    n = _amount(m.group(1))
    sides = _SIDED.get(m.group(2).lower()) or (int(m.group(2)) if m.group(2).isdigit() else None)
    return Effect("roll_die", n if n is not None else 1, "you", f"d{sides}") if sides else None


@_t(r"^play (that card|those cards|them|it|~|the (?:top|exiled) cards?[\w ]*?|that [\w ]+?)(?: this turn| until [\w ' ]+)?$")
def _play(m):
    return Effect("play", "-", _target(m.group(1)))


@_t(r"^play (?:an additional|up to (?:one|two|\w+) additional) lands?(?: this turn)?$")
def _extra_land(m):
    """'[You may] play an additional land this turn' — a one-shot extra-land permission (§116.2a/
    §505.5b). The 'you may' prefix is handled by the wrapper layer, so this matches the bare verb."""
    return Effect("play", "-", "you", "additional_land_this_turn")


@_t(rf"^return ({_TGT}) to the battlefield(?: transformed)?(?: under (?:its owner's|your) control)?( tapped)?$")
def _return_bf(m):
    return Effect("return_to_battlefield", "-", _target(m.group(1)), "tapped" if m.group(2) else "-")


@_t(rf"^({_TGT}) becomes? an? (\d+/\d+)([\w' -]*?)(?: with [\w, ]+?)?(?: until end of turn)?$")
def _becomes(m):
    """'<target> becomes a N/N [colors/types] [creature] [until end of turn]' — animate / set P/T
    & types (§613.3 / §205). The type tail is recorded as a descriptive slug."""
    return Effect("becomes", m.group(2), _target(m.group(1)), ground.slug(m.group(3)) or "-")


@_t(rf"^({_TGT}) becomes? the (.+?) of your choice(?: until end of turn)?$")
def _becomes_choice(m):
    return Effect("becomes", "-", _target(m.group(1)), "chosen_" + ground.slug(m.group(2)))


@_t(r"^(?:it|~) enters with (\w+) ([+-]\d+/[+-]\d+) counters? on it$")
def _enters_counters_eff(m):
    n = _amount(m.group(1))
    return Effect("put_counter", n if n is not None else 1, "self", m.group(2))


@_t(rf"^remove (a|an|one|two|three|\w+) ([+-]\d+/[+-]\d+|[\w ]+?) counters? from ({_TGT})$")
def _remove_counter(m):
    n = _amount(m.group(1))
    kind = m.group(2) if "/" in m.group(2) else ground.slug(m.group(2))
    return Effect("remove_counter", n if n is not None else 1, _target(m.group(3)), kind)


@_t(rf"^({_TGT}) discards? that card$")
def _discard_that(m):
    return Effect("discard", "that_amount", _target(m.group(1)))


@_t(rf"^({_TGT}) is goaded$")
def _is_goaded(m):
    """'<creature> is goaded' — the §701.38 goad keyword action applied as a continuous effect."""
    return Effect("goad", "-", _target(m.group(1)))


@_t(rf"^({_TGT}) fights ({_TGT})$")
def _fight(m):
    """'<A> fights <B>' — the §701.12 fight keyword action (each deals damage equal to its power to
    the other). Recorded as a single grounded fight effect between the two creatures."""
    return Effect("fight", "-", _target(m.group(1)), _target(m.group(2)))


@_t(rf"^(?:({_TGT}) )?(?:gains?|ha(?:s|ve)) ([\w ]+?)$")
def _gains_perm(m):
    """'<target> gains/has <kw>' with NO duration — a permanent keyword grant (§613)."""
    kw = _kw_ok(m.group(2))
    return Effect("grant_keyword", "-", _target(m.group(1) or "~"), kw) if kw else None


@_t(rf"^({_TGT}) sacrifices? (a|an|one|two|three|\w+) (.+)$")
def _sacrifice_subj(m):
    return Effect("sacrifice", "-", _target(m.group(1)), ground.slug(m.group(2) + " " + m.group(3)))


@_t(rf"^have ({_TGT}) deals? (\w+) damage to ({_TGT})$")
def _have_deal(m):
    n = _amount(m.group(2))
    return Effect("deal_damage", n if n is not None else "X", _target(m.group(3)), "by_" + _target(m.group(1)))


@_t(rf"^have ({_TGT}) gets? ([+-]\d+/[+-]\d+) until end of turn$")
def _have_get(m):
    return Effect("modify_pt", m.group(2), _target(m.group(1)))


@_t(rf"^tap or untap ({_TGT})$")
def _tap_or_untap(m):
    return Effect("untap", "-", _target(m.group(1)), "or_tap")


@_t(rf"^(?:({_TGT}|they) )?can't be regenerated$")
def _cant_regen(m):
    return Effect("cant_be_regenerated", "-", _target(m.group(1) or "it"))


@_t(r"^search your library for ([^,]+?)$")
def _search(m):
    return Effect("search", "-", ground.slug(m.group(1)))


@_t(rf"^put ({_TGT}) onto the battlefield( tapped)?$")
def _to_battlefield(m):
    return Effect("return_to_battlefield", "-", _target(m.group(1)), "tapped" if m.group(2) else "-")


@_t(r"^put a ([\w ]+?) card from among them onto the battlefield( tapped)?$")
def _put_among_bf(m):
    """'Put a <kind> card from among them onto the battlefield' — putting a looked-at card into play."""
    return Effect("return_to_battlefield", "-", ground.slug(m.group(1)) + "_card",
                  "from_among_tapped" if m.group(2) else "from_among")


@_t(rf"^({_TGT}) (\w+)$")
def _subject_action(m):
    """A §701 keyword action performed by an object — 'it explores', 'it connives', 'that creature
    investigates'. Third-person 's' is stripped to match the grounded action."""
    base = ground.slug(m.group(2)).rstrip("s") or ground.slug(m.group(2))
    if ground.slug(m.group(2)) in ground.keyword_actions():
        base = ground.slug(m.group(2))
    elif base not in ground.keyword_actions():
        return None
    return Effect(base, "-", _target(m.group(1)))


def parse_effect(sentence: str) -> "Effect | None":
    """A single effect sentence -> grounded Effect, or None (abstain). Only emits if verb is grounded."""
    s = sentence.strip().rstrip(".").strip()
    for pat, fn in _TEMPLATES:
        m = pat.match(s)
        if m:
            e = fn(m)
            if e and e.grounded():
                return e
    return None


import dataclasses as _dc

_MAY = re.compile(r"^you may (.+)$", re.I)
_IF_YOU_DO = re.compile(r"^if you do,?\s+(.+)$", re.I)
_IF_COND = re.compile(r"^if (?!you do\b)(.+?), (.+)$", re.I)
_UNLESS_PAY = re.compile(r"^(.+?) unless (?:its controller|you|that player|they) pays? (.+)$", re.I)
_UNLESS = re.compile(r"^(.+?) unless (.+)$", re.I)
_DELAYED = re.compile(r"^(.+?) (?:at the beginning of (?:the next turn's upkeep|your next upkeep|"
                      r"the next end step|your next end step|the next turn's end step|your upkeep)|"
                      r"at end of combat|at the beginning of the next turn)$", re.I)
_UNTIL = re.compile(r"^until (end of turn|your next turn|the end of your next turn|end of combat),\s+(.+)$", re.I)
_IF_TRAIL = re.compile(r"^(.+?) if (.+)$", re.I)


def _kw_ok(phrase: str):
    kw = ground.slug(phrase)
    return kw if (kw in ground.keyword_abilities() or kw.split("_")[0] in ground.keyword_abilities()) else None


_EOT_PUMP = re.compile(rf"^({_TGT}) gets? ([+-]\d+/[+-]\d+)((?: and gains? [\w ]+?)+) until end of turn$", re.I)
_EOT_GRANTS = re.compile(rf"^({_TGT}) gains? ([\w ]+?(?: and [\w ]+?)+) until end of turn$", re.I)
_EOT_PUMP_CANT = re.compile(rf"^({_TGT}) gets? ([+-]\d+/[+-]\d+) until end of turn and (can't (?:be blocked|block|attack)) this turn$", re.I)


def _eot_compound(s: str):
    """A compound until-end-of-turn buff -> MULTIPLE effects: '<t> gets +N/+N and gains trample …' or
    '<t> gains flying and lifelink …'. Abstains unless every granted word is a real §702 keyword."""
    m = _EOT_PUMP_CANT.match(s)
    if m:
        who = _target(m.group(1))
        return [Effect("modify_pt", m.group(2), who),
                Effect(m.group(3).replace("can't ", "cant_").replace(" ", "_"), "-", who)]
    m = _EOT_PUMP.match(s)
    if m:
        who = _target(m.group(1))
        out = [Effect("modify_pt", m.group(2), who)]
        for g in re.findall(r"gains? ([\w ]+?)(?= and gains| until|$)", m.group(3), re.I):
            kw = _kw_ok(g)
            if not kw:
                return None
            out.append(Effect("grant_keyword", "until_end_of_turn", who, kw))
        return out
    m = _EOT_GRANTS.match(s)
    if m:
        who = _target(m.group(1))
        out = []
        for g in re.split(r" and ", m.group(2)):
            kw = _kw_ok(g)
            if not kw:
                return None
            out.append(Effect("grant_keyword", "until_end_of_turn", who, kw))
        return out
    return None


def parse_clauses(sentence: str) -> "list | None":
    """parse a clause into one OR MORE effects (compound until-EOT buffs yield several); else None."""
    multi = _eot_compound(sentence.strip().rstrip("."))
    if multi:
        return multi
    e = parse_clause(sentence)
    return [e] if e else None


def parse_clause(sentence: str) -> "Effect | None":
    """Like parse_effect, but recognizes the optional/conditional wrappers that dominate the tail:
    'you may <effect>' -> cond='may'; 'if you do, <effect>' -> cond='if_you_did' (follows an optional);
    'if <condition>, <effect>' -> cond=<condition slug> (a descriptive predicate, like a trigger slug).
    Abstains if the inner effect isn't grounded."""
    s = sentence.strip().rstrip(".").strip()
    s = re.sub(r"^(?:then|otherwise),?\s+", "", s, flags=re.I)   # discourse lead — 'Then/Otherwise shuffle'
    s = re.sub(r"\s+instead$", "", s, flags=re.I)               # replacement tail — 'exile it instead' -> 'exile it'
    m = _MAY.match(s)
    if m:
        return _combine(parse_clause(m.group(1)), "may")
    m = _IF_YOU_DO.match(s)
    if m:
        return _combine(parse_clause(m.group(1)), "if_you_did")
    m = _IF_COND.match(s)
    if m:
        return _combine(parse_clause(m.group(2)), ground.slug(m.group(1)), suffix=True)
    m = _UNLESS_PAY.match(s)
    if m:
        return _combine(parse_clause(m.group(1)), "unless_pay_" + ground.slug(m.group(2)))
    m = _UNLESS.match(s)
    if m:
        return _combine(parse_clause(m.group(1)), "unless_" + ground.slug(m.group(2)))
    m = _DELAYED.match(s)
    if m:
        return _combine(parse_clause(m.group(1)), "delayed")
    m = _UNTIL.match(s)
    if m:
        inner = parse_clause(m.group(2))
        if not inner:
            return None
        return inner if inner.cond != "-" else _dc.replace(inner, cond="until_" + ground.slug(m.group(1)))
    e = parse_effect(s)
    if e:
        return e
    m = _IF_TRAIL.match(s)         # '<effect> if <condition>' — trailing conditional
    if m:
        return _combine(parse_clause(m.group(1)), ground.slug(m.group(2)), suffix=True)
    return None


def _combine(inner, cond, suffix=False):
    """Attach a wrapper condition to an already-parsed inner clause. If the inner clause already carries
    a condition (e.g. a nested 'you may'), keep both by joining them ('<inner>__if_<cond>' for the
    if-style wrappers, '<cond>__<inner>' otherwise) so no grounded condition is silently dropped."""
    if not inner:
        return None
    if inner.cond == "-":
        return _dc.replace(inner, cond=cond)
    joined = inner.cond + "__if_" + cond if suffix else cond + "__" + inner.cond
    return _dc.replace(inner, cond=joined)


if __name__ == "__main__":
    for s in ["Draw a card", "Draw two cards", "~ deals 3 damage to any target",
              "Destroy all creatures", "Destroy target creature", "You gain 3 life",
              "Target creature gets +1/+1 until end of turn", "Counter target spell",
              "Scry 2", "Target player draws two cards", "Exile target permanent",
              "Return target creature to its owner's hand", "Untap target creature",
              "Goad target creature"]:
        print(f"  {s:50} -> {parse_effect(s)}")
