"""card_lark.py — a lark CFG for the card-effect clause sublanguage, transforming to the SAME
`Effect(verb, amount, target, extra, cond)` IR the regex templates produce.

This is the principled replacement for the accreted regex in `card_effects.py`: terse card clauses
are a *regular* sublanguage (not free English, which is why spaCy mis-roots them), so a context-free
grammar parses them deterministically and faithfully. The grammar owns the STRUCTURE (verb, object
span, prepositional zone, amount, wrapper conditions); the formal fragments (mana `{..}`, P/T `±n/±n`)
stay masked at the lexer level (the right place for a regular sublanguage); and the transformer reuses
`card_effects._target`/`_amount` so every slug is byte-identical to the regex output.

Built incrementally and OVERLAP-GATED against the regex (`card_effects.parse_clause`) corpus-wide: a
clause shape is only owned here once `lark(clause) == regex(clause)` on every card that grounds it.
"""
from __future__ import annotations

import re

from lark import Lark, Transformer, v_args

import ground
from card_effects import Effect, _target, _amount

# verbs whose grounded name == lemma (the simple object verbs); zone verbs handled separately.
# pure OBJECT verbs (the NP after the verb is the TARGET). Player-count verbs (mill/draw/discard/scry,
# where the NP is the AMOUNT and the subject is a player) are a separate production, added next.
_SIMPLE = {"destroy": "destroy", "exile": "exile", "tap": "tap", "untap": "untap",
           "sacrifice": "sacrifice", "counter": "counter", "regenerate": "regenerate",
           "goad": "goad", "detain": "detain"}
_ZONE = {"hand": "return_to_hand", "battlefield": "return_to_battlefield",
         "library": "put_on_top", "graveyard": "put_in_graveyard"}

# player-count verbs: the subject is a PLAYER and the NP after the verb is the AMOUNT. The trailing
# object word disambiguates the grounded verb (gain/lose need 'life'; draw/mill/discard need 'card[s]';
# scry/surveil take a bare number). Defaults subject to 'you' (imperative mood).
_PVERB = {"draw": "draw", "draws": "draw", "mill": "mill", "mills": "mill",
          "scry": "scry", "scries": "scry", "surveil": "surveil",
          "gain": "gain_life", "gains": "gain_life", "lose": "lose_life", "loses": "lose_life",
          "discard": "discard", "discards": "discard"}
_NEEDS_CARD = {"draw", "mill", "discard"}
_NEEDS_LIFE = {"gain_life", "lose_life"}

_GRAMMAR = r"""
start: rclause | oclause | pclause

rclause: RVERB quant? robj fromphrase? zonephrase? trailer?   -> ret   // 'return': strip from/to
oclause: OVERB quant? objall trailer?            -> imperative  // object verbs: object spans everything
pclause: psubj? PVERB pbody                       -> pcount      // player-count verbs: NP is the AMOUNT

psubj: (WORD | QUANT)+                  // a player phrase before the verb (you / each player / target player)
pbody: (WORD | NUM | QUANT)+            // amount (+ object word: 'cards'/'life')

zonephrase: TOPREP zwords? ZONE        -> zone
fromphrase: FROM zwords? ZONE          -> source
zwords: (WORD | TOPREP)+
trailer: BOUND (WORD | TOPREP | ZONE | QUANT | NUM)*   -> trailer
quant: QUANT
robj: (WORD | ZONE)+                    // return object stops at from/to
objall: (WORD | TOPREP | ZONE | FROM)+

RVERB: "return"
OVERB: %(verbs)s
PVERB.2: /\b(?:draws|draw|mills|mill|scries|scry|surveil|gains|gain|loses|lose|discards|discard)\b/
QUANT.2: /\b(?:up to (?:one|two|three|four|five|that many|x|[0-9]+)|any number of|a|an|one|two|three|four|five|target|all|each|another|x)\b/
TOPREP.2: /\b(?:to|into|onto)\b/
FROM.2: /\bfrom\b/
ZONE.2: /\b(?:hand|battlefield|library|graveyard)\b/
BOUND.3: /\b(?:until|unless|for each)\b/
WORD: /[\w',+\/~*-]+/
NUM: /[0-9]+/

%%ignore /\s+/
"""


def _verb_alt():
    vs = sorted(_SIMPLE, key=len, reverse=True)
    return " | ".join('"%s"' % v for v in vs)


# rider patterns lark defers to the regex (its convention is better there): the structured
# 'exile the top N of <library>' form, and the suspend-style 'exile X with N counters on it'.
_TOPLIB = re.compile(r"^the top (?:\w+ )?cards? of .*librar(?:y|ies)$", re.I)
_WITHCTR = re.compile(r"\bwith \w+ [\w/+ ]*?counters? on it$", re.I)
_COORD = re.compile(r"^(?:or|and)\s", re.I)        # 'tap or untap …' — a coordinated verb the leaf split wrong

# 'return' abstain guards: an object-internal preposition ('attached to it', 'equal to X') or a
# coordinated multi-object list ('return A, B, and C to …') makes the flat from/to split ambiguous;
# defer to the regex (faithful-or-abstain). 'up to N' is a quant, not a dest prep, so strip it first.
_UPTOQ = re.compile(r"\bup to (?:one|two|three|four|five|that many|x|[0-9]+)\b", re.I)


# a player subject for the count verbs is a CLOSED phrase; anything else (a swallowed first clause,
# a 'may'/'who'/'unless' wrapper, a coordinated 'X and Y') means the greedy psubj over-matched -> abstain
# and let the regex wrapper/split chain own it (faithful-or-abstain).
_PLAYER = re.compile(
    r"^(?:~|you|they|it|them|"
    r"target player|target opponent|"
    r"each player|each opponent|each other player|"
    r"that player|that opponent|the player|another player|those players|these players|"
    r"(?:the )?(?:defending|attacking|active|chosen) player|"
    r"its controller|its owner|"
    r"[\w'-]+(?: [\w'-]+)*'s controller)$", re.I)


def _ret_ambiguous(s: str) -> bool:
    if "," in s:
        return True                            # coordinated multi-object list
    return len(re.findall(r"\b(?:to|into|onto)\b", _UPTOQ.sub(" ", s))) >= 2


_PARSER = Lark(_GRAMMAR % {"verbs": _verb_alt()}, parser="earley", lexer="dynamic")


class _Quant(str):
    pass


class _Source:
    def __init__(self, zone):
        self.zone = zone


class _Subj(str):
    pass


class _Body(str):
    pass


@v_args(inline=True)
class _ToEffect(Transformer):
    def quant(self, tok):
        return _Quant(str(tok))

    def obj(self, *toks):
        return " ".join(str(t) for t in toks)

    def robj(self, *toks):
        return " ".join(str(t) for t in toks)

    def objall(self, *toks):
        return " ".join(str(t) for t in toks)

    def zwords(self, *toks):
        return " ".join(str(t) for t in toks)

    def zone(self, prep, *rest):
        sub = " ".join(str(r) for r in rest).lower()        # zwords (optional) + ZONE
        z = next((zz for w, zz in _ZONE.items() if w in sub), None)
        return _Zone(z)

    def source(self, frm, *rest):
        sub = " ".join(str(r) for r in rest).lower()        # zwords (optional) + ZONE
        w = next((w for w in _ZONE if w in sub), None)
        return _Source(w)

    def trailer(self, *toks):
        # a trailing wrapper ('until ~ leaves', 'for each …') — the LEAF stops at the object; the
        # parse_clause wrapper chain handles the wrapper. Marker so imperative() drops it.
        return _Trailer()

    def _assemble(self, rest):
        if any(isinstance(a, _Trailer) for a in rest):
            return None, None, None            # trailing wrapper -> regex chain owns it
        quant = next((str(a) for a in rest if isinstance(a, _Quant)), None)
        zone = next((a for a in rest if isinstance(a, _Zone)), None)
        obj = next((a for a in rest if isinstance(a, str) and not isinstance(a, _Quant)), None)
        otext = (((quant + " ") if quant else "") + (obj or "")).strip()
        return quant, zone, otext

    def ret(self, verb, *rest):
        if any(isinstance(a, _Trailer) for a in rest):
            return None                        # trailing wrapper -> regex chain owns it
        quant = next((str(a) for a in rest if isinstance(a, _Quant)), None)
        zone = next((a for a in rest if isinstance(a, _Zone)), None)
        src = next((a for a in rest if isinstance(a, _Source)), None)
        robj = next((a for a in rest if isinstance(a, str) and not isinstance(a, _Quant)), None)
        if zone is None or zone.verb is None or robj is None:
            return None                        # 'return' needs a 'to <zone>' + object to ground
        otext = (((quant + " ") if quant else "") + robj).strip()
        # CONSISTENT (faithful-replacement): object stops at from/to; source -> extra; dest -> verb.
        extra = ("from_" + src.zone) if (src and src.zone) else "-"
        return Effect(zone.verb, "-", _target(otext), extra)

    def imperative(self, verb, *rest):
        verb = str(verb).lower()
        quant, _zone, otext = self._assemble(rest)
        if otext is None:
            return None
        if _TOPLIB.match(otext) or _WITHCTR.search(otext) or _COORD.match(otext):
            return None                        # defer to the regex (better convention / coordinated verb)
        if verb == "sacrifice":
            n = _amount(quant) if quant in ("a", "an", "another", "two", "three") else None
            return Effect("sacrifice", n if isinstance(n, int) else "-", _target(otext))
        if verb in _SIMPLE:
            return Effect(_SIMPLE[verb], "-", _target(otext))
        return None

    def psubj(self, *toks):
        return _Subj(" ".join(str(t) for t in toks))

    def pbody(self, *toks):
        return _Body(" ".join(str(t) for t in toks))

    def pcount(self, *args):
        subj = next((str(a) for a in args if isinstance(a, _Subj)), None)
        body = next((str(a) for a in args if isinstance(a, _Body)), "")
        verb = next((str(a).lower() for a in args if not isinstance(a, (_Subj, _Body))), "")
        g = _PVERB.get(verb)
        if g is None:
            return None
        if subj is not None and not _PLAYER.match(subj.strip()):
            return None                        # greedy psubj swallowed non-player text -> abstain
        body = body.strip().lower()
        if "for each" in body or body.endswith("per turn") or " per " in body:
            return None                        # per-X amount or per-turn limit -> regex chain owns it
        who = _target(subj) if subj else "you"
        if g in _NEEDS_LIFE:
            if not body.endswith("life"):
                return None                    # 'gain control'/'gains flying' is NOT gain_life
            amt_s = body[:-4].strip()
        elif g in _NEEDS_CARD:
            if body.endswith("at random"):
                body = body[:-len("at random")].strip()   # regex drops 'at random'
            m = re.match(r"^(.*?)\s*cards?$", body)
            if not m:
                return None                    # 'discard their hand'/'discard your hand' -> other template
            amt_s = m.group(1).strip()
        else:                                  # scry / surveil — bare number, no object word
            amt_s = body
        if amt_s == "any number of":
            return Effect("discard", "any", who) if g == "discard" else None
        up_to = amt_s.startswith("up to ")
        if up_to:
            amt_s = amt_s[6:].strip()
        if amt_s in ("a", "an"):
            n = 1
        elif amt_s == "":
            return None
        else:
            n = _amount(amt_s)
        if n is None:
            return None
        if g == "discard":
            return Effect("discard", n, who, "up_to" if up_to else "-")
        if up_to:
            return None                        # 'up to N' only modeled for discard in the regex
        return Effect(g, n, who)


class _Zone:
    def __init__(self, verb):
        self.verb = verb


class _Trailer:
    pass


_T = _ToEffect()


def parse_clause_lark(clause: str):
    """A card-effect clause -> Effect via the CFG, or None (abstain). Case-folded; the grammar owns the
    imperative core + zone-moves so far."""
    s = clause.strip().rstrip(".").lower()
    if s.startswith("return ") and _ret_ambiguous(s):
        return None                            # ambiguous from/to split — defer to regex
    try:
        tree = _PARSER.parse(s)
    except Exception:
        return None
    e = _T.transform(tree)
    e = e.children[0] if hasattr(e, "children") else e
    return e if isinstance(e, Effect) else None
