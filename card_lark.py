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

import functools
import re

from lark import Lark, Transformer, v_args

import ground
from card_effects import Effect, _target, _amount, _is_compound_object, _kw_ok, _kw_list, _TGT, _LIB_OWNER, _mana_production, _SIDED
import card_effects as _ce
# 'Return <obj> [from <zone>] to the battlefield [tapped/under-ctrl/with-counter/at the beginning of <step>]'
# (composite extra slug). NOTE: card_effects has TWO `_return_bf` defs — the module attribute `_ce._return_bf`
# is the SIMPLER 2-group one that SHADOWS the complex one; the complex one (4 groups incl. named 'mods') is
# still REGISTERED in _TEMPLATES and is what parse_effect actually uses. Capture THAT registered (rx, fn) pair
# so the `ret` transformer reproduces parse_effect byte-for-byte (calling _ce._return_bf directly is buggy).
_RBF_RX, _RBF_FN = next((rx, fn) for rx, fn in _ce._TEMPLATES
                        if fn.__name__ == "_return_bf" and "mods" in rx.groupindex)
# 'Put <obj> [from <zone>] onto the battlefield [under ctrl][tapped][attached][counter]' reanimation ->
# return_to_battlefield (the registered `_reanimate_put`; unique, no shadowing). Re-matched in `pzput`.
_RPUT_RX, _RPUT_FN = next((rx, fn) for rx, fn in _ce._TEMPLATES if fn.__name__ == "_reanimate_put")
# SUBJECT-prefixed put-to-zone '<player> puts <obj> on top of/on the bottom of/into <zone>' -> put_on_top/
# put_on_bottom/put_in_* with 'by_<player>'. PARSE-FAILs every production (new `pszclause` below); the
# transformer re-matches the registered `_subject_puts` (+ `_owner_puts` 'on their choice of top/bottom').
_SP_RX, _SP_FN = next((rx, fn) for rx, fn in _ce._TEMPLATES if fn.__name__ == "_subject_puts")
_OP_RX, _OP_FN = next((rx, fn) for rx, fn in _ce._TEMPLATES if fn.__name__ == "_owner_puts")
# LEADING-put 'Put <obj> into <zone>' -> put_in_graveyard/put_in_hand (the registered `_put_zone`; unique).
# `_pz_frame` in `pzput` grounds some (hand) but abstains on others (graveyard) — re-match _put_zone on src.
_PZ_RX, _PZ_FN = next((rx, fn) for rx, fn in _ce._TEMPLATES if fn.__name__ == "_put_zone")
# '<player> gets <n> poison/energy/experience counter(s)' -> put_counter on the PLAYER. The 'gets' verb
# (not 'put') PARSE-FAILs every other production; `pgcclause` below GROUNDS IT NATIVELY from the grammar-
# captured count + kind (no card_effects template re-match).

# verbs whose grounded name == lemma (the simple object verbs); zone verbs handled separately.
# pure OBJECT verbs (the NP after the verb is the TARGET). Player-count verbs (mill/draw/discard/scry,
# where the NP is the AMOUNT and the subject is a player) are a separate production, added next.
_SIMPLE = {"destroy": "destroy", "exile": "exile", "tap": "tap", "untap": "untap",
           "sacrifice": "sacrifice", "counter": "counter", "regenerate": "regenerate",
           "goad": "goad", "detain": "detain", "seek": "seek", "draft": "draft",
           # migrated off the generic object-verb regex leaf — distinctive verbs whose REAL corpus form is
           # 'verb <object>'. cloak ('cloak those cards/them/the top card …') and abandon ('abandon this
           # scheme') ARE clean object verbs (the earlier 'cloak the top N cards' worry was wrong — every cloak
           # clause is the generic 'verb <obj>'); meld ('meld them into <result>') / behold (no effect clause) /
           # triple ('triple strike' = a keyword, not an object) are EXCLUDED — special/non-object shapes.
           "suspect": "suspect", "convert": "convert", "cloak": "cloak", "abandon": "abandon"}
_ZONE = {"hand": "return_to_hand", "battlefield": "return_to_battlefield",
         "library": "put_on_top", "graveyard": "put_in_graveyard"}

# player-count verbs: the subject is a PLAYER and the NP after the verb is the AMOUNT. The trailing
# object word disambiguates the grounded verb (gain/lose need 'life'; draw/mill/discard need 'card[s]';
# scry/surveil take a bare number). Defaults subject to 'you' (imperative mood).
_PVERB = {"draw": "draw", "draws": "draw", "mill": "mill", "mills": "mill",
          "scry": "scry", "scries": "scry", "surveil": "surveil", "surveils": "surveil",
          "blight": "blight", "blights": "blight",   # §701 blight (2025/26) — bare-number player action, like surveil
          "gain": "gain_life", "gains": "gain_life", "lose": "lose_life", "loses": "lose_life",
          "discard": "discard", "discards": "discard"}
_NEEDS_CARD = {"draw", "mill", "discard"}
_NEEDS_LIFE = {"gain_life", "lose_life"}

_GRAMMAR = r"""
start: rclause | oclause | pclause | dclause | mclause | mfeclause | cclause | cconjclause | tclause | tconjclause | gclause | gchclause | cntclause | fcastclause | pdurclause | pflashclause | pfromclause | tfaceclause | aclause
     | deqclause | dteqclause | dtmclause | ddivclause | bcmclause | bccclause | bcpclause | bchclause | bctclause | bptclause | btaoclause | bcchclause | bdgclause | bnsclause | alltclause | chsclause | rvclause | pvclause
     | sfclause | rcclause | dbclause | pzputclause | pzhandclause | lkclause | shclause | nsclause | amclause | amceqclause | amcxclause | amcfeclause | atclause | tfclause | mfclause | fgclause | litclause | pfclause | rdclause | skclause | asclause | cpclause | mrclause | xtclause | xlclause | rhclause | gccclause | msclause | gdclause | fcclause | feclause | kwnclause | kviclause | excclause | tcpclause | tcpofclause | osclause | ceqmclause | pszclause | pgcclause | acronlyclause | trgonlyclause | dothisonlyclause | swptclause | geclause | kvmclause | rollclause | smaclause | smbclause | xtnclause | pvtclause | pbaoclause | dcclause | pmcclause | mcfclause | ecclause | lureclause | youctrlclause | endureclause | exdmgclause

// LITERAL keyword-action effects: §720 monarch/initiative + §701 clash — fixed whole-clause phrases the
// regex templates (_clash/_monarch/_initiative) grounded to a nullary Effect(verb, '-', 'you'). One
// high-priority phrase terminal owns each; the whole clause must BE the phrase, so it's byte-identical
// to the anchored `^…$` regex, or no parse.
litclause: LITEFFECT                                          -> lit
LITEFFECT.5: /clash with an opponent|you become the monarch|you take the initiative/
// GET ENERGY (§107.16) — '[you] get {E}{E}…' (the `_get_energy` template). A whole-phrase GETENERGY terminal
// (requires the trailing {e} symbol(s), so it can't steal 'you get an emblem'/'you get N poison counters');
// the transformer counts the {e} glyphs for the amount -> get_energy(<N>, you). (src is lowercased, so {e}.)
// The 'you' is OPTIONAL so a subject-ELIDED conjunct grounds — 'you gain 1 life and get {E}' splits into
// ['you gain 1 life', 'get {E}'] and the second (shared-subject) conjunct still parses; the actor is always you.
geclause: GETENERGY                                           -> get_energy_v
GETENERGY.5: /(?:you )?get (?:\{e\})+/
// MULTI-WORD nullary keyword actions (§701.x) — 'Manifest dread'/'Time travel'/'The Ring tempts you'/'Open an
// Attraction'/'Collect evidence'/'Venture into the dungeon'/… (the `_bare_action` catch-all, FLIP-ONLY since
// that catch-all serves many verbs). KVINTRANS is single-word; these are DISTINCTIVE multi-word phrases, so a
// whole-phrase KV_MULTI terminal can't steal them mid-clause. The transformer slugs the phrase to its verb and
// validates against keyword_actions() -> <verb>(-, you). (The clause must BE the phrase, like `_bare_action`.)
kvmclause: KV_MULTI kvmnum?                                   -> kvmulti
kvmnum: NUM | QUANT                                            // optional trailing count: 'collect evidence N' (the only NUMBERED multi-word §701 kw action; the regex `_kwaction_n` grounds '<phrase> <N>')
KV_MULTI.5: /manifest dread|time travel|the ring tempts you|open an attraction|collect evidence|venture into the dungeon|roll to visit your attractions|set in motion|face a villainous choice/
// ROLL A DIE (§705) — 'roll a d6' / 'roll two d20s' / 'roll a six-sided die' (the `_roll`/`_roll_sided`
// templates). The ROLLDIE whole-phrase terminal REQUIRES the die-spec ('dN' or '<word>-sided die'), so it
// can't steal the KV_MULTI 'roll to visit your attractions' phrase (no die-spec there). The transformer
// re-applies the two templates' OWN regexes to the matched text -> roll_die(<N>, you, dN), byte-identical.
rollclause: ROLLDIE                                           -> roll_die_v
ROLLDIE.5: /\broll \w+ (?:d\d+s?|[\w]+-sided (?:die|dice))/
// SPEND-MANA-AS (§106.6 spend-as permission) — two `_mana_any_for` / `_spend_as` shapes:
//   'mana of any type can be spent to cast <X>'  -> spend_mana_as(-, you, any_color_for_<slug(X)>)
//   '[you] [may] spend mana as though it were mana of any color [to cast …]' -> spend_mana_as(-, you, any_color)
// Both anchored on distinctive whole-phrase terminals; the scope object span (smaobj) is slugged, and the
// SPEND_AS form's optional 'to cast …' tail is DROPPED exactly as the regex (it always yields 'any_color').
smaclause: MANA_ANY_FOR smaobj                                -> spend_mana_for
smbclause: SPEND_AS                                           -> spend_mana_as_v
smaobj: (WORD | QUANT | NUM)+
MANA_ANY_FOR.5: /mana of any (?:type|color) can be spent to (?:cast|play) /
SPEND_AS.5: /(?:you )?(?:may )?spend mana as though it were mana of any (?:color|type)(?: to cast .+)?/
// EXTRA TURN (§500.7) — '[<player>] take[s] [an|N] extra turn(s) after this one' (the `_extra_turn` template).
// The 'extra turn(s) after this one' tail is unique to this effect, so a whole-phrase EXTRATURN terminal (head
// bounded to a player NP so it can't run away) can't steal any other clause. The transformer re-applies the
// template's EXACT pattern to the matched text -> extra_turn(<n|->, _target(subj|you)), byte-identical.
xtnclause: EXTRATURN                                          -> extra_turn_v
EXTRATURN.5: /(?:[\w'][\w' ]* )?takes? (?:an|one|two|three|\w+) extra turns? after this one/
// 'put them back in any order' (§401 scry-like reorder of looked-at cards back on top) — the dedicated
// `_put_back_any_order` template, a single fixed whole-clause phrase -> put_on_top(-, them, any_order). A
// high-prio whole-phrase PUTBACKAO terminal owns it; the clause must BE the phrase (start consumes all), so
// it's byte-identical to the `^…$` regex. The longer 'put them back … on top of your library in any order'
// forms carry extra words and ground via the existing put productions (no theft — verified by regression).
pbaoclause: PUTBACKAO                                         -> put_back_any_order
PUTBACKAO.5: /put them back in any order/
// 'The <keyword> cost is equal to its mana cost' — the cost spec accompanying a granted alt-cost keyword
// (flashback/scavenge/embalm/…, §702). PARSE-FAILs every other production; the distinctive CEQMANA tail
// terminal anchors it and the transformer re-matches src against `_granted_keyword_cost` (validates the kw).
// POSITIVE priority: the QUANT?-broadened bctclause otherwise parses 'the <kw> cost is equal to its mana cost'
// as a becomes copula ('the <kw> cost' IS 'equal …') and SHADOWS this production (one-tree/no-fallthrough -> lark
// abstains). A small negative-vs-negative gap (-1 over -2) did NOT flip earley's ambiguity choice; a positive
// priority does. The distinctive CEQMANA exact-tail terminal makes a high priority theft-safe (only 'the <X> cost
// is equal to its mana cost' clauses can match it).
ceqmclause.5: ceqmlead CEQMANA                               -> cost_eq_mana
ceqmlead: WORD+
CEQMANA.6: /\bcost is equal to its mana cost\b/
rclause: RVERB quant? robj fromphrase? zonephrase? trailer?   -> ret   // 'return': strip from/to
oclause: OVERB quant? objall trailer?            -> imperative  // object verbs: object spans everything
// SUBJECT-PREFIXED tap/untap (§701.20): '<player> taps/untaps <obj>' — the `_taputap` leading-subject form
// (subject DROPPED). The bare imperative `oclause` is verb-first, so these PARSE-FAIL; this production carries
// a leading subject SPAN (ossubj) and the transformer re-matches `_taputap` on src (its `(?:{_TGT} )?` drops +
// validates the subject). NEGATIVE priority; tap/untap only (other OVERBs have no subject-drop template -> abstain).
osclause.-2: ossubj OVERB quant? objall trailer?  -> tapuntap_subj
ossubj: (WORD | QUANT | NUM)+                     // leading actor NP before the object verb (validated via _taputap)
// SUBJECT-prefixed PUT-to-zone '<player> puts <obj> on top of/on the bottom of/into <zone>' (PARSE-FAILs the
// leading-PUT pzput; mirror of osclause). Flat body span re-matched against `_subject_puts`/`_owner_puts`.
pszclause.-2: pszsubj PUT pszbody                 -> subject_puts
pszsubj: (WORD | QUANT | NUM)+                     // leading player NP before 'puts' (validated via _subject_puts)
pszbody: (WORD | QUANT | NUM | CCOUNT | FROM | ZONE | ONPREP | TOPREP | XLIB | EQUALTO | MDUR | PTDELTA | FACE_DIR)+  // object + destination (src re-matched)
// '<player> gets <n> poison/energy/experience counter(s)' — the 'gets' verb (not 'put') PARSE-FAILs every
// production; this owns it. Flat body; the transformer re-matches `_gets_counter` on _src (faithful flip,
// abstains if the kind isn't a player counter). NEGATIVE priority so any competing parse wins.
pgcclause.-2: pgcsubj GETS pgccount pgckind COUNTER   -> player_gets_counter
pgcsubj: (WORD | QUANT | NUM)+                     // leading player NP before 'gets' (-> _target)
pgccount: QUANT | NUM | WORD                        // 'a'/'an'/'one'/'two'/'x'/digit/word (-> _amount)
pgckind: WORD                                       // the counter kind, validated poison|energy|experience in the xf
pclause: psubj? PVERB pbody                       -> pcount      // player-count verbs: NP is the AMOUNT
dclause: dsrc DEALS damamt DMG TOPREP dtarget     -> deal        // '<source> deals N damage to <target>'
mclause: mtgt GETS PTDELTA mdur?                  -> boost       // '<target> gets +N/+N [duration]'
mfeclause: mtgt GETS PTDELTA mdur FOREACH mferest -> boost_foreach  // '<t> gets +N/+N until eot for each <X>' (§107.3)

// deal_damage VARIANTS (the basic dclause abstains on these — they lack a single-token amount before
// 'damage', or carry an 'equal to <amount>' / 'that much' / 'divided' rider). The discriminator is
// purely lexical: 'damage equal to … to …' (amount-first) vs 'damage to … equal to …' (target-first)
// are disjoint by whether EQUALTO precedes or follows the 'to <target>' prep phrase.
deqclause: dsrc DEALS DMG EQUALTO deqamt TOPREP dteqtgt          -> deal_eq   // 'deals damage equal to <amt> to <tgt>'
dteqclause: dsrc DEALS DMG TOPREP dteqtgt EQUALTO dvamt          -> deal_teq  // 'deals damage to <tgt> equal to <amt>'
dtmclause: dsrc DEALS THATMUCH DMG TOPREP dtarget               -> deal_tm   // 'deals that much damage to <tgt>'
ddivclause: dsrc DEALS damamt DMG DIVIDED ddivtgt              -> deal_div  // 'deals N damage divided as you choose among <tgts>'

gclause: gtgt? GVERB gkw mdur?                    -> grant       // '<target> gains/has <KEYWORD> [duration]'
// KEYWORD-CHOICE grant (§700.2 choice): '<tgt> gains your choice of <kw>, <kw>, or <kw> [until eot]' — a
// grant of ONE chosen keyword from the listed §702 options. The YOURCHOICE terminal claims 'your choice of'
// (scoped to GVERB, so 'becomes/create your choice of' don't reach here); every option must ground as a §702
// keyword, else abstain. -> grant_keyword(-, tgt, 'choice_<kw>_or_<kw>…') — faithful (no option dropped).
gchclause.2: gtgt? GVERB YOURCHOICE gchrest mdur?  -> grant_choice
gchrest: (WORD | NUM | QUANT | TOPREP | FROM | ZONE)+
aclause: gtgt GVERB QUOTED mdur?                  -> grant_ab    // '<target> has/gains "<ability>" [duration]'

// CHOOSE family (§700.2). Bare imperative 'choose <quant> <thing>' only; the regex `_choose` DROPS
// any leading subject, so subject-prefixed forms are deferred to the regex (no leading-subject rule
// here — abstaining is safe and avoids the greedy subject-swallow ambiguity). The chosen thing spans
// to end as opaque tokens (rejoined to text and slugged, like the regex `(.+?)$`). 'choose' folds the
// quant exactly: {a,an,one} -> q="", else q=slug(quant)+"_".
chsclause: CHS_CHOOSE chsquant chsrest            -> chs
// CHOOSE NEW TARGETS (§707.10) — '[you may] choose new targets for <X>' (the copy-redirect rider). The
// 'choose_new_targets' verb is already §707.10-grounded; this just reads the object. -> choose_new_targets(-, X).
cntclause: CHOOSE_NEW_TGT cntobj                  -> choose_new_targets
cntobj: (WORD | QUANT | NUM | TOPREP | FROM | ZONE)+
// §602.5 ACTIVATION restriction tail: 'Activate [this ability] only <as a sorcery|once each turn|during
// your turn|…>'. A whole-clause timing/frequency restriction on an activated ability (often the last line
// of the ability's text). ACT_ONLY (the distinctive 'activate [this ability] only' bigram, high-prio so it
// outranks the bare 'activate' keyword-action WORD) anchors it; the rest is the restriction span, slugged
// to extra. -> activate_only(-, -, <restriction>). The keyword-action 'activate target X' lacks the 'only'
// and never matches, so this can't steal it.
acronlyclause: ACT_ONLY acronlyrest              -> activate_only
acronlyrest: (WORD | QUANT | NUM | TOPREP | FROM | ZONE)+
ACT_ONLY.6: /\bactivate (?:this ability )?only\b/
// sibling §603.3 / §603.3e frequency restrictions, same restriction-tail shape (reusing acronlyrest):
// 'This ability triggers only <once each turn|…>' -> triggers_only; 'Do this only <…>' (the prior effect's
// frequency cap) -> do_this_only. Each anchor is a distinctive contiguous phrase (so it can't steal a bare
// 'triggers'/'do this'); the restriction rides the slug.
trgonlyclause: TRG_ONLY acronlyrest              -> triggers_only
dothisonlyclause: DOTHIS_ONLY acronlyrest        -> do_this_only
TRG_ONLY.6: /\bthis ability triggers only\b/
DOTHIS_ONLY.6: /\bdo this only\b/
// FREE CAST (§601/§118.5) — '[you may] play/cast <X> [this turn] without paying its mana cost' (impulse-draw
// / free-cast). The trailing WITHOUT_PAY phrase anchors it (so the play/cast verb stays a plain WORD elsewhere,
// no collision); the modifier was DROPPED/garbled by the regex leaf. -> play|cast(-, <X>, without_paying_mana_cost).
fcastclause.2: fcastverb fcastobj WITHOUT_PAY              -> free_cast
fcastverb: WORD
fcastobj: (WORD | QUANT | NUM | ZONE)+
// IMPULSE PLAY DURATION — '[you may] play/cast <X> for as long as <cond>' (impulse-exile). The duration was
// GARBLED into the target by the regex leaf; FORASLONGAS anchors it -> play|cast(-, <X>, -, for_as_long_as_<cond>).
// NEGATIVE priority (lowered from +2): the FORASLONGAS tail is now also reachable by nstail (the §502
// "<perm> doesn't untap during … for as long as …" no-untap static), and `fcastverb` is an unconstrained
// WORD, so a positive priority let play_dur STEAL that untap clause (then abstain, since fcastverb isn't
// play/cast). At -3 the play_dur transformer's own play/cast guard still rejects non-play/cast, and the
// nsuntap rule (-2) wins the untap clause; on a real 'play/cast X for as long as …' nothing else matches, so
// play_dur still wins. (Same negative-defer rationale as pfromclause.)
pdurclause.-3: fcastverb fcastobj FORASLONGAS pdcond      -> play_dur
pdcond: (WORD | QUANT | NUM | ZONE)+
// CAST-AS-THOUGH-FLASH (§117.1a) — '[you may] play/cast <X> as though it/they had flash' (impulse instant-
// speed). The whole 'as though … flash' phrase is the anchor (NOT bare 'as though', so attack/block
// 'as though' permissions are untouched). -> play|cast(-, <X>, as_though_flash).
pflashclause.2: fcastverb fcastobj ASTHOUGH_FLASH         -> play_flash
// CAST FROM A ZONE (§601) — '[you may] play/cast <X> from your graveyard/hand/...' (graveyard-recursion).
// Reuses the existing fromphrase (FROM zwords? ZONE); NEGATIVE priority so return/exile/etc. (which also use
// fromphrase) keep their parse — pfromclause only wins for play/cast, where nothing else matches. The xf
// abstains on any non-play/cast verb. -> play|cast(-, <X>, from_<zone>).
pfromclause.-3: fcastverb fcastobj fromphrase            -> play_from
// TURN FACE UP (§708.5) — '[you may] turn <X> face up' (morph/disguise/manifest/cloak reveal). The FACE_UP
// terminal is also added to the object spans (objall/pzbody) so existing 'face up' spans are unchanged.
// NEGATIVE priority so object verbs keep their parse — tfaceclause only wins for 'turn'; the xf abstains
// otherwise. (No 'turn face DOWN' counterpart: a FACE_DOWN terminal broke the conjure→hand parse.) Priority
// -1 (raised from -3) so it OUTRANKS osclause/tapuntap_subj (-2), which otherwise STEALS 'turn <multi-word
// object> face up' (e.g. 'turn the exiled card face up' parsed as tapuntap_subj and abstained); tfaceclause
// requires the distinctive trailing FACE_DIR + a 'turn' verb, so it can't grab any non-turn-face clause, and
// staying NEGATIVE still yields to every positive-priority family.
tfaceclause.-1: fcastverb fcastobj FACE_DIR                 -> turn_face
chsquant: QUANT                                   // reuse the shared QUANT terminal (no new quant terminal)
chsrest: chstok+                                  // the chosen-thing NP, opaque to end (rejoined + slugged)
chstok: WORD | NUM | QUANT | TOPREP | FROM | ZONE | EQUALTO | THATMANY | ONPREP | COUNTER
      | DEALS | DMG | GETS | GVERB | PVERB | PUT | TOKEN | DIVIDED | THATMUCH | PTDELTA | MDUR | CCOUNT
cclause: csubj? PUT ccount ckind COUNTER ONPREP ctarget   -> putctr  // 'put <N> <kind> counter(s) on <tgt>'
// COMPOUND counters (AST conjunction): 'put <c1> <k1> counter(s) and <c2> <k2> counter(s) on <tgt>' — the
// two counter NPs SHARE one PUT and one 'on <tgt>', so a flat split fails; the grammar composes them into
// two put_counter effects (xf returns a LIST, consumed by parse_clauses_lark). Common keyword-counter form.
cconjclause: csubj? PUT ccount ckind COUNTER "and" ccount ckind COUNTER ONPREP ctarget   -> putctr_conj
// DISTRIBUTE counters (§122) — 'distribute <N> <kind> counters among <targets>' (the `_distribute_counters`
// template: spread N counters over a target set, recorded put_counter(<N|X>, slug(targets), <kind>, distributed)).
// A leading DISTRIBUTE terminal anchors it; dcbody spans the rest (commas live inside WORD). The transformer
// re-applies the template's EXACT regex to self._src -> byte-identical. NEGATIVE priority so any competing parse
// wins (one earley tree, no fallthrough) — only a pure distribute-counters clause, with no other parse, lands here.
dcclause.-2: DISTRIBUTE dcbody                 -> distribute_v
dcbody: (WORD | QUANT | NUM | PTDELTA | COUNTER | TOPREP | FROM | ZONE | ONPREP | EQUALTO)+
// MOVE/relocate EXISTING counters (§122) — two shapes recorded as a put_counter with the relocation in cond:
//   'put <its|all|all of its> counters on <tgt>'            (`_move_counters`)     -> put_counter(slug(q), tgt, moved)
//   'move <N> <kind> counters from <src> onto|to <tgt>'     (`_move_counter_from`) -> put_counter(<n|X>, tgt, kind, moved_from_<src>)
// pmcclause reuses PUT but is NEGATIVE priority so cclause (which REQUIRES a ckind token) wins whenever it
// applies — only a kind-less 'put its/all counters on …' lands here. mcfclause is anchored by a distinctive
// MOVE terminal ('move' is otherwise unused in lark). Each transformer re-applies its template's EXACT regex to src.
pmcclause.-2: PUT pmccount COUNTER ONPREP pmctarget   -> move_counters_v
pmccount: (WORD | QUANT)+
pmctarget: (WORD | QUANT | NUM | ZONE | PTDELTA | THATMANY)+
mcfclause.-2: MOVE mcfbody                            -> move_counter_from_v
mcfbody: (WORD | QUANT | NUM | PTDELTA | COUNTER | FROM | TOPREP | ZONE | ONPREP)+
// §509 COMBAT REQUIREMENTS — three clean families grounded by dedicated templates:
//   '[after this [main] phase,] there is an additional combat phase [followed by …]' (`_extra_combat`) — a
//      CONSTANT tuple extra_combat(-, you); the whole-phrase ECOMBAT terminal IS the regex, so just emit it.
//   'all creatures able to block <X> [this turn|this combat] do so' (`_lure`)      -> lure(-, X)
// lure carries an object SPAN and re-applies its template's EXACT regex to self._src (object outside `_TGT`
// abstains -> regex leaf). NEGATIVE priority. ('<X> must be blocked … if able' is NOT a new production — it
// already parses as mrclause/mustreq via the shared MRABLE 'if able' anchor; that transformer is extended below.)
ecclause: ECOMBAT                                     -> extra_combat_v
lureclause.-2: LURELEAD lurebody DOSO                 -> lure_v
lurebody: (WORD | QUANT | NUM | MDUR)+
// §720 STATIC 'you control <X>' (no 'gain') — the bare-control aura form ('you control enchanted creature/
// permanent/…') that lark's grant gc-branch (which only fires via GVERB gains/has/have) misses. Anchored on a
// clause-INITIAL YOUCTRL terminal so it can only match a whole 'you control …' clause — a mid-clause 'creatures
// you control' can't satisfy it (the clause doesn't START with 'you control'). NEGATIVE priority so any other
// production wins, and the transformer re-applies `_control`'s EXACT regex to src (non-matching clause abstains).
youctrlclause.-3: YOUCTRL ycbody                      -> you_control_v
ycbody: (WORD | QUANT | NUM | ZONE | TOPREP | FROM | MDUR)+
tclause: ccreator? CVERB (CCOUNT | THATMANY) cspec TOKEN cforeach? ctail?  -> create  // 'create N <spec> token[s] [for each X]'; THATMANY = anaphoric 'create that many <spec> tokens'
// COMPOUND tokens (AST conjunction): 'create <c1> <spec1> token(s) and <c2> <spec2> token(s)' — the two
// token NPs share ONE 'create', so a flat split strands the verb-less 2nd half and `create` would DROP it
// (its ctail swallows 'and a Treasure token' — a silent lossy conjunct). The grammar composes them into two
// create effects (xf -> LIST). Priority .2 so it WINS the ctail-swallow ambiguity for the compound form.
tconjclause.2: ccreator? CVERB CCOUNT cspec TOKEN "and" CCOUNT cspec TOKEN  -> create_conj
// CREATE a COPY token (§707) — '[<creator>] create[s] [N] token[s] that's a copy of <X>[, except <mods>]'.
// Spec-LESS, so the normal `tclause` (which needs a cspec before TOKEN) PARSE-FAILs; the distinctive COPYTOK
// terminal anchors it, cpobj consumes the copied object + optional ', except <mods>' (WORD eats the comma),
// and the transformer re-matches src against `_create_copy` (`_CCP_RE`) for a byte-identical tuple.
tcpclause.-2: ccreator? CVERB CCOUNT COPYTOK cpobj  -> create_copy
cpobj: (WORD | QUANT | NUM | TOPREP | FROM | ZONE | PTDELTA | EQUALTO | MDUR)+   // copied object + 'except …' span (src-re-matched; raw)
// CREATE a COPY token, ELIDED 'token that's' form — 'create a copy of <X>[, except <mods>]' (the
// `_create_copy_of` template). Anchored on the EXISTING COPYOF ('a copy of') terminal (no new terminal ->
// no lexer collision; bcpclause needs 'becomes' before COPYOF so it never claims a create clause). The
// count 'a' is inside COPYOF, so no CCOUNT; the transformer re-matches src against `_create_copy_of`.
tcpofclause.-2: CVERB COPYOF cpobj  -> create_copy_of

// BECOMES (the dominant 'animate to a N/N' shape): '<tgt> becomes/is/are [a] N/N <typetail>
// [with <kw>] [until end of turn]'. We own ONLY this P/T-bearing shape (the `_becomes` template);
// copy/color/base-pt/added/type variants stay with the regex (faithful-or-abstain). The whole
// post-P/T span is captured raw and the regex's non-greedy g3 (type tail) is reconstructed exactly
// in the transformer (`_bcm_g3`) — the 'with <kw>' and a trailing 'until end of turn' are stripped.
bcmclause: bcmtgt BCM_COP quant? BCM_PT bcmtail?   -> bcmbecomes

// BECOMES <color> (§105/§613) — '<subj> becomes/is <basic-color> [in addition to its other colors] [until
// end of turn]' (the `_becomes_color` template, the literal-color slice). The COLOR terminal GREEDILY
// includes the two optional riders, so a compound ('becomes white and gains flying') does NOT parse as
// this production (COLOR stops at 'white', the clause has leftover -> no parse) — that avoids the
// body-splitter coupling. The transformer re-matches src against `_becomes_color`'s exact pattern (subj
// via _target, color via ground.slug) for byte-identity; 'the color of your choice' is NOT in COLOR, so it
// defers to `_becomes_choice`. NEGATIVE priority.
bccclause.-2: bcmtgt BCM_COP COLOR                 -> bccolor_v

// BECOMES a copy of <X> (§707) — '<subj> becomes a copy of <X>[, except <mods>] [until end of turn]' (the
// `_becomes_copy` template). Distinctive COPYOF ('a copy of') anchor; the rest (copied object + optional
// 'except' overrides + 'until end of turn') is consumed by bcprest, then the transformer re-matches src
// against the template's exact pattern -> becomes(-, _target(subj), 'copy_of_'+_target(obj)[+'_except_'+
// slug(mods)]). An `_is_compound_object(src)` guard defers a run-on ('… and <verb> …') to the regex chain.
bcpclause.-2: bcmtgt BCM_COP COPYOF bcprest        -> bccopy_v
bcprest: (WORD | QUANT | NUM | TOPREP | FROM | ZONE | MDUR | BOUND | PTDELTA | EQUALTO | DEALS | DMG | GETS)+

// BECOMES the <X> of your choice (§700.2) — '<subj> becomes the <X> of your choice [until end of turn]'
// (the `_becomes_choice` template, 'becomes' only). Distinctive trailing OFCHOICE ('of your choice') anchor;
// the transformer re-matches src against the template's exact pattern -> becomes(-, _target(subj),
// 'chosen_'+slug(X)). An `_is_compound_object(src)` guard defers run-ons to the regex.
bchclause.-2: bcmtgt BCM_COP bchmid OFCHOICE bchtail?  -> bcchoice_v
bchmid: (WORD | QUANT | NUM | ZONE)+                  // 'the <X>' between the copula and 'of your choice'
bchtail: MDUR                                          // optional 'until end of turn' (dropped)

// BECOMES a/an <card-type> (§205) — '<subj> is/are/becomes a/an <…card-type…> [in addition to its other
// types] [until end of turn | for as long as <cond>]' (the `_becomes_type` template). 'a/an'-anchored; the
// transformer re-matches src against the template's exact pattern (the CLOSED card-type word list: only a
// real type word grounds, so 'is a black Zombie' / 'is a Forest in addition to its other LAND types' defer
// to `_becomes_color_type`/the added_ templates). becomes(-, _target(subj), slug(type), <for_as_long_as|->).
// An `_is_compound_object(src)` guard defers run-ons. NOTE: the QUANT (a/an article) is REQUIRED. An earlier
// commit (9e22c5e) made it optional (QUANT?) to reach the ARTICLE-LESS forms ('<subj> are <Type>s in addition
// to their other types' / '<subj> is every <kind> type'), but QUANT? let bctclause match ANY '<NP> is/are
// <non-article>' clause — shadowing ~32 productions (relative-clause modify_pt anthems, conditional deal_damage,
// becomes_day/night, ceqmclause) via one-tree/no-fallthrough, a NET -9 lark coverage loss. Reverted to QUANT;
// the article-less becomes forms fall back to the regex leaf (parse_clause output unchanged) pending a clean
// dedicated-anchor re-implementation (EVERYTYPE / article-less-INADD productions) that won't shadow.
bctclause.-2: bcmtgt BCM_COP QUANT ctrest             -> bctype_v
ctrest: (WORD | QUANT | NUM | TOPREP | FROM | ZONE | MDUR | BOUND | PTDELTA | EQUALTO | DEALS | DMG | GETS | ONPREP | COUNTER)+
// ARTICLE-LESS ALL-TYPES (§205) — '<subj> is/are/becomes every <kind> type [in addition to other types]' (the
// `_all_types` template). The CORRECT way to reach this article-less form (vs the reverted QUANT? blunt-instrument):
// a DEDICATED production anchored on a DISTINCTIVE EVERYTYPE terminal ('every <creature|basic land|…> type'),
// which appears ONLY in all-types becomes clauses — so it can't shadow unrelated productions. The optional 'in
// addition …'/duration tail rides alltail (plain WORDs, NOT a shared terminal). Transformer re-applies `_all_types`'
// EXACT regex (_ALLT_RE) to src -> becomes(-, _target(subj), 'every_'+slug(kind)+'_type').
alltclause.-2: bcmtgt BCM_COP EVERYTYPE alltail?      -> alltypes_v
alltail: (WORD | QUANT | NUM | MDUR)+

// BASE POWER AND TOUGHNESS (§208/§613.3) — the base-P/T-set family, anchored on the highly distinctive
// BASEPT phrase 'base power and toughness'. The transformer re-matches src against the three templates in
// the regex chain's order: `_becomes_base_pt` ('becomes a <type> with base P/T' -> base_pt_<type>) ->
// `_base_pt_perpetual` ('perpetually has base P/T' -> base_pt, perpetual) -> `_base_pt` ('has/have/with
// base P/T' -> base_pt). `_is_compound_object(src)` guard defers run-ons (the `_base_pt_compound` cases).
bptclause.-2: bptpre BASEPT bptpost                   -> basept_v
bptpre: (WORD | QUANT | NUM | BCM_COP | TOPREP)+
bptpost: (BCM_PT | PTDELTA | WORD | QUANT | NUM | MDUR | BOUND | TOPREP | ZONE | EQUALTO)+

// BECOMES 'is/are also a/an <type>' (§205 type ADDITION) — the `_type_also` template. ALSO-anchored ('is
// also a Cleric, Rogue, Warrior, and Wizard'); re-match src -> becomes(-, _target(subj), 'added_'+slug(X)).
btaoclause.-2: bcmtgt BCM_COP ALSO QUANT btaorest     -> btalso_v
btaorest: (WORD | QUANT | NUM)+

// BECOMES 'the chosen type' (§205) — the `_becomes_chosen` template's TYPE branch ('the chosen color' is
// already owned by the color slice, so the CHOSENTYPE anchor is 'the chosen type' only). -> chosen_type.
bcchclause.-2: bcmtgt BCM_COP CHOSENTYPE bcchtail?    -> bcchosen_v
bcchtail: (WORD | QUANT | TOPREP | MDUR)+

// BECOMES a §701 STATUS DESIGNATION (§701.43 foretold / §701.55 plotted) — '<subj> becomes foretold/plotted'
// (the tail of a face-down-exile trigger). The whole 'becomes <desig>' bigram is one distinctive terminal
// (BECOMESDESIG, outranks BCM_COP), so it can't collide with the P/T / color / type becomes-productions.
// -> becomes(-, _target(subj), <desig>). Subject _TGT or abstain.
bdgclause.-2: bdgsubj BECOMESDESIG bdgtail?            -> bcdesig_v
bdgsubj: (WORD | QUANT | NUM)+
bdgtail: (WORD | QUANT | NUM | MDUR)+                  // optional trailing 'until end of turn' etc. (dropped, like _BCT)

// '<subj> is/are/become no longer suspected' (§701.60 suspect REMOVAL) — the inverse of 'suspect <tgt>'.
// The copula+predicate is one distinctive terminal (NOLONGERSUSP); subject reuses bdgsubj (_TGT or abstain).
// -> suspect(-, _target(subj), no_longer).
bnsclause.-2: bdgsubj NOLONGERSUSP                     -> bcnosusp

bcmtgt: (WORD | QUANT | NUM)+            // the permanent receiving the animate (stops at the copula)
bcmtail: (WORD | QUANT | NUM | PTDELTA | TOPREP | FROM | ZONE | GETS | DEALS | DMG | MDUR | TOKEN | BCM_PT | BCM_COP | EQUALTO | COUNTER | ONPREP)+  -> bcmtail  // raw post-P/T span

// REVEAL — the structured 'reveal the top N cards of <owner> library' (imperative or subject-form) and
// 'reveals their hand'. The clause body is captured as a flat token run and re-joined in the transformer,
// which applies the same fixed-frame regex the templates use (faithful by construction) and slugs the
// owner/subject via card_effects._target. A subject before the verb is allowed (closed _PLAYER phrase).
// rvclause/pvclause carry NEGATIVE rule priority so that when a sentence is ALSO parseable as another
// family (e.g. 'Whenever you reveal …, <src> deals N damage to <tgt>' — really a deal_damage clause
// whose 'reveal' sits in a leading wrapper that the regex leaf swallows into dsrc), Earley's ambiguity
// resolver prefers the competing (deal/…) parse, leaving the reveal/prevent grounding to the cases
// where it is the ONLY parse. On a pure 'reveal the top …' / 'prevent the next …' clause there is no
// competitor, so these rules still win. (The transformer additionally abstains on any non-frame body.)
rvclause.-2: rvsubj? RVREVEAL rvbody       -> reveal
rvsubj: (WORD | QUANT | NUM | ZONE)+        // player phrase before 'reveals' (validated as _PLAYER)
rvbody: (WORD | QUANT | NUM | ZONE | TOPREP | FROM | EQUALTO | THATMANY | FACE_DIR)+

// PREVENT_DAMAGE — the two clean dominant frames: 'prevent the next N damage that would be dealt
// [this turn] to <target> [this turn]' (_prevent) and 'prevent all [combat] damage that would be dealt
// this turn' (_fog). This is a TRUE grammar production: the body is carved at the structural DMG
// ('damage') terminal — which BOTH frames require exactly once — into a leading span (`pvpre`: 'all
// [combat]' or 'the next <count>') and a trailing span (`pvtail`: 'that would be dealt [this turn] [to
// <target>] [this turn]'). The transformer reads the two spans off the tree and certifies each with an
// anchored per-operand validator (`_PV_PRE_*` / `_PV_TAIL_*`) that captures the operands, exactly the
// `_fog`/`_prevent` skeletons — byte-identical, or abstain (the `_prevent_all_scoped`/`_prevent_that`/
// shield clauses lack this skeleton -> the regex fallback owns them). No whole-clause frame regex.
pvclause.-2: PVPREVENT pvpre DMG pvtail      -> prevent
pvpre:  (WORD | QUANT | NUM)+                                                // 'all [combat]' / 'the next <count>'
pvtail: (WORD | QUANT | NUM | ZONE | TOPREP | FROM | EQUALTO | THATMANY | MDUR)+  // 'that would be dealt [this turn] [to <tgt>] [this turn]'
// PREVENT consequent, NO tail (§615) — 'prevent that damage' / 'prevent the next N damage' / 'prevent N of
// that damage' (the `_prevent_that` template; the consequent of an 'if damage would be dealt …' wrapper).
// These lack the 'that would be dealt …' tail that pvclause requires, so they're a DISJOINT production:
// PVPREVENT + a body span + the structural DMG as the FINAL token (the start rule consumes the whole clause,
// so nothing follows 'damage' — that's what separates this from pvclause). The transformer re-applies
// `_prevent_that`'s EXACT regex to self._src -> prevent_damage(<n|that>, -), byte-identical.
pvtclause.-2: PVPREVENT pvtbody DMG          -> prevent_that
pvtbody: (WORD | QUANT | NUM)+                                               // 'that' / 'the next <n>' / '<n> of that'

// PHASE_OUT / PHASE_IN (§702.26/§502.15) — '<permanent> phases out/in [until …]' (the `_phase` template
// `^(_TGT) phases? (out|in)(?: until …)?$`). A TRUE grammar production: the distinctive PHASE terminal
// ('phase[s] out/in') splits the clause into a leading subject SPAN (validated as the anchored `_TGT`,
// `_AT_TGT`) and an optional trailing 'until …' rider (reuse `trailer`, DROPPED exactly as the regex's
// `(?: until …)?` does). The transformer reads the subject + direction off the tree -> phase_<dir>(-,
// _target(subj)); a subject outside `_TGT` abstains to the regex. NEGATIVE priority so a competing parse
// wins; a pure '<X> phases out' has no competitor.
pfclause.-2: pfsubj PHASE trailer?           -> phaseout
pfsubj: (WORD | QUANT | NUM)+                                                // the permanent NP (validated by _AT_TGT)

// REDIRECT_DAMAGE (§614.9) — '<amount> damage that would be dealt to <A> [this turn] [by <src>] is dealt
// to <B> [instead]' (the `_redirect` next-N + `_redirect_all` all/all-combat templates). TRUE grammar: the
// structural DMG ('damage') + the distinctive RDIS ('is dealt to') terminals carve the clause into a
// leading amount SPAN (`rdpre`: 'the next <N>' / 'all [combat]'), a source-side SPAN (`rda`: 'that would
// be dealt to <A> …'), and a recipient SPAN (`rdb`: '<B> [instead]'). The transformer reads the three
// spans and certifies A/B with anchored `_TGT` operand regexes that REUSE the template's own `_TGT` (so
// the greedy A/rider split — _TGT may absorb a trailing 'this turn', the 'by <src>' rider is dropped — is
// byte-identical). NEGATIVE priority; a clause outside the skeleton abstains to the regex.
rdclause.-2: rdpre DMG rda RDIS rdb          -> redirect
rdpre: (WORD | QUANT | NUM)+                                                 // 'the next <N>' | 'all [combat]'
rda: (WORD | QUANT | NUM | TOPREP | FROM | MDUR)+                            // 'that would be dealt to <A> [this turn] [by <src>]'
rdb: (WORD | QUANT | NUM)+                                                   // '<B> [instead]'
// EXCESS damage redirect (§120.4 'excess damage' / trample-to-controller) — 'Excess damage is dealt to <B>
// [instead]', the overflow beyond a creature's lethal damage spilling to <B>. NO source-side <A> (it's the
// prior sentence's damaged target), so this is a DISJOINT production from rdclause; the EXCESS terminal +
// the shared RDIS split carve it. -> redirect_damage(excess, _target(B)).
exdmgclause.-2: EXCESS RDIS rdb              -> excess_redirect    // EXCESS = 'excess damage' bigram

// SKIP (§500.7+) — '[<player>] skip[s] (your|its|their|his or her) [next] <phase/step|turn>' (the `_skip`
// template). The distinctive SKIP terminal splits an optional leading subject SPAN (a player, validated
// `_TGT` or defaulted to 'you') from the body; the transformer extracts the phase with the template's own
// possessive+next+phase regex (operating on the captured `skbody` span). skip(-, <player>, <phase_slug>).
// POSITIVE priority + PVERB in the body: a 'skip your DRAW step' clause is otherwise grabbed by `pcount`
// (psubj 'skip your', PVERB 'draw', pbody 'step') which abstains; the distinctive leading SKIP makes the
// positive priority safe (only genuine skip clauses match this production).
skclause.2: sksubj? SKIP skbody              -> skipverb
sksubj: (WORD | QUANT | NUM)+                                                // optional acting player (validated _TGT)
skbody: (WORD | QUANT | NUM | PVERB)+                                        // '(your|its|their|his or her) [next] <phase>' (PVERB covers 'draw')

// AMASS (§701.43) — 'amass <army-type> <N>' (the `_amass` template). Verb-first; the distinctive AMASS
// terminal leads, then the army-type word(s) SPAN and a trailing count token -> amass(<n|1>, you,
// slug(<type>)). The count is restricted to the template's own `(\d+|one|two|three|x)` set (else abstain).
asclause.-2: AMASS askind asnum              -> amassverb
askind: WORD+                                                                // the army type ('Zombies'/'Orcs'/…)
asnum: NUM | QUANT                                                           // the count ('2'/'one'/'x'); validated

// COPY (§707) — 'copy <object>' (the generic object-verb leaf `_verb_target`/`_generic_object_verb`,
// NOT a dedicated template — so this FLIPS copy onto lark but the shared catch-all stays). The EXACT
// mirror of `dbclause`/`dbl`: the distinctive CP_COPY terminal owns the leading verb; the transformer
// slices the object from the source after 'copy ' (byte-identical slug, never a re-joined approximation),
// applies the leaf's `_DB_OBJ_BAD`/`_is_compound_object` guards, and reproduces the _TGT-keeps-article /
// else-slug grounding. Compound/run-on/structural-marker objects abstain to the regex.
cpclause.-2: CP_COPY cpbody                  -> copyverb
cpbody: (WORD | QUANT | NUM | PTDELTA | TOPREP | FROM | ZONE | COUNTER | ONPREP | DMG | GETS | EQUALTO | THATMANY | MDUR | DEALS)+  -> cpbody

// MUST_ATTACK / MUST_BLOCK (§508/§509) — '<subj> attacks/blocks [<obj>] [<dur>] if able' (the
// `_must_attack` / `_must_block_tgt` / `_must_block_able` templates). The ONLY terminal is the distinctive
// trailing 'if able' anchor (MRABLE) — NO common-word 'attacks'/'blocks' terminal (those collide
// corpus-wide). The transformer reads the body span before 'if able' and re-applies the templates' own
// regexes (reusing `_TGT`) to split subject / directed-object / duration, so the grounding is byte-
// identical; a lossy/compound subject (which the regex grabs LOSSILY) finds no clean `_TGT` split here and
// abstains -> the regex keeps it (faithful 'abstain over lossy'). NEGATIVE priority.
mrclause.-2: mrbody MRABLE                    -> mustreq
mrbody: (WORD | QUANT | NUM | MDUR | TOPREP | FROM)+                         // '<subj> attacks/blocks [<obj>] [<dur>]'

// EXILE-TOP-OF-LIBRARY (§701.x exile) — 'exile the top [<N>] card[s] of <owner> librar(y|ies)' (the
// `_exile_top` template `^exile the top (?:(\w+) )?cards? of ([\w' ]+?) librar(?:y|ies)$`). The generic
// imperative leaf ALREADY abstains here (its `_TOPLIB` guard), so this production OWNS the shape. Verb-
// first like `copyverb`: the distinctive leading EXILE + a trailing XLIB ('library'/'libraries') anchor
// carve the clause; the transformer slices the body from `self._src` and RE-APPLIES the template's own
// regex (byte-identical N + `top_of_…_library` owner slug), never a re-joined approximation. NEGATIVE
// priority (the SF_VERB/oclause exile parses compete; on this shape they abstain so this wins clean).
xtclause.-2: EXILE xtbody XLIB               -> exiletop
xtbody: (WORD | QUANT | NUM)+                                                // 'the top [<N>] card[s] of <owner>'

// EXILE-UNTIL-LEAVES (§603.6e / §701.x) — 'exile <object> until ~ leaves the battlefield' (the
// `_exile_until` template `^exile (_TGT) until ~ leaves the battlefield$`). The generic imperative leaf
// ALREADY abstains here (its `trailer` eats the 'until …' rider and yields no object), so this owns it.
// Verb-first: leading EXILE + the distinctive trailing XLEAVES ('leaves the battlefield') anchor; the
// transformer reads the object SPAN between them and CERTIFIES it with the anchored `_TGT` (`_XL_TGT`,
// reusing the shared `_TGT`) exactly as the template's `(_TGT)` group, abstaining (over a lossy fact)
// on a compound/run-on object. exile(-, _target(obj), 'until_self_leaves'). NEGATIVE priority.
xlclause.-2: EXILE xlobj XLEAVES             -> exileuntil
xlobj: (WORD | QUANT | NUM | TOPREP | FROM | ZONE)+                          // the object NP (validated _TGT)

// MONSTROSITY (§701.x) — 'Monstrosity <N>' (the shared `_kwaction_n` keyword-action-with-number leaf,
// for this verb): a distinctive MONSTROSITY terminal + a count token -> monstrosity(<n>, you). FLIP-ONLY
// (the `_kwaction_n` catch-all stays for the other keyword actions). The count is restricted to the
// template's own (\d+|one..five|x) set, abstaining otherwise.
msclause.-2: MONSTROSITY msnum               -> monstrosity_v
msnum: NUM | QUANT                                                           // the count ('3'/'two'/'x'); validated

// GOAD (passive, §701.38) — '<creature> is goaded' (the dedicated `_goaded` template): the distinctive
// GOADED bigram ('is goaded') anchors a leading subject SPAN -> goad(-, _target(subj)). Subject _TGT or abstain.
gdclause.-2: gdsubj GOADED                   -> goaded_v
gdsubj: (WORD | QUANT | NUM)+                                                // the goaded creature (validated _TGT)

// FLIP_COIN (§701.x) — 'Flip a coin [until you lose a flip]' (the `_flip` template). The whole phrase is
// one distinctive FLIPCOIN terminal (the optional 'until you lose a flip' is part of it, so the clause
// must BE the phrase or no parse) -> flip_coin(<until_lose|->, you). POSITIVE priority: the 'until you
// LOSE a flip' tail otherwise lets `pcount` (PVERB 'lose') win the ambiguity and abstain; the distinctive
// whole-phrase FLIPCOIN terminal makes the positive priority safe (only a flip-a-coin clause matches).
fcclause.2: FLIPCOIN                          -> flipcoin

// FIGHT each other (§701.12 reciprocal) — '[then] <creatures> fight each other' (the `_fight_each`
// template). The distinctive trailing FIGHTEACH bigram anchors a leading subject SPAN (validated _TGT,
// a leading 'then' stripped) -> fight(-, _target(subj), 'each_other'). Distinct from FG_FIGHTS ('fights').
feclause.-2: fesubj FIGHTEACH                -> fighteach
fesubj: (WORD | QUANT | NUM)+                                                // the fighting creatures (validated _TGT, 'then' stripped)

// NUMBERED KEYWORD ACTIONS (§701.x) — '<bolster|adapt|incubate|support> <N>' (the shared `_kwaction_n`
// leaf, for these distinctive verbs): a KWACTION_N terminal + count -> <verb>(<n>, you). FLIP-ONLY (the
// `_kwaction_n` catch-all stays). Count restricted to the template's (\d+|one..five|x) set.
kwnclause.-2: KWACTION_N kwnnum               -> kwaction_n
kwnnum: NUM | QUANT                                                          // the count ('2'/'x'); validated
// ENDURE (§701, Bloomburrow/Duskmourn 2024) — '<creature> endures N' (the `_endure` template). Unlike the
// verb-first KWACTION_N (you-actor), endure is a CREATURE action: the SUBJECT is the actor/target ('it endures
// 2' -> endure(2, it)), so it has its own subject-prefixed production. The distinctive ENDURE terminal ('endure[s]')
// + a leading subject SPAN + a count; the transformer re-applies `_endure`'s EXACT regex to src -> byte-identical.
endureclause.-2: enduresubj ENDURE endurenum  -> endure_v
enduresubj: (WORD | QUANT | NUM)+                                            // the enduring creature (validated _TGT via re-match)
endurenum: WORD | NUM | QUANT                                                // the count (_amount; '\w+' in the template)

// INTRANSITIVE KEYWORD ACTIONS (§701.x) — '[<subject>] investigate[s]/explore[s]/proliferate[s]' (the
// shared `_bare_action` bare-form + `_subject_action` subject-form leaves, for these distinctive verbs).
// KVINTRANS terminal + optional leading subject SPAN -> <verb>(-, _target(subject|you)); the 3rd-person
// 's' is stripped to the keyword-action base exactly as `_subject_action` does. FLIP-ONLY (catch-alls stay).
kviclause.-2: kvisubj? KVINTRANS KVIMULT?     -> kvintrans
kvisubj: (WORD | QUANT | NUM)+                                               // optional actor (validated _TGT)
// optional repeat multiplier on an intransitive keyword action — 'investigate twice', 'connive three times'
// -> the count lands in the amount slot (parallels 'scry N'/'amass N'). The negative lookahead keeps it off
// 'twice that many' (an anaphoric AMOUNT, owned by _that_amt), so this can't steal that token.
KVIMULT.5: /\b(?:once|twice|thrice|that many times|(?:three|four|five|six|seven|eight|nine|ten|[0-9]+) times)\b(?! that)/

// EXCHANGE (§701.10) — 'exchange <object>' (the generic object-verb leaf; exchange IS in `_OBJ_VERBS`, so
// like `dbl`/double it grounds via the slug leaf). The EXACT mirror of `dbl`: slice the object from src
// after 'exchange ' (byte-identical slug), apply the leaf's `_DB_OBJ_BAD`/`_is_compound_object` guards, and
// reproduce the _TGT-keeps-article / else-slug grounding. FLIP-ONLY (shared catch-all stays).
excclause.-2: EXCHANGE excbody               -> exchangeverb
excbody: (WORD | QUANT | NUM | PTDELTA | TOPREP | FROM | ZONE | COUNTER | ONPREP | DMG | GETS | EQUALTO | THATMANY | MDUR | DEALS)+  -> excbody

// RETURN_TO_HAND (§614) — the dominant 'Return <object> [from <zone>] to <owner>'s hand' bounce that the
// existing `rclause`/`ret` MISSES (its `zonephrase: TOPREP zwords? ZONE` can't carve the possessive
// 'to its owner's hand' / 'their owners' hands' destination, so the object span greedily swallows it ->
// abstain). We mirror the EXACT `_bounce` (`^return (_TGT) to (its owner's hand|your hand|their owners'
// hands?|its owner's hands?)$`) and `_regrowth` (`^return (_TGT) from your graveyard to your hand$`)
// templates. The distinctive trailing RETHAND terminal IS the structural marker — it anchors the
// owner-hand destination at clause end; the transformer slices the object span out of `self._src` between
// the leading 'return[s] ' verb and RETHAND (byte-identical slug, never a re-joined approximation) and
// splits a trailing 'from <zone>' SOURCE off the object into extra (the faithful-replacement convention,
// object stops at 'from' — identical to `_bounce`). A leading PLAYER subject ('<player> returns <obj> …',
// the `_return_zone` subject-first shape) is allowed and dropped exactly as that template drops it. The
// object is gated ONLY by `_is_compound_object` (NOT `_TGT`): `_bounce` needs a `_TGT`, but `_return_zone`
// — which runs after `_bounce` and yields the SAME tuple — accepts any non-compound object via its
// `(.+?)`, so a §107.3 X-count / 'up to N' / 'all' / 'those' / 'it' / 'a card' / a card-name object all
// ground faithfully. `_is_compound_object` abstains a run-on EFFECT compound (' and '/' then ' + predicate),
// but a NOUN conjunction ('A and B' both nouns) is NOT compound and DOES ground. Comma lists / multi-'to'
// clauses are deferred up front by `_ret_ambiguous`. POSITIVE rule priority (2): the generic `rclause`/`ret`
// ALSO parses these
// (its `robj` greedily swallows the owner-hand destination, then returns None on the singular forms or
// grounds via `zonephrase` on 'to its owner's hand') — so `rhclause` must WIN the Earley forest to ground
// the plural 'their owners' hands' that `rclause` misses. On the singular overlap ('to its owner's hand'/
// 'to your hand') `rhclause` reproduces `rclause`'s `_bounce` tuple byte-identically, so winning is inert.
rhclause.2: rhbody RETHAND rhtail?            -> rethand
rhbody: (RVERB | WORD | QUANT | NUM | ZONE | FROM | TOPREP | EQUALTO | FACE_DIR)+   // 'return[s] [<subject>] <object> [from <zone>]' (RVERB: the leading 'return' string terminal; value unused; sliced from src)
rhtail: (WORD | NUM | QUANT | TOPREP | ZONE | FROM)+   // trailing '[at the beginning of] <step>' delayed-return timing — DROPPED (the regex `_return_zone` drops it for return_to_hand)

// GRANT_COMBAT (§509/§508 combat permission) — the clean '<subj> can attack/block …' subfamily of
// grant_ability (the `_as_though_combat` as-though-permission + `_can_block_more` multi-block templates).
// The distinctive GCC_CAN bigram ('can attack'/'can block') anchors it (bare 'can' collides corpus-wide,
// the bigram doesn't); an optional leading subject SPAN and a trailing permission SPAN flank it. The
// transformer slices the WHOLE clause from `self._src` and re-applies the two templates' OWN regexes
// (_GCC_ASTHOUGH / _GCC_BLOCKMORE, reusing `_TGT`) so the slug — and exactly which trailing duration is
// dropped (the as-though span keeps a mid-phrase 'this turn'; the block-more count drops a trailing
// 'this turn'/'each combat') — is byte-identical. A quoted-ability grant, an ' and '-joined compound, a
// 'can't …' restriction, or a non-`_TGT` subject matches NEITHER regex -> abstain to the regex (the
// `_grant_ability` quoted template / compound splitters own those). POSITIVE priority so the GCC_CAN-
// anchored parse WINS the `gclause`/`grant` competitor (a mid-phrase 'have' copula — 'as though it didn't
// HAVE defender' — would otherwise let `grant` claim the clause as a keyword-grant and abstain to None);
// this is safe because the transformer is faithful-or-abstain (it only grounds on an exact template match
// and otherwise returns None, after which `parse_clause` falls back to the regex anyway).
gccclause.2: gccsubj? GCC_CAN gcctail         -> grantcombat
gccsubj: (WORD | QUANT | NUM)+                                               // optional subject NP (validated `_TGT` via the re-applied template)
gcctail: (WORD | QUANT | NUM | MDUR | ZONE | FROM | TOPREP)+                 // the permission phrase after 'can attack/block' (re-sliced from `_src`)

// SUBJECT-FIRST object verbs (§701.17 sacrifice; §701.x exile) with an explicit PLAYER subject:
//   '<player> sacrifices it/that creature/them'        (_sacrifice_subj #1: by_<player> in extra, obj in target)
//   '<player> sacrifices <quant> <object>'             (_sacrifice_subj #2: <player> in target, obj slug in extra)
//   '<player> exiles <object>'                         (_subject_obj_verb: by_<player> in extra, obj in target)
// The subject is captured as a flat token run and HARD-gated against the closed `_PLAYER` allow-list in
// the transformer; the object span is sliced from the raw source by the verb token's end position (so the
// slug is byte-identical to the regex, never a re-joined approximation). NEGATIVE rule priority so that a
// sentence ALSO parseable as another family (the subject run could otherwise compete) yields to that
// parse; on a pure '<player> sacrifices/exiles …' there is no competitor and this rule still wins.
sfclause.-2: sfsubj SF_VERB sfrest          -> subjverb
sfsubj: (WORD | QUANT | NUM)+                // the acting player (validated as _PLAYER)
sfrest: (WORD | QUANT | NUM | ZONE | TOPREP | FROM | EQUALTO | THATMANY | MDUR | DMG | PTDELTA | COUNTER | ONPREP | FACE_DIR)+

// REMOVE_COUNTER — the mirror of put_counter (cclause/putctr): 'remove <count> [<kind>] counter[s]
// from <target>' (_remove_counter). This is a TRUE grammar production (like cclause): the COUNT, the
// optional KIND, and the TARGET are captured as distinct grammar SPANS, anchored by the structural
// COUNTER ('counter[s]') and FROM ('from') terminals — exactly the skeleton the `_remove_counter`
// template `^remove <count> [<kind>] counter[s] from <_TGT>$` required. The transformer reads the spans
// off the parse tree and reproduces the template's count/kind/target grounding (byte-identical). The
// target span is certified by the anchored `_TGT` operand validator `_RC_TGT` (mirrors `_DB_TGT`): a
// target outside `_TGT` abstains exactly as the template did. The 'remove … from combat' / non-counter
// 'remove' clauses lack the 'counter[s] from' skeleton, so they don't match this production at all ->
// they parse via the regex fallback (faithful-or-abstain). No structural whole-clause frame regex.
rcclause: RC_REMOVE rccount rckind? COUNTER FROM rctarget  -> rcremove
rccount: QUANT | NUM | WORD                  // the count token ('a'/'two'/'all'/'any number of'/x/word) — _remove_counter g1
rckind: PTDELTA | rckwords                   // the optional counter KIND: a P/T delta (verbatim) or word(s) -> slugged
rckwords: WORD+                              // kind words; can't cross COUNTER (own terminal) -> stops at the counter
rctarget: (WORD | QUANT | NUM | ZONE | PTDELTA | THATMANY)+  -> rctarget  // the target NP (validated by _RC_TGT)

// DOUBLE — the §107.16/keyword-action 'double <object>' verb, grounded (like the regex) by the generic
// object-verb leaf as double(-, slug(<object>)). We own the clean object shape and apply the EXACT
// `_generic_object_verb`/`_verb_target` guards (_is_compound_object + _OBJ_BAD) in the transformer so
// the output is identical-or-abstain; compound/run-on/'equal to'/'for each'/'unless'/'where'/'if'
// objects defer to the regex.
dbclause: DB_DOUBLE dbbody                   -> dbl
dbbody: (WORD | QUANT | NUM | PTDELTA | TOPREP | FROM | ZONE | COUNTER | ONPREP | DMG | GETS | EQUALTO | THATMANY | MDUR | DEALS)+  -> dbbody

// SWITCH P/T (§613.4e) — 'switch <X>'s power and toughness [until end of turn]' (the `_switch_pt` template).
// Anchored on the distinctive SWITCHPT 'switch' terminal (low collision — in oracle text 'switch' is ~always
// this clause); the object + 'power and toughness' [+ duration] is a flat span validated in the transformer by
// the template's OWN _TGT frame (NO 'power and toughness' terminal -> no corpus-wide lexer poison). ->
// switch_pt(-, _target(X)); duration dropped exactly as the regex does.
swptclause.-2: SWITCHPT swptbody             -> switch_pt_v
swptbody: (WORD | QUANT | NUM)+
SWITCHPT.4: /\bswitch\b/

// ATTACH (§701.3) — 'attach <equipment/aura> to <creature>'. The regex `_attach`
// (`^attach (~|it|<_TGT>) to (<_TGT>)$`) puts the MOVED object (g1) in EXTRA and the DESTINATION (g2)
// in TARGET, splitting at the FIRST ' to ' whose two halves are each a clean `_TGT` (source non-greedy).
// We OWN the shape with two GRAMMAR SPANS — a moved-object span (`atsrc`) and a destination span
// (`atdest`) — joined by the splitting TOPREP. Each span carries its OWN internal 'to's as TOPREP (so
// 'up to N target', or an object-internal 'attached to a creature', stays intact); the transformer then
// reproduces the regex's first-viable-' to ' split over the rejoined spans and validates each half with
// the anchored `_TGT` (`_AT_TGT`). The split TOPREP is required to be the literal 'to' (not into/onto).
// A clause with no viable split (an internal 'to' that leaves a non-`_TGT` half, e.g. 'attach target
// Aura attached to a creature to another creature') or a destination outside `_TGT` ('… to Sokka')
// abstains -> the whole-object-slug `_generic_object_verb` form to the regex (byte-identical-or-abstain).
atclause: AT_ATTACH atsrc TOPREP atdest      -> atattach
// both spans admit TOPREP so 'up to N'/object-internal 'to' is captured as text; the transformer owns the
// faithful first-viable-' to ' split (the rejoined 'atsrc to atdest' run, exactly the regex's body).
atsrc: (WORD | QUANT | NUM | PTDELTA | TOPREP | FROM | ZONE | COUNTER | ONPREP | EQUALTO | THATMANY | MDUR)+  -> atsrc
atdest: (WORD | QUANT | NUM | PTDELTA | TOPREP | FROM | ZONE | COUNTER | ONPREP | EQUALTO | THATMANY | MDUR)+ -> atdest

// FIGHT (§701.12) — '<A> fights <B>' -> fight(-, _target(A), _target(B)=EXTRA). TRUE grammar: the distinctive
// FG_FIGHTS terminal splits the clause into two operand SPANS; the transformer reads them off the tree and
// validates each as a clean `_TGT` (no whole-clause re-parse). Reciprocal '<A> fight each other' rides the
// same fgo span with the FG_EACHOTHER terminal as the object. ('have <A> fight <B>' and the bare 'fight'
// imperative collide with the grant 'have' verb / object-verb leaf at the lexer — left to the regex for now.)
fgclause: fgo FG_FIGHTS fgo            -> fight
fgo: (WORD | QUANT | NUM)+             -> fgo

// TRANSFORM (§701.28) — 'transform <object>' -> transform(-, slug(<object>)), the exact mirror of the
// DOUBLE family: grounded by the generic object-verb leaf (`_verb_target` then `_generic_object_verb`).
// We OWN the clean imperative object shape and apply the SAME guards (_is_compound_object + _OBJ_BAD)
// in the transformer; compound/run-on/'equal to'/'for each'/'unless'/'where'/'if' objects defer to the
// regex. Bare 'transform' only (no 's'): a subject-form '<X> transforms' is left to the regex.
tfclause: TF_TRANSFORM tfbody                 -> tftransform
// MANIFEST (§701.34) — 'manifest the top card of your library' -> manifest(1, top_of_library). TRUE grammar:
// the MF_MANIFEST verb anchors the clause; the transformer validates the fixed shape (structural slice, not
// an interpretive frame regex) and abstains on any other manifest phrasing (anaphoric 'those cards', etc.).
mfclause.-2: MF_MANIFEST mfbody                -> mfmanifest
mfbody: (WORD | QUANT | NUM | ZONE | TOPREP | FROM)+  -> mfbody
tfbody: (WORD | QUANT | NUM | PTDELTA | TOPREP | FROM | ZONE | COUNTER | ONPREP | DMG | GETS | EQUALTO | THATMANY | MDUR | DEALS)+  -> tfbody

// PUT-TO-ZONE family (§401/§400.7) — the zone-move verbs the 'return' family doesn't cover:
// put_on_bottom / put_in_hand / put_on_top. We OWN the clean IMPERATIVE shapes (a leading 'put',
// or a 'conjure …/<obj> into <owner> hand' clause) and ABSTAIN on the subject-prefixed ('<player>
// puts …') and possessive-owner ("<X>'s owner puts it on their choice …") variants — those don't
// begin with 'put' / 'into … hand', so these anchored rules never reach them (faithful-or-abstain).
//
// The body is captured as a FLAT token run and re-joined in the transformer, which applies the SAME
// fixed-frame regexes the templates use (in the SAME precedence order), so the grounded tuple is
// byte-identical to _put_zone/_put_bottom/_put_library_position/_put_bottom_tgt/_put_top_tgt/
// _put_cards_library. NEGATIVE rule priority defers to any competing family parse (Earley ambiguity).
//
// pzputclause: a leading 'put' imperative ('put <X> on the bottom/on top/into … library … / into hand').
pzputclause.-2: PUT pzbody                  -> pzput
// pzhandclause: a leading 'conjure' clause ending in 'into <owner> hand' ('conjure a card named X into
// your hand' — the §711 conjure form `_put_zone` grounds to put_in_hand). Anchored on the leading
// PZ_CONJURE terminal (cheap, like PUT — a broad mid-clause anchor poisons the lexer / explodes Earley),
// so it only fires on conjure-led clauses; the transformer applies the _put_zone frame (which requires
// the body to END in 'into <owner> hand', else abstains — faithful-or-abstain).
pzhandclause.-2: PZ_CONJURE pzbody          -> pzhand
// pzbody deliberately EXCLUDES the COUNTER terminal: a 'put <N> <kind> counter on …' clause is the
// put_counter family (the cclause/putctr rule, default priority) — letting pzbody consume 'counter'
// would offer a competing pzput parse that Earley can pick over putctr, turning an existing put_counter
// grounding into an abstain (a regression). No real put-to-zone clause contains 'counter', so stopping
// pzbody at COUNTER costs nothing and keeps putctr the sole parse for counter clauses (faithful).
pzbody:  (WORD | QUANT | NUM | ZONE | TOPREP | FROM | ONPREP | EQUALTO | PTDELTA | FACE_DIR)+

// LOOK family (§701.x 'look at') — the three dominant frames the regex templates ground:
//   '[<subject> ]look[s] at [the top N cards of ]<owner> hand/library'   (_look_at)
//   'look at the top N cards of your library'                            (_look_top)
//   'look at that many cards from the top of your library'              (_look_that_many)
// TRUE grammar production: the LK_LOOK ('look[s]') verb is the structural anchor that carves the clause
// into a SUBJECT span (`lksubj`, the templates' optional leading `_TGT`) and a BODY span (`lkbody`, the
// templates' post-verb 'at …' portion). The transformer READS the two spans off the tree and applies the
// three templates as anchored per-operand validators (in TEMPLATE PRECEDENCE ORDER), so the grounded
// tuple is byte-identical to parse_effect or — on any clause outside those frames (an open-ended 'look at
// <obj>' shape the templates don't have, or a swallowed wrapper) — abstain (faithful-or-abstain). A
// subject before the verb is allowed (the templates' optional leading '(<TGT>) '). NEGATIVE rule priority
// so a sentence ALSO parseable as another family yields to that parse; a pure 'look at …' has no rival.
lkclause.-2: lksubj? LK_LOOK lkbody       -> look
lksubj: (WORD | QUANT | NUM | ZONE)+       // player phrase before 'look[s]' (the templates' optional <TGT>)
lkbody: (WORD | QUANT | NUM | ZONE | TOPREP | FROM | EQUALTO | THATMANY | FACE_DIR)+

// SHUFFLE family (§103.2/§701.19) — the two templates the regex grounds:
//   '[<subject> ]shuffle[s] [their library | <obj> into <owner> library]'   (_shuffle: extra='-')
//   '<player> shuffles <source> into his or her library'                    (_shuffle_subj: from_<src>)
// TRUE grammar production: the SH_SHUFFLE ('shuffle[s]') verb is the structural anchor that carves the
// clause into a SUBJECT span (`shsubj`) and a BODY span (`shbody`). The transformer READS the two spans
// off the tree and applies the two templates as anchored per-operand validators in REGISTRATION ORDER.
// _shuffle is registered FIRST and its '… into <your|their|its owner's|their owner's> library' BODY
// branch already swallows most subject-source clauses (extra='-'); _shuffle_subj fires ONLY for a 'his or
// her library' destination its body doesn't list (so `_SH_BODY` leaves it unconsumed and falls through).
// A subject before the verb is allowed. NEGATIVE rule priority defers to any competing family parse.
shclause.-2: shsubj? SH_SHUFFLE shbody?   -> shuffle
shsubj: (WORD | QUANT | NUM | ZONE)+       // player phrase before 'shuffle[s]' (the templates' optional <TGT>)
shbody: (WORD | QUANT | NUM | ZONE | TOPREP | FROM | EQUALTO)+

// NEGATIVE STATICS (namespaced `ns`) — the §509/§508 combat prohibitions and the §502 no-untap static:
//   cant_be_blocked : '<TGT> can't be blocked [this turn]'            (_cant_combat / _cant_combat_set)
//   cant_block      : '<TGT> can't block [<TGT>] this turn' / '<set> can't block'  (same two templates)
//   doesnt_untap    : "<TGT> doesn't/don't untap during <ctrl>'s [next] untap step[s] [for as long as …]"  (_doesnt_untap)
// These are mid-clause-anchored shapes (a subject NP precedes the distinctive verb), so unlike the
// leading-anchored families they cannot key on a clause-initial literal. Instead each rule keys on a
// HIGH-PRIORITY distinctive terminal — NS_CANT ("can't") or NS_DUVERB ("doesn't/don't untap") — and
// captures the surrounding subject/tail as FLAT token runs purely to consume the whole string. The
// faithful parse is then the EXACT original template regex re-applied to the lowercased source in
// `self._src` (frame-regex, like rcremove/dbl/pzput), so the grounded tuple is BYTE-IDENTICAL to
// parse_effect's _cant_combat/_cant_combat_set/_doesnt_untap, or — when the frame rejects the clause
// (an 'except by …'/conditional rider on the block forms, a non-_TGT subject, a 'this combat' variant)
// — abstain. The transformer ONLY emits a verb in {cant_be_blocked, cant_block, doesnt_untap}: the
// shared block template also grounds cant_attack/cant_attack_or_block/cant_block_or_be_blocked, which
// are OTHER families, so those are filtered to None here (faithful-or-abstain).
//
// NEGATIVE rule priority: NS_CANT/NS_DUVERB are mid-clause, so a sentence ALSO parseable as another
// family (an earlier @_t template could ground its lead differently) must yield to that parse; on a
// pure negative-static clause there is no competitor and these rules still win. The frame regexes are
// anchored ^…$ over the whole source, so the flat runs' exact tokenization is irrelevant to the slug.
nsclause.-2: nssubj NS_CANT nsverb nstail?     -> nscant     // '<subj> can't <combat-verb> [<obj>] this turn'
           | nssubj NS_DUVERB nstail           -> nsuntap    // "<subj> doesn't/don't untap during …"
nssubj: (WORD | QUANT | NUM | ZONE)+           // the subject NP (validated by the frame regex's _TGT)
nsverb: (WORD | ZONE)+                          // 'be blocked' / 'block' / 'attack' / 'block or be blocked' …
nstail: (WORD | QUANT | NUM | ZONE | COUNTER | FROM | ONPREP | TOPREP | PTDELTA | MDUR | FACE_DIR | FORASLONGAS)+  // 'this turn', 'during …', 'for as long as …', a rider

NS_CANT.5: /\bcan't\b/                          // the §509/§508 prohibition modal (outranks WORD)
NS_DUVERB.5: /\b(?:doesn't|don't) untap\b/      // the §502 no-untap static verb (outranks WORD)

// ADD_MANA family (§106/§605) — '[<player>] add[s] [an additional] <mana-spec>'. This family is
// almost ENTIRELY a formal symbol-sublanguage: the clause STRUCTURE is trivial (optional subject,
// the 'add' verb, an optional 'additional' modifier), and ALL the substance is the <mana-spec>, which
// is the mana-symbol/colour formal language parsed by card_effects._mana_production (reused verbatim —
// NOT re-implemented here). The structure is now a TRUE GRAMMAR PRODUCTION (no whole-clause re-parse
// regex): an optional subject span `amlead`, the 'add' verb, an optional `AM_ADDL` modifier
// ('an additional'/'additional'), and the mana-spec span `amrest`. The transformer VALIDATES `amlead`
// with the anchored `_TGT` operand regex `_AM_TGT` (mirrors `_DB_TGT`/`_AT_TGT`/`_TF_TGT`) and feeds the
// `amrest` span (token-rejoined, mana glyphs re-uppercased) to `_mana_production` (reused verbatim).
// Because `_mana_production` parses {…} symbols by findall and English phrases by fullmatch, the grounded
// `prod` (hence amount=len(prod) + extra=dedup-join) is INDEPENDENT of inter-token spacing, so the tuple
// is byte-identical to the old `_add_mana`/`_AM_FRAME` output — or None (abstain) when `_mana_production`
// rejects the spec or `amlead` isn't a clean `_TGT`. NEGATIVE rule priority defers to any competing
// family parse (Earley ambiguity); on a real 'add <mana>' clause there is no competitor, so this wins.
amclause.-2: amlead? AM_ADD AM_ADDL? amrest -> amadd
// CONDITIONAL/VARIABLE mana (§106.3, state-derived amounts) — three sibling productions, each a TRUE
// grammar shape whose conditional tail is captured as a SPAN read off the parse tree (no whole-clause
// re-parse, no self._src). They are NEGATIVE-priority like `amadd` so any competing family parse wins;
// the dedicated markers (AM_AMOUNTOF / EQUALTO / FOREACH / AM_WHEREX) keep them disjoint from the
// fixed-mana `amadd` (which has no such marker, so its `amrest` never reaches one — fixed mana is
// byte-identical, unchanged). The amount becomes the repo-standard state-derived slug:
//   shape 1 'add an amount of <sym> equal to <expr>'        -> add_mana("equal_to_<slug(expr)>",  you, <color of sym>)
//   shape 2 'add X mana of any [one] color, where X is <expr>' -> add_mana("equal_to_<slug(expr)>", you, any[_one]_color)
//   shape 3 'add <mana-spec> for each <thing>'              -> add_mana("1_per_<slug(thing)>",      you, <color(s)>)
amceqclause.-2: amlead? AM_ADD AM_ADDL? AM_AMOUNTOF AM_MANASYM EQUALTO amexpr -> amadd_eq    // shape 1
amcxclause.-2:  amlead? AM_ADD AM_ADDL? amxmana AM_WHEREX amexpr               -> amadd_wherex // shape 2
amcfeclause.-2: amlead? AM_ADD AM_ADDL? amrest FOREACH amexpr                  -> amadd_foreach // shape 3
amlead: (WORD | QUANT | NUM)+               // optional player subject before 'add[s]' (validated by _AM_TGT)
amrest: (WORD | QUANT | NUM | AM_MANASYM)+  // the mana-spec span (fed to _mana_production, spacing-independent)
amxmana: (WORD | QUANT | NUM)+              // the 'X mana of any [one] color' span (validated/colored below)
amexpr: (WORD | QUANT | NUM | AM_MANASYM | TOPREP | FROM | ZONE | EQUALTO | COUNTER | ONPREP)+  // the state-derived <expr>/<thing> span (slugged)

ccreator: (WORD | QUANT)+               // optional creator player phrase ('target opponent creates …')
cspec: (WORD | NUM)+                    // the token descriptor (P/T + colors + types) up to 'token[s]'
cforeach: FOREACH cfeword               // 'for each <X>' — regex keeps only the FIRST word of X
cfeword: WORD | NUM | QUANT | PTDELTA    // first word may be a '+1/+1' counter kind (lexed as PTDELTA)
ctail: (WORD | NUM | QUANT | TOPREP | FROM | ZONE | DEALS | DMG | GETS | PTDELTA | TOKEN | MDUR | QUOTED)*  -> ctail  // dropped (regex's trailing '.*'); QUOTED lets 'token with "<ability>"' parse

psubj: (WORD | QUANT)+                  // a player phrase before the verb (you / each player / target player)
pbody: (WORD | NUM | QUANT | FACE_DIR)+            // amount (+ object word: 'cards'/'life')
dsrc: (WORD | QUANT | COLON)+           // damage source (DROPPED — implicit self, matching the regex; COLON
                                        // lets a non-mana activation cost 'Sacrifice ~:' be swallowed before 'deals')
damamt: NUM | QUANT | WORD             // single-token damage amount (N / X)
dtarget: (WORD | QUANT | NUM | ZONE | EQUALTO)+   // basic target NP (no TOPREP: internal 'to' -> abstain). 'equal to' stays content here.
dteqtgt: (WORD | QUANT | NUM | ZONE)+   // variant target NP — stops at TOPREP and at EQUALTO (the rider boundary)
deqamt: (WORD | QUANT | NUM | ZONE)+    // amount-first amount: stops at the FIRST 'to' (regex non-greedy); an internal ' to ' -> won't parse -> abstain
dvamt: (WORD | QUANT | NUM | ZONE | TOPREP | EQUALTO)+   // target-first amount: runs to end of string
ddivtgt: (WORD | QUANT | NUM | ZONE | TOPREP | EQUALTO)+ // divided targets: run to end (grounded raw, like the regex)
mtgt: (WORD | QUANT)+                   // the creature getting the P/T boost
mdur: MDUR
mferest: (WORD | QUANT | NUM | TOPREP | FROM | ZONE | MDUR | BOUND | PTDELTA | EQUALTO | DEALS | DMG | GETS | ONPREP | COUNTER)+   // the 'for each <X>' object span
gtgt: (WORD | QUANT | NUM)+             // the permanent/player receiving the grant (stops at gains/has/have)
gkw: WORD (WORD | NUM | QUANT | TOPREP | FROM | ZONE | AM_MANASYM)*   // keyword phrase: first token a plain WORD (so 'gains 3 life' -> pcount, not here); AM_MANASYM lets a kw carry its cost ('ward {2}', 'ninjutsu {1}{u}') — _kw_ok slugs it ('ward_2') exactly as the bare-number 'ward 2' already grounds
csubj: (WORD | QUANT | NUM | ZONE)+     // a player phrase before 'put' (DROPPED — must be a clean player, else abstain)
ccount: THATMANY | QUANT | WORD | NUM   // the counter count: 'a'/'two'/'up to N'/'that many'/N/X/word
ckind: PTDELTA | ckwords               // the counter KIND: a P/T delta (kept verbatim) or word(s) -> slugged
ckwords: WORD+                          // kind words; can't cross COUNTER (own terminal) -> stops at the counter
// the object after 'on' spans to end but must NOT re-cross a structural 'counter'/'on'/'into'/'onto':
// the regex binds the FIRST 'counter on', so a clause with a second one ('… and a +1/+1 counter on Y',
// '… into your hand') is a run-on/zone-move -> no parse -> abstain to the regex (faithful-or-abstain).
ctarget: (WORD | QUANT | NUM | ZONE | PTDELTA | THATMANY)+   // object after 'on' (spans to end)

zonephrase: TOPREP zwords? ZONE        -> zone
fromphrase: FROM zwords? ZONE          -> source
zwords: (WORD | TOPREP)+
trailer: BOUND (WORD | TOPREP | ZONE | QUANT | NUM | FACE_DIR)*   -> trailer
quant: QUANT
robj: (WORD | ZONE | EQUALTO | FACE_DIR)+          // return object stops at from/to; 'equal to' stays content
objall: (WORD | TOPREP | ZONE | FROM | EQUALTO | FACE_DIR)+   // object verbs ('equal to' stays content); FACE_UP/DOWN kept in-span so 'exile a card face down' is unchanged

RVERB: "return"
// RETHAND — the distinctive trailing owner-hand destination of the §614 bounce (`_bounce`/`_regrowth`).
// Matches EXACTLY the destinations those templates accept ('its owner's hand', 'your hand', "their
// owners' hands", 'its owner's hands') anchored at clause end ($). High priority (.5) so it wins the
// 'to'/'hand'/possessive tokens away from WORD/ZONE/TOPREP, forcing the object/destination split the
// generic `rclause` can't make. Anchored to end -> a trailing rider ('… at the beginning of …') leaves
// the regex `_return_zone` to own it (no parse here -> abstain), faithful to `_bounce`'s own `…$`.
RETHAND.5: /\bto (?:its owner's|your|their owners'|their owner's|their|his or her) hands?/
OVERB: %(verbs)s
CVERB.3: /\bcreates?\b/
CCOUNT.3: /\b(?:a|an|one|two|three|four|five|six|seven|eight|nine|ten|x|[0-9]+)\b/
TOKEN.4: /\btokens?\b/
FOREACH.4: /\bfor each\b/
PVERB.2: /\b(?:draws|draw|mills|mill|scries|scry|surveils|surveil|blights|blight|loses|lose|discards|discard)\b/
RVREVEAL.3: /\breveals?\b/
PVPREVENT.3: /\bprevent\b/
SF_VERB.3: /\b(?:sacrifices?|exiles?)\b/     // subject-first object verbs (the SUBJECT precedes the verb)
RC_REMOVE.3: /\bremoves?\b/            // 'remove' — the §701.45/counter-removal verb (remove_counter family; namespaced)
DB_DOUBLE.3: /\bdouble\b/             // 'double' — the §107.16 doubling verb (double family; namespaced; not 'doubles', which the regex object-verb doesn't ground)
LK_LOOK.3: /\blooks?\b/               // 'look'/'looks' — the §701.x 'look at' verb (look family; namespaced)
SH_SHUFFLE.3: /\bshuffles?\b/         // 'shuffle'/'shuffles' — the §701.19 shuffle verb (shuffle family; namespaced)
AM_ADD.3: /\badds?\b/                 // 'add'/'adds' — the §106 mana-production verb (add_mana family; namespaced)
AM_ADDL.4: /\b(?:an additional|additional)\b/  // the optional 'additional' modifier after 'add' (consumed grammar-side; the old frame's `(?:an additional |additional )?` group, so amrest never carries it)
AM_MANASYM.4: /\{[^}]*\}/             // a single mana symbol '{G}'/'{C}' (BOUNDED — never a greedy .*; '{' '}' aren't in WORD)
AM_AMOUNTOF.5: /\ban amount of\b/     // §106.3 'add an amount of <sym> equal to …' lead-in (outranks QUANT's 'an' + WORD; appears ONLY in conditional shape 1, so fixed-mana amrest is untouched)
AM_WHEREX.5: /\bwhere x (?:is|equals)\b/  // §106.3 'add X mana of any [one] color, where X is <expr>' connective (outranks WORD; conditional shape 2 only)
AT_ATTACH.3: /\battach\b/             // 'attach' — the §701.3 attach keyword action (attach family; namespaced; imperative only)
FG_FIGHTS.5: /\bfights\b/             // '<A> fights <B>' separator (§701.12 fight; the 's' form, distinct from the rarer bare 'fight')
TF_TRANSFORM.3: /\btransform\b/       // 'transform' — the §701.28 transform keyword action (transform family; namespaced; bare imperative, not 'transforms')
MF_MANIFEST.3: /\bmanifest\b/         // 'manifest' — the §701.34 manifest keyword action (manifest family; namespaced)
PHASE.4: /\bphases? (?:out|in)\b/     // '<X> phase[s] out/in' — §702.26 phasing (phase_out/phase_in; the 'out'/'in' is bound to 'phase' so a lone in/out is never stolen)
RDIS.5: /\bis dealt to\b/             // '… is dealt to <B>' — the §614.9 redirect split (distinct from the source-side 'would be dealt to')
EXCESS.6: /\bexcess damage\b/         // §120.4 'excess damage' redirect anchor (whole bigram so it can't steal a bare 'excess')
SKIP.4: /\bskips?\b/                  // '[<player>] skip[s] …' — §500.7 skip-a-step/phase/turn (skip family; namespaced)
AMASS.4: /\bamass\b/                  // 'amass <type> <N>' — §701.43 amass keyword action (amass family; namespaced; rare word, low collision)
CP_COPY.3: /\bcopy\b/                 // 'copy <object>' — §707 copy verb (copy family; namespaced; leading imperative, mirrors DB_DOUBLE)
EXILE.3: /\bexiles?\b/                // 'exile …' — leading §701.x exile verb for the top-of-library / until-leaves shapes (mirrors CP_COPY; dynamic lexer also explores SF_VERB/OVERB on 'exile')
XLIB.5: /\blibrar(?:y|ies)\b/         // 'library'/'libraries' — the trailing anchor of `_exile_top` (outranks ZONE so xtbody stops here; re-validated in the transformer)
XLEAVES.5: /\bleaves the battlefield\b/  // 'leaves the battlefield' — the distinctive `_exile_until` trailing anchor ('until ~ leaves the battlefield')
MRABLE.5: /\bif able\b/               // '… if able' — the §508/§509 attack/block requirement anchor (distinctive; the ONLY must_attack/must_block terminal)
MONSTROSITY.4: /\bmonstrosity\b/      // 'Monstrosity <N>' — §701.x keyword action (namespaced; rare word)
GOADED.5: /\bis goaded\b/             // '<creature> is goaded' — the §701.38 passive goad bigram (distinctive)
BECOMESDESIG.6: /\bbecomes? (?:foretold|plotted|blocked|snow|saddled)\b/   // '<subj> becomes foretold/plotted' — §701 status designation bigram (outranks BCM_COP); + §509 'becomes blocked' (forced-block state change) + §205 'becomes snow' (supertype set) + §702 'becomes saddled' (the saddle keyword's status, OTJ 2024 — saddle is a grounded keyword ability) — all the same becomes(-, subj, <state>) shape
NOLONGERSUSP.6: /\b(?:is|are|becomes?) no longer suspected\b/   // '<subj> is/are/become no longer suspected' — §701.60 suspect removal (outranks BCM_COP)
FLIPCOIN.5: /\bflip a coin(?: until you lose a flip)?\b/   // 'Flip a coin [until you lose a flip]' — §701.x (whole phrase, distinctive)
FIGHTEACH.5: /\bfight each other\b/   // '<creatures> fight each other' — §701.12 reciprocal fight (distinct from FG_FIGHTS 'fights')
KWACTION_N.4: /\b(?:bolster|adapt|incubate|support|amass|airbend|earthbend|waterbend|discover)\b/   // numbered §701 keyword actions (distinctive; '<verb> <N>'); migrated off _kwaction_n: +amass +the Avatar bending family (air/earth/water; 'firebend' is NOT a keyword action) +discover (§701, LCI 2024 — 'discover N')
ENDURE.4: /\bendures?\b/   // §701 endure (BLB/DSK 2024) — '<creature> endures N' (subject-prefixed; _endure)
KVINTRANS.4: /\b(?:investigates?|explores?|proliferates?|connives?|populates?|forages?|planeswalks?|learns?)\b/   // intransitive §701 keyword actions (distinctive); migrated off _bare_action: +populate/forage/planeswalk/learn
EXCHANGE.3: /\bexchange\b/   // 'exchange <object>' — §701.10 exchange verb (in _OBJ_VERBS; mirrors DB_DOUBLE)
GCC_CAN.5: /\bcan (?:attack|block)\b/ // '… can attack/block …' — the §509/§508 combat-PERMISSION anchor (grant_combat family; the bigram is distinctive — bare 'can' collides, 'can attack'/'can block' don't; outranks WORD)
DEALS.2: /\bdeals?\b/
DMG.2: /\bdamage\b/
GETS.2: /\bgets?\b/
GVERB.3: /\b(?:gains?|has|have)\b/
YOURCHOICE.6: /\byour choice of\b/    // '<tgt> gains your choice of <kw-list>' — §700.2 keyword-choice grant anchor
QUOTED.5: /"[^"]*"/                    // a quoted ability (bounded — an unanchored .* poisons the dynamic lexer)
CHS_CHOOSE.3: /\bchooses?\b/         // 'choose'/'chooses' — the §700.2 choice verb (namespaced; below DIVIDED's 'choose')
CHOOSE_NEW_TGT.6: /\bchoose new targets for\b/   // §707.10 copy-redirect anchor (beats CHS_CHOOSE)
WITHOUT_PAY.6: /\bwithout paying its mana cost\b/   // §601 free-cast modifier anchor (distinctive phrase)
FORASLONGAS.6: /\bfor as long as\b/   // impulse play-duration anchor ('play X for as long as it remains exiled')
ASTHOUGH_FLASH.6: /\bas though (?:it|they) (?:had|have) flash\b/   // §117.1a impulse instant-speed anchor (full phrase, not bare 'as though')
FACE_DIR.6: /\bface (?:up|down)\b/      // §708 'turn <X> face up/down' anchor (also kept in the object spans to preserve them)
PUT.3: /\bputs?\b/
PZ_CONJURE.3: /\bconjures?\b/   // §711 'conjure' — the leading anchor for the put_in_hand (conjure …) clause
COUNTER.4: /\bcounters?\b/
DISTRIBUTE.4: /\bdistribute\b/   // §122 'distribute <N> <kind> counters among …' anchor (_distribute_counters)
MOVE.3: /\bmoves?\b/   // §122 'move <N> <kind> counters from <X> onto <Y>' anchor (_move_counter_from)
ECOMBAT.5: /(?:after this (?:phase|main phase), )?there is an additional combat phase(?: followed by an additional main phase)?/   // §505 extra_combat whole-phrase (constant tuple)
LURELEAD.5: /all creatures? able to block/   // §509 lure lead anchor (_lure)
DOSO.5: /do so/   // §509 lure trailing anchor
YOUCTRL.5: /you control/   // §720 clause-initial 'you control <X>' static-control anchor (_control bare branch)
ONPREP.3: /\bon\b/
THATMANY.4: /\bthat many\b/
PTDELTA.4: /[+-](?:\d+|x)\/[+-](?:\d+|x)/
BASEPT.6: /\bbase power and toughness\b/   // §208/§613.3 base-P/T-set anchor (distinctive)
ALSO.4: /\balso\b/                  // 'is/are also a <type>' — §205 type-addition anchor (_type_also)
CHOSENTYPE.6: /\bthe chosen type\b/   // 'is the chosen type' — §205 (_becomes_chosen type branch; 'chosen color' is the color slice)
EVERYTYPE.6: /\bevery (?:creature|basic land|nonbasic land|land) type\b/   // '<subj> is every <kind> type' — §205 all-types anchor (_all_types); distinctive, becomes-only
BCM_PT.5: /(?:[0-9]|x|\*)+\/(?:[0-9]|x|\*)+/   // a set base P/T ('2/1','x/x','*/*') — regex `[\dX*]+/[\dX*]+` (input is lowercased). Outranks WORD so the P/T slot is unambiguous.
BCM_COP.4: /\b(?:becomes?|are|is)\b/             // the becomes/is/are copula (the optional 'a/an' reuses QUANT, not a new terminal)
COLOR.6: /\b(?:white|blue|black|red|green|colorless|all colors|that color|the chosen color)(?: in addition to its other colors)?(?: until end of turn)?\b/   // _becomes_color literal-color slice + greedy riders
COPYOF.5: /\ba copy of\b/   // '<subj> becomes a copy of <X>' — §707 (the becomes-copy anchor)
COPYTOK.6: /\btokens? that(?:'s| are) (?:a )?cop(?:y|ies) of\b/   // 'token[s] that's/are [a] copy/copies of' — the create-copy anchor (§707); priority above TOKEN/COPYOF so it claims the whole phrase
OFCHOICE.5: /\bof your choice\b/   // '<subj> becomes the <X> of your choice' — §700.2 (becomes-choice anchor)
MDUR.3: /\b(?:until end of turn|until end of combat|until your next turn|until end of your next turn|this turn)\b/
DIVIDED.4: /\bdivided as you choose among\b/
THATMUCH.4: /\bthat much\b/
EQUALTO.3: /\bequal to\b/
QUANT.2: /\b(?:up to (?:one|two|three|four|five|that many|x|[0-9]+)|any number of|a|an|one|two|three|four|five|target|all|each|another|x)\b/
TOPREP.2: /\b(?:to|into|onto)\b/
FROM.2: /\bfrom\b/
ZONE.2: /\b(?:hand|battlefield|library|graveyard)\b/
BOUND.3: /\b(?:until|unless|for each)\b/
COLON.2: /:/                          // activation-cost separator ('<cost>: <effect>') — lets dsrc swallow a
                                      // non-mana cost prefix (Sacrifice ~: / Tap …:) before 'deals', like the regex source
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
# an object that swallowed a following clause ('Destroy X, then ~ deals damage to Y') — the leaf must
# stop at the first verb; abstain so the upstream sentence-splitter / wrapper chain owns the sequence.
_MULTICLAUSE = re.compile(r"\bthen\b|\bdeals?\s+\S+\s+damage\b", re.I)
# a tribal-ANTHEM subject that is a comma-list of creature TYPES ('[Other] Skeletons, Vampires, and Zombies
# [you control]') — a single boost subject, NOT a multi-clause. The grammar already parsed the whole clause as
# ONE mclause (subject GETS delta), so a comma here is always within the subject; this lets the boost
# transformer keep it instead of abstaining on the bare comma. Strict (every item a plural noun) so it can't
# match a genuine multi-subject ('you, target opponent, and each player').
_TYPELIST_ANTHEM = re.compile(r"^(?:other )?[\w'-]+s(?:, [\w'-]+s)*,? and [\w'-]+s(?: you control)?$", re.I)

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


# a grant keyword phrase is FAITHFUL only if it is a single clean §702 keyword (optionally with its
# standard 'from/of' qualifier or a numeric parameter). The regex `_gains_perm` greedily slugs a
# trailing duration/condition ('flying as long as …', 'haste until your next turn') or a multi-keyword
# list ('hexproof and indestructible') into ONE extra — a LOSSY tuple. We ABSTAIN on those (the slug
# would carry clause-junk), keeping only the clean grant; the regex fallback owns the lossy whole.
_KW_LOSSY = re.compile(r"_until_|_as_long_as_|_this_turn|_this_combat|_during_|_for_as_long|"
                       r"_unless_|_whenever_|_if_|_as_though_|_where_|_permanently$|_and_")


# A faithful grant target is a NOUN PHRASE. Two ways the greedy `gtgt` over-matches and must abstain:
#  (1) it binds to a clause-internal 'has/have' ("…didn't HAVE defender", "…as long as it HAS flying"),
#      leaving verb material in the target;
#  (2) the leaf is fed a sentence whose leading wrapper the production chain would have peeled but here
#      hasn't ("Until end of turn, <t> gains …", "If …, <t> gains …", "When you do, … and it gains …"),
#      so the target swallows a whole preceding clause.
# Either way the target carries clause-level material no `_TGT` noun phrase holds -> abstain (the regex
# leaf abstains too; in production the wrapper chain peels the lead and the clean residue re-enters).
_TGT_BAD = re.compile(
    r"^(?:until |if |when |whenever |at |for each|as you |after |during your turn\b)|"   # swallowed lead
    r",|"                                                                                 # comma = two clauses
    r"\b(?:has|have|gains?|gets?|can|could|can't|cannot|doesn't|didn't|assigns?|is|are|"
    r"becomes?|loses?|create|creates?|return|returns?|exile|exiles?|put|puts?|destroy|"
    r"destroys?|draws?|deals?|sacrifices?|adds?|attacks?|blocks?)\b|"                      # embedded verb
    r"\bas long as\b|\bas though\b|\bin addition\b|\band they\b|\band it\b|\bexcept\b|\balso\b",
    re.I)


_KW = ground.keyword_abilities()                   # §702 roster (set) — reproduces _granted_keyword_cost's gate
_CEQMANA_RE = re.compile(r"^the (\w+) cost is equal to its mana cost$", re.I)  # `_granted_keyword_cost`'s pattern


def _clean_kw(extra: str) -> bool:
    """True iff `extra` (a slugged keyword grant) is a single faithful §702 keyword, not a lossy
    duration/condition tail or a crammed multi-keyword list."""
    if _KW_LOSSY.search(extra):
        return False                           # duration/condition tail or 'kw and kw' list -> lossy
    if "_or_" in extra and not extra.startswith("protection_from"):
        return False                           # 'kw or kw' list (protection's own 'or from' is fine)
    return True


def _ret_ambiguous(s: str) -> bool:
    if "," in s:
        return True                            # coordinated multi-object list
    return len(re.findall(r"\b(?:to|into|onto)\b", _UPTOQ.sub(" ", s))) >= 2


# the regex `_becomes` type tail is `([\w' -]*?)(?: with [\w, ]+?)?(?: until end of turn)?$` — a
# NON-GREEDY g3. We reconstruct it EXACTLY: g3 is the shortest prefix of the post-P/T span T whose
# every consumed char is in `[\w' -]` (so it can't cross a comma) such that the remainder matches
# the optional ' with <kw>' + trailing ' until end of turn'. Returns None if no such split (the
# clause isn't the clean pt shape — abstain). Validated tuple-identical on every pt clause.
_BCM_G3CHAR = re.compile(r"[\w' -]")
_BCM_REM = re.compile(r"^(?: with [\w, ]+?)?(?: until end of turn)?$", re.I)
# The greedy `bcmtgt` can swallow a leading wrapper ('Until end of turn, …', 'If …, it', 'you may
# have …') that the regex's anchored `_TGT` would never match — grounding those is LOSSY (wrong
# target). Gate the target on the EXACT `_TGT` noun-phrase regex: only own clauses whose subject is a
# legitimate `_TGT` (faithful-or-abstain). In production the wrapper chain peels the lead and re-feeds
# the clean residue, so abstaining here loses nothing.
from card_effects import _TGT as _BCM_TGT_SRC
from card_effects import _that_amt as _that_amt   # 'twice/half that much [plus N]' amount slug — reused, not re-derived
_BCM_TGT = re.compile(r"(?:" + _BCM_TGT_SRC + r")$", re.I)
# `_damage_self` (deal_teq 'itself' branch): the regex's g1 is `({_TGT})` — the SOURCE creature, which is
# also the (self-)target. Anchor the dsrc span on the EXACT shared `_TGT` noun phrase so a card-name /
# wrapper-swallowed source abstains exactly as the regex does (faithful-or-abstain).
_DSELF_TGT = re.compile(r"^(?:" + _BCM_TGT_SRC + r")$", re.I)
# DRAW <N> cards for each <X> — `_draw_foreach`'s exact pattern (count-scaled draw); re-applied to src by pcount.
_DFE_RE = re.compile(r"^(?:(" + _BCM_TGT_SRC + r") )?draws? (a card|\w+) cards? for each (.+?)$", re.I)
# DRAW/MILL dynamic amount-expr — `_flow_amount`'s exact pattern (up-to-N / equal-to-X /
# as-many-as-X / half-X). Re-applied to src by pcount; reproduces its amt logic byte-for-byte.
_FLOW_RE = re.compile(r"^(?:(" + _BCM_TGT_SRC + r") )?(draws?|mills?) (up to \w+ cards?|cards? equal to .+?|as many cards as .+?|half(?: of)? .+?)$", re.I)
# DRAW/MILL 'that many cards' anaphoric amount — `_draw_that_many`/`_mill_that_many`'s exact (symmetric)
# pattern (amount via _that_amt: [twice|half] that many [plus|minus N]). MUST be tried BEFORE _FLOW_RE in
# pcount: the regex chain has `_{draw,mill}_that_many` ahead of `_flow_amount`, and _FLOW_RE's
# 'half(?: of)? .+?' alt would otherwise grab 'half that many cards' as half_that_many_cards instead of
# the _that_amt 'half_that_amount'.
_MTM_RE = re.compile(r"^(?:(" + _BCM_TGT_SRC + r") )?(draws?|mills?) (twice |half )?that many cards( plus \w+| minus \w+)?$", re.I)
# DRAW <N> ADDITIONAL cards — `_draw_additional`'s exact pattern (extra='additional'); re-applied to src by
# pcount, which otherwise dies on the 'additional' word (amt 'an additional' isn't a number). Reproduces the
# regex tuple byte-for-byte: amount = _amount(count) or 1, target = _target(subj or 'you'), extra='additional'.
_DADD_RE = re.compile(r"^(?:(" + _BCM_TGT_SRC + r") )?draws? (an|a|\w+) additional cards?$", re.I)
# <subj> draws N cards — `_draw_tgt`'s exact pattern, for a _TGT subject pcount's player-gate rejects
# (quantified players / 'each player who …' / the lossy compound-as-draw leaf). Re-applied to src by pcount;
# uses the SAME _TGT span as the regex so it grounds exactly what `_draw_tgt` does, byte-for-byte.
_DRAW_TGT_RE = re.compile(rf"^({_TGT}) draws? (a card|\w+) cards?$|^({_TGT}) draws? (a) card$", re.I)
# <subj> loses N life — `_lose`'s exact pattern, for a _TGT subject pcount's player-gate rejects ('each
# opponent who can't', 'each opponent with no cards in hand', 'any number of target players each'). Re-applied
# to src by pcount; same _TGT span as the regex -> grounds exactly what `_lose` does, byte-for-byte.
_LOSE_TGT_RE = re.compile(rf"^({_TGT}) loses? (\w+) life$", re.I)
# <subj> gains N life — `_gain`'s exact pattern, for an EXPLICIT _TGT subject the grant rule's plain gain
# branch rejects (it only grounds `_PLAYER` subjects). 'any number of target players each gain 6 life',
# 'each player who … gains 1 life'. Re-matched on src in the grant transformer -> byte-for-byte `_gain`.
_GAIN_TGT_RE = re.compile(rf"^({_TGT}) gains? (\w+) life$", re.I)
# <subj> mills N cards — `_mill`'s exact pattern, for a _TGT subject pcount's player-gate rejects ('any number
# of target players each mill two cards'). Re-applied to src by pcount; same _TGT span -> byte-for-byte `_mill`.
_MILL_TGT_RE = re.compile(rf"^({_TGT}) mills? (a card|\w+) cards?$", re.I)
_LEADING_EFFECT_VERB = re.compile(
    r"^(?:destroys?|exiles?|taps?|untaps?|sacrifices?|returns?|puts?|creates?|counters?|deals?|draws?|"
    r"discards?|mills?|gains?|loses?|searches?|shuffles?|scry|scries|surveils?|attaches?|distributes?|"
    r"regenerates?|reveals?|proliferates?)\b", re.I)
def _compound_subj(s):
    """True when a captured `({_TGT})` 'subject' actually spans a PRECEDING clause (a compound, e.g. 'destroy
    target creature an opponent controls and you' from 'destroy … and you gain N life') rather than a clean
    player NP. The permissive _TGT in the broad-subject `_*_TGT_RE` branches lets it swallow the whole run-on,
    grounding it lossily (a cross-verb DIFFERS — lark says gain_life, regex says destroy). The tell is a LEADING
    EFFECT VERB: a real player NP never starts with one (it starts each/any/target/a/all/that/its/you/the…),
    whereas the swallowed clause does. NB: a bare ' and ' is NOT used — it wrongly rejects valid relative-clause
    subjects ('each opponent who controls an artifact and a creature'). Production _smart_splits the real compound."""
    return bool(_LEADING_EFFECT_VERB.match(s.strip().lower()))
# REGENERATE <obj> with a rider — `_generic_object_verb`'s last-resort pattern (`^(\w+) (.+?)$`, _OBJ_VERBS
# gated by _OBJ_BAD + _is_compound_object). The imperative leaf abstains on a 'with a +1/+1 counter on it' /
# 'that has …' rider (_WITHCTR); _generic_object_verb grounds the whole NP as the slug target. Re-match on src.
from card_effects import _OBJ_BAD as _OBJ_BAD
_GENOBJ_RE = re.compile(r"^(\w+) (.+?)$", re.I)
# TAP/UNTAP <obj> — `_taputap`'s exact pattern: an optional leading subject (DROPPED) + tap/untap + the object
# `({_TGT})`, where _TGT spans a 'with <counter>' / 'that has …' RIDER and a subject-prefixed '<player> untaps
# <their permanents>'. The imperative leaf abstains on those (the _WITHCTR guard / no leading-subject form); a
# src re-match here reproduces the WHOLE-NP target byte-for-byte. group1=verb, group2=object.
_TAPUNTAP_RE = re.compile(rf"^(?:{_TGT} )?(tap|untap)s? ({_TGT})$", re.I)
# 'tap or untap <X>' — the coordinated §701.20/§701.21 idiom (`_tap_or_untap` -> untap(-, X, 'or_tap')). The
# imperative leaf splits verb='tap', rest='or untap …' and the _COORD guard defers it; reproduce the template
# here from src so lark owns it (the _GENOBJ generic path is scoped to destroy/regenerate, excluding this).
_TAP_OR_UNTAP_RE = re.compile(rf"^tap or untap ({_TGT})$", re.I)
# '<subj> enters with N [additional] <kind> counter(s) on it' — an ETB counter placement (§614/§122,
# put_counter / cond on_enter). 'counter(s)' mis-lexes as the OVERB, so the clause routes to
# osclause/tapuntap_subj where the tap/untap re-match abstains; a src re-match here reproduces
# card_effects._enters_counters_eff byte-for-byte. g1=optional subject (->self), g2=count, g3=kind.
_ENTERS_CTR_RE = re.compile(rf"^(?:({_TGT}) )?enters with (\w+) (?:additional )?([+-]\d+/[+-]\d+|[\w]+) counters? on it$", re.I)
# DISCARD whole-hand / referenced-set — `_discard_hand` ('discard your hand' -> discard/all/you) and
# `_discard_set` ('[<subj>] discards their hand|those cards|that card|all the cards in their hand' ->
# discard/-/<subj>/<slug>). pcount routes 'discard' here via PVERB but abstains (the body isn't 'N cards').
# Re-applied to src; reproduce each template byte-for-byte. `_discard_hand` (570) precedes `_discard_set`
# (690) in the chain, but their patterns are disjoint ('your hand' ∉ _discard_set's set), so order is moot.
_DH_RE = re.compile(r"^discard your hand$", re.I)
_DSET_RE = re.compile(r"^(?:(" + _BCM_TGT_SRC + r") )?discards? (their hand|those cards|that card|all the cards in their hand)$", re.I)
# 'discard all the cards in YOUR hand' — NOT in `_discard_set` (which only matches 'their hand'), so the regex
# grounds it via the generic object-verb leaf as discard(-, _target('all the cards in your hand')) — the whole
# phrase in the TARGET slot (vs _discard_set's 'their' form, which puts the slug in EXTRA). Reproduce that exactly.
_DAYH_RE = re.compile(r"^discards? all the cards in your hand$", re.I)
# DISCARD '[twice|half] that many cards [plus|minus N]' anaphoric amount — `_discard_that_many`'s exact pattern
# (amount via _that_amt; the trailing 'at random' rider is DROPPED, as the regex does). pcount routes discard
# here but dies on `_amount('that many')`=None. Re-applied to src; reproduces the tuple byte-for-byte.
_DTM_RE = re.compile(r"^(?:(" + _BCM_TGT_SRC + r") )?discards? (twice |half )?that many cards( plus \w+| minus \w+)?(?: at random)?$", re.I)
# the LOOT/rummage partitive 'discard <N> of them/those/these [cards]' (Krovikan Sorcerer, Soldevi Sage,
# Casting of Bones — 'draw N, then discard one of them'): the body is 'N of them', not 'N cards', so pcount's
# _NEEDS_CARD gate misses it. Count is faithful (you discard N cards); the partitive pool (the just-drawn
# cards) rides extra='of_them'. Re-applied to src in the discard block (the frame pattern, NOT a @_t template).
_DOFT_RE = re.compile(r"^(?:(" + _BCM_TGT_SRC + r") )?discards? (\w+) of (?:them|those|these)(?: cards?)?(?: at random)?$", re.I)
# '[then] <player> may have you draw N cards' — the §603 causative the regex grounds LOSSILY via `_draw_tgt`
# (subject='target_opponent_may_have_you', dropping that YOU are the drawer). This is a FAITHFUL IMPROVEMENT,
# not a byte-identical flip: ground the actual drawer ('you') + the directing player as extra='by_<player>'
# (mirrors deal_damage `_have_deal`'s extra='by_<src>'). Routes to the grant rule (GVERB 'have'); handled there.
_HAVE_DRAW_RE = re.compile(rf"^(?:then )?({_TGT}) may have you draws? (a card|\w+) cards?$", re.I)
# GAIN/LOSE life dynamic amounts — exact patterns of `_gain_foreach`/`_gain_life_equal`/`_that_gain`
# (gain routes to the grant rule) and `_lose_life_equal`/`_lose_half`/`_lose_that_much` (lose routes to
# pcount via the PVERB terminal). Re-applied to src; reproduce each template's amount slug byte-for-byte.
_GFE_RE = re.compile(r"^(?:(" + _BCM_TGT_SRC + r") )?gains? (\w+) life for each (.+?)$", re.I)
_GLE_RE = re.compile(r"^(?:(" + _BCM_TGT_SRC + r") )?gains? life equal to (.+?)$", re.I)
_GTM_RE = re.compile(r"^(?:(" + _BCM_TGT_SRC + r") )?gains? (twice |half )?that much life( plus \w+| minus \w+)?$", re.I)
_LLE_RE = re.compile(r"^(?:(" + _BCM_TGT_SRC + r") )?loses? life equal to (.+?)$", re.I)
_LHF_RE = re.compile(r"^(?:(" + _BCM_TGT_SRC + r") )?loses? half (?:your |their |his or her |its )?life(?:,? rounded (up|down))?$", re.I)
_LTM_RE = re.compile(r"^(?:(" + _BCM_TGT_SRC + r") )?loses? (twice |half )?that much life( plus \w+| minus \w+)?$", re.I)
# '<X> gains all creature types [until end of turn]' — the §205 changeling-style omni-type worded as 'gain'
# (`_gain_all_creature_types`). Routes to gclause (gain ∈ GVERB) but 'all creature types' isn't a §702 keyword,
# so the grant transformer abstains; reproduce the template's becomes(-, X, every_creature_type) from src here.
_GAIN_ALL_CT_RE = re.compile(r"^(" + _TGT + r") gains? all creature types(?: until end of turn)?$", re.I)
# PUT a number of <kind> counters on <obj> equal to <X> — `_put_counter_equal`'s exact pattern (count-scaled
# counters). Re-applied to src by putctr; the 'a number of' kind makes the span logic abstain, so own it here.
_PCE_RE = re.compile(r"^put a number of ([+-]\d+/[+-]\d+|[\w ]+?) counters? on (.+?) equal to (.+?)$", re.I)
# PUT X <kind> counters on <obj>, where X is <Y> — the regex `_put_counter` grounds this DOUBLY lossy
# (amount='X' AND the ', where X is …' crammed into the target slug). Ground amount='equal_to_<Y>' + a clean
# target (the create where-X recipe). g1=kind, g2=object, g3=Y. Optional non-capturing leading subject.
_PCWX_RE = re.compile(rf"^(?:{_TGT} )?puts? x ([+-]\d+/[+-]\d+|[\w' -]+?) counters? on (.+?),? where x is (.+?)$", re.I)
# PUT <n> <kind> counter(s) on <obj> for each <Y> — §107.3 count-scaled. The regex `_put_counter` grounds
# this lossy (amount=<n>, the ' for each <Y>' crammed into the target slug, 'on ~' dropped). Ground the
# count-scaled amount '<n>_per_<Y>' (the exact draw/gain_life for-each convention) + a CLEAN target.
# g1=count, g2=kind, g3=obj, g4=Y. Optional non-capturing leading subject.
_PCFE_RE = re.compile(rf"^(?:{_TGT} )?puts? (\w+) ([+-]\d+/[+-]\d+|[\w' -]+?) counters? on (.+?) for each (.+?)$", re.I)
# PUT that many <kind> counters on <obj> — count carried from a prior clause. The regex grounds the +1/+1
# case faithfully (amount='that_amount') but BOTCHES word kinds: 'that' is dropped and 'many <kind>' becomes
# the kind ('many_vitality') with amount='X'. Ground amount='that_amount' (the draw 'that many' convention)
# + a clean kind. g1=kind, g2=obj. Optional non-capturing leading subject.
_PCTM_RE = re.compile(rf"^(?:{_TGT} )?puts? that many ([+-]\d+/[+-]\d+|[\w' -]+?) counters? on (.+?)$", re.I)
# CREATE a number of <spec> tokens equal to <X> — `_create_equal`'s exact pattern (count-scaled tokens).
# Re-applied to src by `create` (the 'number of' spec otherwise makes it abstain).
_CEQ_RE = re.compile(r"^(?:you )?create a number of (.+?) tokens? equal to (.+?)$", re.I)
# CREATE X <spec> tokens[, with <kw>], where X is <Y> — the COUNT is the variable X (literal 'x' count),
# defined inline by the 'where X is <Y>' tail. The generic _create_token grounds it LOSSILY (amount='X',
# dropping the def); ground amount='equal_to_<Y>' instead (same convention as _create_equal). The literal
# 'x ' after the verb is what restricts this to a COUNT-X clause — 'creates an x/x … where x is …' (X in the
# P/T, count='an') does NOT match, so it stays on the regex. g1=creator, g2=spec, g3=Y.
_CWHEREX_RE = re.compile(rf"^(?:({_TGT}) )?creates? x (.+?) tokens?(?: with [^,]+)?,? where x is (.+?)$", re.I)
# CREATE [N] token(s) that's a copy of <X>[, except <mods>] — `_create_copy`'s exact pattern (§111/§707). The
# spec-less copy shape PARSE-FAILs the normal `tclause` (empty cspec), so `tcpclause` makes it parse and this
# re-match on src reproduces the tuple byte-for-byte: amount, extra='copy_of_<X>[_except_<mods>]', creator/cond.
_CCP_RE = re.compile(rf"^(?:({_TGT}) )?creates? (a|one|two|three|x|\w+) tokens? that(?:'s| are) (?:a )?cop(?:y|ies) of ({_TGT})(?:,? except (?:it has |they have |it's |they're )?(.+?))?$", re.I)
# CREATE [N] copy/copies of <X>[, except <mods>] — `_create_copy_of`'s exact pattern (elided 'token that's',
# §707). Re-matched on src by `create_copy_of` for a byte-identical tuple (extra='copy_of_<X>[_except_<mods>]').
_CCPOF_RE = re.compile(r"^create (a|one|two|three|x|\w+) cop(?:y|ies) of (.+?)(?:, except (.+?))?$", re.I)
# BECOMES <color> — `_becomes_color`'s exact pattern (LITERAL-color slice: 'the color of your choice' is
# omitted so it defers to the earlier-registered `_becomes_choice`). Re-applied to src by bccolor_v.
_BCC_RE = re.compile(r"^(" + _BCM_TGT_SRC + r") (?:becomes?|is|are) (white|blue|black|red|green|colorless|all colors|that color|the chosen color)(?: in addition to its other colors)?(?: until end of turn)?$", re.I)
# BECOMES a copy of <X> — `_becomes_copy`'s exact pattern (re-applied to src by bccopy_v).
_BCP_RE = re.compile(r"^(" + _BCM_TGT_SRC + r") becomes? a copy of (" + _BCM_TGT_SRC + r"|that card|the chosen card)(?:, except (.+?))?(?: until end of turn)?$", re.I)
# BECOMES the <X> of your choice — `_becomes_choice`'s exact pattern (re-applied to src by bcchoice_v).
_BCH_RE = re.compile(r"^(" + _BCM_TGT_SRC + r") becomes? the (.+?) of your choice(?: until end of turn)?$", re.I)
# BECOMES a/an <card-type> — `_becomes_type`'s exact pattern (closed card-type word list); re-applied to src.
_BCT_RE = re.compile(r"^(" + _BCM_TGT_SRC + r") (?:is|are|becomes?) an? ([\w' -]*?(?:artifact|enchantment|land|creature|planeswalker|aura|equipment|plains|island|swamp|mountain|forest)s?)(?: in addition to its other types)?(?: until end of turn| for as long as (.+?))?$", re.I)
# BECOMES a/an <color(s)> <type> (e.g. 'is a black Zombie') — `_becomes_color_type`'s exact pattern
# (color-anchored); re-applied to src by bctype_v as a fallback after _BCT_RE (matching the regex chain order).
_BCCT_RE = re.compile(r"^(" + _BCM_TGT_SRC + r") (?:is|are|becomes?) an? ((?:white|blue|black|red|green|colorless)(?: (?:and )?(?:white|blue|black|red|green|colorless))* [\w' -]+?)(?: in addition to its other (?:types and colors|colors and types|types|colors))?(?: until end of turn)?$", re.I)
# BECOMES a/an <X> in addition to (its|their) other [creature|land] types|colors -> added_<X> (`_type_add`).
from card_effects import _COPULA_RUNON as _BT_RUNON
_BTA_RE = re.compile(r"^(" + _BCM_TGT_SRC + r") (?:is|are|becomes?) an? ([\w' -]+?) in addition to (?:its|their) other (?:creature |land )?(?:types|colors)(?: until end of turn)?$", re.I)
# BECOMES every <kind> type -> every_<kind>_type (`_all_types`); and the ARTICLE-LESS plural type addition
# '<subj> are <Type>s in addition to their other types' -> added_<X> (`_type_add_plural`). Both re-applied to
# src by bctype_v (after _BCT_RE); _ALLT_RE precedes _TAP_RE to match the regex chain order (`_all_types` is
# registered before `_type_add_plural`, so 'is every creature type in addition …' grounds every_, not added_).
_ALLT_RE = re.compile(r"^(" + _BCM_TGT_SRC + r") (?:is|are|becomes?) every (creature|basic land|nonbasic land|land) type(?: in addition to (?:its|their) other types)?(?: until end of turn)?$", re.I)
# '<subj> becomes a/an <subtype> [until eot| for as long as …]' (§205 type SET to a permanent subtype) — net-new,
# gated by ground.permanent_subtypes in bctype_v (every type word must be a grounded subtype, else abstain).
_BCSUB_RE = re.compile(r"^(" + _BCM_TGT_SRC + r") (?:is|are|becomes?) an? ([\w' -]+?)(?: until end of turn| for as long as (.+?))?$", re.I)
_PERM_SUBTYPES = ground.permanent_subtypes()
_TAP_RE = re.compile(r"^(" + _BCM_TGT_SRC + r") (?:is|are|becomes?) ([\w' ,-]+?) in addition to (?:its|their) other (?:creature |land )?(?:types|colors)(?: until end of turn)?$", re.I)
# BASE-P/T-set family — `_becomes_base_pt` / `_base_pt_perpetual` / `_base_pt` exact patterns (re-applied
# to src by basept_v in that precedence order).
_BBPT_RE = re.compile(r"^(" + _BCM_TGT_SRC + r") (?:becomes?|is|are) an? ([\w' -]+?) with base power and toughness ([\dxX]+/[\dxX]+)(?: in addition to (?:its|their) other (?:colors and types|types and colors|creature types|types|colors))?(?: until end of turn| for as long as (.+?))?$", re.I)
_BPTP_RE = re.compile(r"^(" + _BCM_TGT_SRC + r") perpetually (?:has|have) base power and toughness ([\dxX]+/[\dxX]+)$", re.I)
_BPT_RE = re.compile(r"^(" + _BCM_TGT_SRC + r") (?:has|have|with) base power and toughness ([\dxX]+/[\dxX]+)(?: until end of turn| until your next (?:turn|upkeep)| until the end of your next upkeep)?$", re.I)
# `_type_also` + `_becomes_chosen` exact patterns (re-applied to src).
_TAO_RE = re.compile(r"^(" + _BCM_TGT_SRC + r") (?:is|are) also an? ([\w' ,-]+?)(?: in addition to its other types)?(?: until end of turn)?$", re.I)
_BCHN_RE = re.compile(r"^(" + _BCM_TGT_SRC + r") (?:is|are|becomes?) the chosen (color|type)(?: in addition to its other (?:types|colors))?(?: until end of turn)?$", re.I)


def _bcm_g3(tail: str):
    n = len(tail)
    for i in range(0, n + 1):
        if i > 0 and not _BCM_G3CHAR.match(tail[i - 1]):
            break                              # g3 (`[\w' -]*`) can't include this char -> stop
        if _BCM_REM.match(tail[i:]):
            return tail[:i]
    return None


# 'gain control of <X>' (§720 control-change) is owned by the grant rule's gc-branch (gkw starts
# with 'control'). The regex templates `_control`/`_control_subj` recognise EXACTLY two trailing
# durations — ' until end of turn' and ' for as long as …' (the latter slurps to end) — and leave
# every OTHER duration ('until your next turn', 'this turn', 'during their next turn') inside the
# object span. `_gc_dursplit` reproduces that split byte-for-byte: it folds any lark-lexed MDUR back
# into the object first (only the two recognised forms are peeled), so the slugged object/target and
# the duration extra are identical to the regex.
def _gc_dursplit(obj_full: str):
    """(object_text, duration_extra) for a 'control of <object_full>' span, faithful to the regex."""
    s = obj_full.strip()
    i = s.find(" for as long as ")
    if i >= 0:
        return s[:i].strip(), ground.slug(s[i:])          # ' for as long as …' -> slug to end
    if s.endswith(" until end of turn"):
        return s[:-len(" until end of turn")].strip(), "until_end_of_turn"
    return s, "-"


# the regex `_control`/`_control_subj` only ground when the controlled OBJECT (and, for the subject
# form, the gaining player) match the `_TGT` noun-phrase pattern anchored to the span — a comma, a
# trailing clause ('… instead if you control …'), an anaphor-name ('Starke'), or a run-on coordinated
# imperative ('untap target creature and gain control of it' — the regex leaf grounds that as UNTAP,
# an earlier template) all FAIL `_TGT`. We validate the raw span against the same pattern so lark
# grounds gain_control on EXACTLY the clauses the regex does, and abstains everywhere else.
_GC_TGT = re.compile(r"^(?:" + _BCM_TGT_SRC + r")$", re.I)


# CHOOSE family. The EXACT quantifier set the regex `_choose` alternation `(a|an|one|two|three|up to
# \w+|one or more|any number of|another|target|the)` SELECTS, minus alternatives it never reaches
# ('one or more' is shadowed by 'one') and minus 'the'/'up to <non-numeric>' (rare / split-divergent).
_CHS_UPTO = {"one", "two", "three", "four", "five", "x"}   # 'up to <N>' the shared QUANT can also yield
_CHS_QUANTS = ({"a", "an", "one", "two", "three", "another", "target", "any number of"}
               | {"up to " + n for n in _CHS_UPTO})
# 'choose <obj>' is grounded by TWO regex templates in PRECEDENCE order: the generic `_verb_target`
# (`^(\w+) (<_TGT>)$`) fires FIRST whenever the FULL object is a `_TGT` noun phrase, slugging it WHOLE
# (article kept) via `_target`; only otherwise does `_choose` fire, folding the article (a/an/one -> "").
# So an object that is itself a `_TGT` keeps its leading 'a/an/one'; one that isn't drops it. Mirror that.
_CHS_TGT = re.compile(r"(?i)^(?:" + _TGT + r")$")


# REVEAL frame regexes — the EXACT fixed frames of the regex templates this rule replaces. The lark rule
# only certifies the clause starts with 'reveal[s]' (and optionally a player subject); the faithful body
# parse is these frames, so the grounded tuple is byte-identical to the regex templates. The structured
# 'top N cards of <owner> library' / 'their hand' frames are tried first; the open-ended GENERIC object
# slugs (`_reveal_among`/`_reveal_generic` imperative, `_subject_obj_verb` subject-form) follow, each
# reproducing the template's exact slug/placement (object in TARGET='you' for imperative; object in
# TARGET + by_<player> in EXTRA for an explicit subject). A compound run-on object abstains -> regex.
_RV_TOP = re.compile(r"^the top (?:(\w+) )?cards? of ([\w' ]+?) librar(?:y|ies)$", re.I)          # _reveal_top
_RV_SUBJ_TOP = re.compile(r"^the top (?:(\w+) )?cards? of (?:their|its owner's|your) library$", re.I)  # _subject_reveal_top body
_RV_SUBJ_HAND = re.compile(r"^their hand$", re.I)                                                  # _reveal_hand body
_RV_AMONG = re.compile(r"^(?:a|an|one|up to \w+) ([\w ]+?) from among them$", re.I)                # _reveal_among body
# `_verb_target` (`^(\w+) (<_TGT>)$`) is registered BEFORE `_reveal_among`/`_reveal_generic`, so an
# imperative 'reveal <obj>' whose obj is a clean `_TGT` ('reveal it', 'reveal that card', 'reveal each of
# those cards') grounds there FIRST as reveal(-, _target(obj)) — target-form, NOT the generic you/EXTRA
# form. We must try this _TGT shape ahead of the generic slug to stay byte-identical to the regex chain.
_RV_VT = re.compile(rf"^({_TGT})$", re.I)                                                          # _verb_target obj

# PREVENT_DAMAGE operand validators — the `_fog`/`_prevent` templates split at the structural DMG
# ('damage') terminal the GRAMMAR now owns into a leading `pvpre` span and a trailing `pvtail` span, each
# certified by an anchored per-operand validator. `_PV_FOG_PRE`/`_PV_FOG_TAIL` are the `_fog` skeleton
# ('all [combat]' + 'that would be dealt this turn'); `_PV_FOG_PRE`'s group is the optional 'combat'.
# `_PV_NEXT_PRE`/`_PV_NEXT_TAIL` are the `_prevent` skeleton ('the next <count>' + 'that would be dealt
# [this turn] to <target> [this turn]'); `_PV_NEXT_PRE`'s group is the count, `_PV_NEXT_TAIL`'s group is
# the target span (the template's `(.+)`, with the leading/trailing 'this turn' handling preserved in the
# transformer). The variable-scope `_prevent_all_scoped`, the consequent `_prevent_that`, and the
# `_prevent_next_source` shield don't fit these skeletons -> abstain (the regex fallback owns them). No
# whole-clause frame regex; the FOG-vs-NEXT precedence is the disjoint pre validators ('all' vs 'the next').
_PV_FOG_PRE = re.compile(r"^all( combat)?$", re.I)                          # _fog pre: 'all [combat]'
_PV_FOG_TAIL = re.compile(r"^that would be dealt this turn$", re.I)         # _fog tail
_PV_NEXT_PRE = re.compile(r"^the next (\w+)$", re.I)                        # _prevent pre: 'the next <count>'
_PV_NEXT_TAIL = re.compile(r"^that would be dealt (?:this turn )?to (.+)$", re.I)  # _prevent tail: '… to <target>'

# SCOPED / SOURCE prevention (the `_prevent_all_scoped` PASSIVE frame + `_prevent_all_source` ACTIVE
# frame): 'prevent all [combat|noncombat] damage <rider>'. `_PV_ALL_KIND` certifies the pre span and
# captures the optional combat/noncombat kind; `_PV_SCOPED_TAIL` is the passive rider ('that would be
# dealt <scope>'); `_PV_SOURCE_TAIL` is the active rider ('[that ]<source> would deal [to <recip>]
# [this turn|this combat]') — its source group reuses the regex's restricted `[\w'~ -]+?` charset so we
# abstain on exactly the spans (commas, slashes, braces) the regex template can't capture either.
_PV_ALL_KIND = re.compile(r"^all(?: (combat|noncombat))?$", re.I)
_PV_SCOPED_TAIL = re.compile(r"^that would be dealt (.+)$", re.I)
_PV_SOURCE_TAIL = re.compile(r"^(?:that )?([\w'~ -]+?) would deal( to .+?)?( this turn| this combat)?$", re.I)


def _pv_scope(kind: str, scope: str) -> "Effect":
    """Build the `prevent_damage / all / - / <scope_slug>` tuple for a scoped/source prevention clause —
    the EXACT `card_effects._prevent_scope`: prefix the optional kind, map '~'->'self' (so the bare
    self-reference survives slugging), and record the whole rider as a faithful descriptive slug."""
    scope = ((kind + " ") if kind else "") + scope.strip()
    scope = scope.replace("~", "self")
    return Effect("prevent_damage", "all", "-", ground.slug(scope))

# REDIRECT_DAMAGE operand validators — certify the rdpre/rda/rdb spans the GRAMMAR carved at the DMG +
# RDIS terminals, REUSING the `_redirect`/`_redirect_all` templates' own `_TGT` so the greedy A/rider split
# is byte-identical: `_RD_A_NEXT` is `_redirect`'s 'to <A> this turn' (this-turn REQUIRED); `_RD_A_ALL` is
# `_redirect_all`'s 'to <A>(?: this turn| by <src>)?' (the optional this-turn-or-by rider DROPPED, _TGT
# greedy as in the regex); `_RD_B` is the shared recipient 'to <B>(?: instead)?'. A span outside these
# abstains to the regex fallback.
_RD_PRE_NEXT = re.compile(r"^the next (\w+)$", re.I)
_RD_PRE_ALL = re.compile(r"^all( combat)?$", re.I)
_RD_A_NEXT = re.compile(r"^that would be dealt to (" + _TGT + r") this turn$", re.I)
_RD_A_ALL = re.compile(r"^that would be dealt to (" + _TGT + r")(?: this turn| by [\w' -]+?)?$", re.I)
_RD_B = re.compile(r"^(" + _TGT + r")(?: instead)?$", re.I)

# SKIP body validator — the `_skip` template's '(your|its|their|his or her) [next] <phase>' tail, applied
# to the captured `skbody` span (the phase slug is group 1). AMASS count validator — the `_amass`
# template's own `(\d+|one|two|three|x)` set, so a count outside it (e.g. 'four') abstains to the regex.
_SK_BODY = re.compile(r"^(?:your|its|their|his or her|that|this) (?:next )?([\w ]+? (?:steps?|phases?)|turns?)$", re.I)  # +that/this ('skips that turn' — Stranglehold) + plural step/phase/turn ('skip their upkeep steps' — Eon Hub)
_AS_NUM = re.compile(r"^(?:\d+|one|two|three|x)$", re.I)
_MS_NUM = re.compile(r"^(?:\d+|one|two|three|four|five|x)$", re.I)   # monstrosity count (the `_kwaction_n` set)
_ENDURE_RE = re.compile(rf"^(?:({_TGT}) )?endures? (\w+)$", re.I)    # `_endure`'s exact pattern (re-applied by endure_v)

# MUST_ATTACK / MUST_BLOCK body validators — the `_must_attack` / `_must_block_tgt` / `_must_block_able`
# template patterns MINUS the trailing ' if able' (the grammar's MRABLE terminal already consumed it),
# applied to the captured `mrbody` span and reusing the templates' own `_TGT`. ATTACK: subject + optional
# directed player (the negative lookahead keeps a bare duration out of the object slot) + optional
# duration. BLOCK_TGT: subject + blocked object + optional duration. BLOCK_ABLE: subject + optional
# duration. A non-`_TGT` (lossy/compound) subject matches none -> abstain to the regex.
_MR_ATTACK = re.compile(r"^(" + _TGT + r") attacks?(?: (?!each combat|this turn|this combat)(" + _TGT + r"))?(?: each combat| this turn| this combat)?$", re.I)
_MR_BLOCK_TGT = re.compile(r"^(" + _TGT + r") blocks (" + _TGT + r")(?: this turn| this combat)?$", re.I)
# bare-form (no object): 'blocks?' so a PLURAL subject 'they block … if able' grounds (the singular 'blocks'
# missed it). Kept off _MR_BLOCK_TGT: 'block' there would let a causative 'have <X> block <Y>' match with a
# garbled 'have <X>' subject; with an object the bare form can't match, so the causative still abstains.
_MR_BLOCK_ABLE = re.compile(r"^(" + _TGT + r") blocks?(?: this turn| this combat| each combat)?$", re.I)
# combined '<subj> attacks or blocks [each combat] if able' (§508/§509) — the must-do mirror of the existing
# cant_attack_or_block; ATTACK/BLOCK singly already ground, this is the disjunction (Khârn the Betrayer, …).
_MR_ATTACK_OR_BLOCK = re.compile(r"^(" + _TGT + r") attacks? or blocks?(?: each combat| this turn| this combat)?$", re.I)
# PASSIVE '<X> must be blocked [this turn|this combat]' (`_must_be_blocked`, the 'if able' MRABLE already
# stripped) — the §509 lure-like requirement. -> must_be_blocked(-, X). A non-`_TGT` subject (a run-on 'gains …
# and must be blocked') matches none of these -> abstain to the regex.
_MR_MUST_BE_BLOCKED = re.compile(r"^(" + _TGT + r") must be blocked(?: this turn| this combat)?$", re.I)
# '<X> must attack/block [dur]' — the 'must <verb>' phrasing of the §508/§509 requirement (`_must_attack_block`,
# the 'if able' MRABLE already stripped by the grammar). -> must_attack/must_block(-, X). Disjoint from the bare
# 'attacks'/'blocks' forms above (those have no 'must'); a non-`_TGT` subject abstains to the regex. NOTE: only
# the 'if able' variants reach here (mrclause is anchored on MRABLE); a bare 'X must attack' with NO 'if able'
# still falls to `_must_attack_block` — so this REDUCES the template's abstains but doesn't yet retire it.
_MR_MUST_ATTACK_BLOCK = re.compile(r"^(" + _TGT + r") must (attack|block)(?: each combat| this turn| this combat)?$", re.I)

# GRANT_COMBAT (can attack/block …) — the clean §509/§508 combat-PERMISSION subfamily of grant_ability,
# the EXACT mirror of two card_effects templates re-applied to the captured clause (the GCC_CAN 'can
# attack'/'can block' terminal anchors the production; the transformer slices `self._src` so the slug is
# byte-identical — never a re-joined approximation):
#   _as_though_combat: `^(_TGT) can ((?:attack|block)\b[\w' -]*? as though (?:it|they) (?:had|didn't have|
#       don't have) [\w' -]+?)$` -> grant_ability('-', _target(subj), 'can_' + slug(<attack/block…asthough…>)).
#       The whole 'attack/block … as though …' span is slugged WHOLE (`$`-anchored after the as-though tail)
#       — NO trailing-duration strip (a mid-phrase 'this turn' is KEPT, e.g. 'can attack this turn as though…').
#   _can_block_more: `^(?:(_TGT) )?can block (an additional creature|any number of creatures|up to \w+
#       additional creatures|an additional \w+ creatures?)(?: this turn| each combat)?$` -> grant_ability(
#       '-', _target(subj or 'self'), 'can_block_' + slug(<count>)). The trailing ' this turn'/' each combat'
#       is OUTSIDE the captured count group -> DROPPED. The subject is optional (-> 'self' when absent).
# A clause that matches NEITHER regex (a quoted-ability grant, an ' and '-joined compound, a 'can't …'
# restriction, a non-`_TGT` subject) finds no clean re-application here -> abstain to the regex (the
# `_grant_ability` quoted template / the compound splitters own it). We additionally hard-guard on a quote
# char and `_is_compound_object` so a quoted/compound clause never grounds even if a regex were to skim it.
_GCC_ASTHOUGH = re.compile(rf"^({_TGT}) can ((?:attack|block)\b[\w' -]*? as though (?:it|they) (?:had|didn't have|don't have) [\w' -]+?)$", re.I)
_GCC_BLOCKMORE = re.compile(rf"^(?:({_TGT}) )?can block (an additional creature|any number of creatures|up to \w+ additional creatures|an additional \w+ creatures?)(?: this turn| each combat)?$", re.I)


# DISTRIBUTE counters — `_distribute_counters`'s exact pattern, re-applied to src by distribute_v.
_DC_RE = re.compile(r"^distribute (\w+) ([+-]\d+/[+-]\d+|[\w ]+?) counters? among (.+?)$", re.I)
# MOVE/relocate existing counters (§122) — `_move_counters` ('put <its|all|all of its> counters on <tgt>') and
# `_move_counter_from` ('move <N> <kind> counters from <src> onto|to <tgt>') exact patterns, re-applied to src.
_MC_RE = re.compile(rf"^put (its|all|all of its) counters on ({_TGT})$", re.I)
_MCF_RE = re.compile(rf"^move (a|an|one|two|three|x|\w+) ([+-]\d+/[+-]\d+|[\w ]+?) counters? from ({_TGT}) (?:onto|to) ({_TGT})$", re.I)
# §509 combat-requirement — `_lure` ('all creatures able to block <X> [dur] do so' -> lure(-, X)) exact pattern,
# re-applied to src by lure_v. (extra_combat is a constant-tuple whole-phrase terminal, so it needs no re-apply
# regex; '<X> must be blocked … if able' is handled in the mustreq transformer via the body-level _MR_MUST_BE_BLOCKED.)
_LURE_RE = re.compile(r"^all creatures? able to block ({0}) (?:this turn |this combat )?do so$".format(_TGT), re.I)
# §720 STATIC 'you control <X>' (no 'gain') — the bare-control branch of `_control` that lark's grant gc-branch
# (GVERB-routed) misses. The 'you control enchanted <type>' aura form dominates. Re-applied to src by you_control_v.
_CTRL_RE = re.compile(rf"^(?:you )?(?:gain )?control (?:of )?({_TGT})( until end of turn| for as long as .+?)?$", re.I)


# REMOVE_COUNTER operand validator — the anchored `_TGT` noun-phrase (mirrors `_DB_TGT`/`_AT_TGT`). The
# GRAMMAR now owns the `remove <count> [<kind>] counter[s] from <tgt>` skeleton as distinct spans (the
# structural COUNTER/FROM terminals anchor it); this regex CERTIFIES the TARGET span is a clean `_TGT`
# exactly as the `_remove_counter` template's group 3 required. A target outside `_TGT` ('… from combat',
# a non-noun-phrase) abstains -> the regex fallback owns the whole clause (byte-identical-or-abstain). No
# structural whole-clause frame regex.
_RC_TGT = re.compile(r"^(?:" + _TGT + r")$", re.I)

# DOUBLE — the generic object-verb leaf grounds 'double <object>' as double(-, slug(<object>)). The
# regex precedence is `_verb_target` (`^(\w+) (<_TGT>)$`, slugs the WHOLE object via _target, article
# kept) FIRST, else `_generic_object_verb` (slug). They coincide on every corpus 'double' clause, but we
# mirror the precedence exactly to stay identical-or-abstain. The guards are `_generic_object_verb`'s:
# abstain on a compound/run-on object or an `_OBJ_BAD` structural marker (the regex object-verb leaf
# can't ground those either — faithful-or-abstain).
_DB_OBJ_BAD = re.compile(r"[:;]|\bequal to\b|\bfor each\b|\bunless\b|\bwhere\b|\bif\b", re.I)
_DB_TGT = re.compile(r"^(?:" + _TGT + r")$", re.I)

# ADD_MANA operand validator — the anchored `_TGT` noun-phrase (mirrors `_DB_TGT`/`_AT_TGT`/`_TF_TGT`).
# The GRAMMAR owns the shape (`amlead? AM_ADD AM_ADDL? amrest`); the transformer validates the optional
# subject span `amlead` with this regex (exactly the old `_AM_FRAME`'s `(_TGT)?` subject group — a clean
# `_TGT` or abstain) and feeds the `amrest` mana-spec span to `_mana_production`. `parse_clause_lark`
# LOWERCASES the clause, but `_mana_production` looks up mana symbols in the §107.4 colour table by their
# UPPERCASE glyph ('{G}', not '{g}') — so `_am_upper_syms` re-uppercases ONLY the inside of each '{…}'
# (English phrases stay lowercase, which `_mana_production` matches case-insensitively). The grounded
# tuple is byte-identical to the old `_add_mana`/`_AM_FRAME` output (amount = len(prod), target =
# _target(subj or 'you'), extra = dedup-joined colours) — or None (abstain) when `_mana_production`
# rejects the spec or `amlead` isn't a clean `_TGT`. No whole-clause re-parse regex.
_AM_TGT = re.compile(r"^(?:" + _TGT + r")$", re.I)
_AM_SYM = re.compile(r"\{[^}]*\}")


def _am_upper_syms(s: str) -> str:
    return _AM_SYM.sub(lambda m: m.group(0).upper(), s)


# ATTACH operand validator — the anchored `_TGT` noun-phrase (mirrors `_DB_TGT`/`_TF_TGT`). The GRAMMAR
# owns the split shape (`atsrc TOPREP atdest`); the transformer rejoins the body and picks the first
# viable ' to ', and this regex CERTIFIES each half is a clean `_TGT` exactly as the `_attach` template's
# groups required (source `(~|it|_TGT)` ⊆ `_TGT`, since `_TGT` already includes `~`/`it`; destination
# `_TGT`). A half outside `_TGT` ('… to Sokka') or no viable split ('…attached to a creature to another
# creature') abstains -> the whole-object `_generic_object_verb` form to the regex. No structural regex.
_AT_TGT = re.compile(r"^(?:" + _TGT + r")$", re.I)

# RETURN_TO_HAND (rhclause). `_RH_SUBJ` is the optional leading PLAYER subject ('<player> returns <obj> …'
# — the `_return_zone` subject-first shape), the family-shared closed `_PLAYER` allow-list; it is DROPPED
# exactly as `_return_zone`'s `(?:_TGT )?` prefix drops it. The object itself is gated ONLY by
# `_is_compound_object` (no `_TGT` requirement): `_bounce` requires a `_TGT`, but `_return_zone` (which
# runs after `_bounce` and produces the SAME tuple) accepts any non-compound object via its `(.+?)` —
# so a non-`_TGT` clean object like 'a card' / a card name still grounds, faithful to `_return_zone`.
# `_RH_FROM` splits a trailing 'from <…> graveyard/battlefield/exile/hand/library' SOURCE off the object
# (object stops at 'from'), byte-identical to `_bounce`/`_return_zone` (here including 'battlefield' as
# `_bounce` does, so a 'from a graveyard'/'from the battlefield' source folds to from_<zone>).
_RH_SUBJ = _PLAYER
_RH_FROM = re.compile(r"\bfrom [\w' ]+? (graveyard|battlefield|exile|hand|library)$", re.I)

# TRANSFORM — the exact mirror of DOUBLE. The generic object-verb leaf grounds 'transform <object>' as
# transform(-, slug(<object>)); precedence is `_verb_target` (`^(\w+) (<_TGT>)$`, _target, article kept)
# FIRST, else `_generic_object_verb` (slug). They coincide on every corpus clause; we mirror the
# precedence (whole-`_TGT` object -> _target; else plain slug) and apply the same guards.
_TF_OBJ_BAD = re.compile(r"[:;]|\bequal to\b|\bfor each\b|\bunless\b|\bwhere\b|\bif\b", re.I)
_TF_TGT = re.compile(r"^(?:" + _TGT + r")$", re.I)

# EXILE shapes (exiletop / exileuntil). The GRAMMAR carves the clause (EXILE … XLIB / EXILE … XLEAVES);
# each transformer RE-APPLIES the template's own anchored regex to `self._src` so the grounded tuple is
# BYTE-IDENTICAL, never a re-joined span approximation. `_XL_TOP` is `_exile_top` verbatim (N + owner ->
# 'top_of_library' for 'your', else 'top_of_'+slug(owner)+'_library'); `_XL_UNTIL` is `_exile_until`
# (object certified by the shared `_TGT`, extra 'until_self_leaves'). A compound/run-on object abstains.
_XL_TOP = re.compile(r"^exile the top (?:(\w+) )?cards? of ([\w' ]+?) librar(?:y|ies)$", re.I)
_XL_UNTIL = re.compile(rf"^exile ({_TGT}) until ~ leaves the battlefield$", re.I)


# SUBJECT-FIRST object verbs. `_SF_PLAYER` is the closed player allow-list (reuse the family-shared
# `_PLAYER`); `_SF_SAC_NAMED` is `_sacrifice_subj` #1's exact object alternation `(it|that \w+|them|those
# \w+)`, which (because it is registered BEFORE the count form #2) takes precedence on those four shapes.
_SF_PLAYER = _PLAYER
_SF_SAC_NAMED = re.compile(r"^(?:it|that \w+|them|those \w+)$", re.I)
# `_sacrifice_subj` #2's count head — a SINGLE word `(a|an|one|two|three|\w+)` followed by `(.+)` (so the
# object must have >=2 words). The head is `\w+`, i.e. a bare word with no spaces/hyphens; '\w' excludes
# '-', so a hyphenated first token ('non-Vampire …') makes #2 FAIL its `(a|an|one|two|three|\w+) ` split
# at that boundary and the regex would re-split — we reproduce that boundary exactly (faithful-or-abstain).
_SF_WORD = re.compile(r"^\w+$")
# A '… and <3rd-person verb> …' run-on tacks a SECOND clause onto the count form's object ('… of their
# choice and gets a poison counter', '… and loses 4 life'). The regex count form #2 has NO compound guard
# and would slug the whole run-on, BUT an EARLIER template (e.g. `_put_counter` on the 'gets a … counter'
# tail) often intercepts the full sentence and grounds a DIFFERENT verb — so our sacrifice tuple would
# DIFFER from `parse_effect`. `_is_compound_object` misses these because the second verb's SURFACE form
# ('gets'/'loses') isn't a grounded-verb lemma. Abstain on any '… and <verb>s …' continuation (the regex
# fallback owns whatever the full chain makes of it). A type-union object ('artifact creature and a
# nonartifact creature') is NOT matched here (the word after 'and' is an article/noun, not a verb).
_SF_AND_VERB = re.compile(
    r"\b(?:and|then) (?:gets?|loses?|draws?|discards?|gains?|puts?|exiles?|sacrifices?|creates?|"
    r"mills?|takes?|adds?|deals?|reveals?|shuffles?|searches?|chooses?|returns?|destroys?|taps?|"
    r"untaps?|removes?|has|have|may|must|can|will)\b", re.I)


# PUT-TO-ZONE frame regexes — the EXACT fixed-frame patterns of the regex templates this family
# replaces (`card_effects._put_zone`/`_put_bottom`/`_put_library_position`/`_put_bottom_tgt`/
# `_put_top_tgt`/`_put_cards_library`). The lark rules certify the clause begins with 'put' (or ends
# 'into <owner> hand'); the faithful body parse is these frames applied IN TEMPLATE PRECEDENCE ORDER,
# so the grounded tuple is byte-identical to parse_effect. Anything outside these frames is left to
# the regex (abstain) — faithful-or-abstain. Only the put_on_top/put_on_bottom/put_in_hand verbs are
# emitted here; the put_in_graveyard branch of `_put_zone` belongs to another family -> abstain.
_PZ_ZONE = re.compile(r"^(?:put )?((?:(?! into )(?! and ).)+?) into (?:your|its owner's|their) (hand|graveyard)$", re.I)  # _put_zone
_PZ_BOTTOM = re.compile(r"^put (.+?) on the bottom(?: of your library)?(?: in (?:a |any )?(?:random )?order)?$", re.I)   # _put_bottom
_PZ_LIBPOS = re.compile(rf"^put ({_TGT}) into (?:its owner's|their owner's|your) library (\w+) from the top$", re.I)      # _put_library_position
_PZ_BOTTOM_TGT = re.compile(rf"^put ({_TGT}) on the bottom of {_LIB_OWNER} library$", re.I)                              # _put_bottom_tgt
_PZ_TOP_TGT = re.compile(rf"^put ({_TGT}) on top(?: of {_LIB_OWNER} library)?(?: in any order)?$", re.I)                 # _put_top_tgt
_PZ_CARDS_LIB = re.compile(rf"^put (.+?)(?: from your hand)? on (top|the bottom) of {_LIB_OWNER} (?:libraries|library)(?: in (?:any|a random) order)?$", re.I)  # _put_cards_library
_PZ_PUTS = re.compile(r"\bputs?\b", re.I)   # `_put_zone`'s declarative guard ('<subject> puts …' is _subject_puts' job)
# PRECEDENCE: the regex `_to_hand` (`^put (<_TGT>) into your hand$` -> return_to_hand, defined EARLIER)
# fires BEFORE `_put_zone` (-> put_in_hand). So a clean `_TGT` 'put <X> into your hand' is return_to_hand,
# not put_in_hand. Reproduce that ordering (the put-to-zone agent's frame missed it).
_PZ_TO_HAND = re.compile(r"^put (" + _TGT + r") into your hand$", re.I)


def _pz_frame(full: str):
    """Apply the put-to-zone frame regexes in TEMPLATE PRECEDENCE ORDER to a full (lowercased) clause,
    returning the first grounded Effect (byte-identical to parse_effect) or None (abstain). Only the
    put_on_top/put_on_bottom/put_in_hand verbs of this family are emitted."""
    m = _PZ_TO_HAND.match(full)                      # 0. _to_hand (EARLIER template) — wins over _put_zone
    if m:
        return Effect("return_to_hand", "-", _target(m.group(1)))
    m = _PZ_ZONE.match(full)                         # 1. _put_zone (hand only; graveyard -> other family)
    if m and not _PZ_PUTS.search(m.group(1)):
        if m.group(2).lower() == "hand":
            return Effect("put_in_hand", "-", "you", ground.slug(m.group(1)))
        return None                                  # 'into … graveyard' -> put_in_graveyard (abstain)
    m = _PZ_BOTTOM.match(full)                        # 2. _put_bottom
    if m:
        return Effect("put_on_bottom", "-", "library", ground.slug(m.group(1)))
    m = _PZ_LIBPOS.match(full)                        # 3. _put_library_position
    if m:
        return Effect("put_on_top", "-", _target(m.group(1)), m.group(2).lower() + "_from_top")
    m = _PZ_BOTTOM_TGT.match(full)                    # 4. _put_bottom_tgt
    if m:
        return Effect("put_on_bottom", "-", _target(m.group(1)))
    m = _PZ_TOP_TGT.match(full)                       # 5. _put_top_tgt
    if m:
        return Effect("put_on_top", "-", _target(m.group(1)))
    m = _PZ_CARDS_LIB.match(full)                     # 6. _put_cards_library (compound-object guarded)
    if m:
        if _is_compound_object(m.group(1)):
            return None
        return Effect("put_on_top" if m.group(2).lower() == "top" else "put_on_bottom", "-", ground.slug(m.group(1)))
    return None


# LOOK operand validators — the `_look_at`/`_look_top`/`_look_that_many` templates split at the structural
# LK_LOOK ('look[s]') terminal the GRAMMAR owns (`lksubj? LK_LOOK lkbody`) into a SUBJECT span (`lksubj`,
# the templates' optional leading `_TGT`) and a BODY span (`lkbody`, the templates' post-verb 'at …'
# portion). Each template becomes anchored per-operand certifiers: an optional SUBJECT validator (`_TGT`)
# and a BODY validator that captures the top-count and the owner. The transformer reads the spans off the
# tree and applies the three in TEMPLATE PRECEDENCE ORDER (`_look_at` FIRST, then `_look_top`, then
# `_look_that_many`) — byte-identical to parse_effect, or abstain (an open-ended 'look at <object>' shape
# the templates have no frame for fails all three -> the regex fallback owns it). `_look_at`'s owner is a
# clean `_TGT` (or the literals 'their'/'his or her'), so 'your library' fails it and falls to `_look_top`
# (whose owner is fixed 'your'), exactly the template precedence. No whole-clause frame regex.
_LK_AT_BODY = re.compile(                                                       # _look_at post-"look[s]" portion
    r"^at (?:the top (?:(\w+) )?cards? of )?"
    r"(" + _TGT + r"|their|his or her)(?:'s)? (?:hand|library)$", re.I)
_LK_TOP_BODY = re.compile(r"^at the top (?:(\w+) )?cards? of your library$", re.I)            # _look_top
_LK_THATMANY_BODY = re.compile(r"^at that many cards from the top of your library$", re.I)     # _look_that_many
_LK_SUBJ_TGT = re.compile(r"^(?:" + _TGT + r")$", re.I)                         # _look_at `_TGT` subject (g1)


def _lk_frame(subj, body):
    """Apply the look templates in PRECEDENCE ORDER to the SUBJECT and BODY spans read off the parse tree,
    returning the first grounded Effect (byte-identical to parse_effect) or None (abstain). `subj` is the
    optional leading `_TGT` (None if absent); `body` is the post-verb 'at …' span."""
    if body is None:
        return None
    if subj is None or _LK_SUBJ_TGT.match(subj):     # 1. _look_at (subject? + optional top-N + owner hand/library)
        m = _LK_AT_BODY.match(body)
        if m:
            n = _amount(m.group(1)) if m.group(1) else 1
            owner = "their" if m.group(2).lower() in ("their", "his or her") else _target(m.group(2))
            return Effect("look", n if n is not None else 1, owner,
                          "by_" + _target(subj) if subj else "-")
    if subj is None:                                  # 2./3. _look_top / _look_that_many (no subject form)
        m = _LK_TOP_BODY.match(body)                  # 2. _look_top ('look at the top N cards of your library')
        if m:
            n = _amount(m.group(1)) if m.group(1) else 1
            return Effect("look", n, "top_of_library") if n is not None else None
        if _LK_THATMANY_BODY.match(body):             # 3. _look_that_many
            return Effect("look", "that_amount", "top_of_library")
    return None


# SHUFFLE operand validators — the `_shuffle`/`_shuffle_subj` templates split at the structural
# SH_SHUFFLE ('shuffle[s]') terminal the GRAMMAR owns (`shsubj? SH_SHUFFLE shbody?`) into a SUBJECT span
# (`shsubj`, the templates' optional leading `_TGT`) and a BODY span (`shbody`, the templates' post-verb
# portion). Each template becomes anchored per-operand certifiers: a SUBJECT validator (`_TGT`) and a
# BODY validator that captures the source object. The transformer reads the spans off the tree and
# applies the pair in REGISTRATION ORDER (`_shuffle` FIRST, then `_shuffle_subj`) — byte-identical to
# parse_effect, or abstain. `_shuffle` is registered FIRST and its '… into <your|their|its owner's|their
# owner's> library' BODY branch swallows most subject-source clauses (extra='-'); `_shuffle_subj`
# (extra='from_<source>') fires ONLY for the 'his or her library' destination `_shuffle`'s body doesn't
# list (so its body branch leaves text unconsumed -> `_SH_BODY` fails -> fall through). No whole-clause
# frame regex — only anchored single-span certifiers.
_SH_BODY = re.compile(                                                          # _shuffle post-"shuffle" portion
    r"^(?:(?:your|their|his or her) library"
    r"|(it|them|.+?) into (?:your|their|its owner's|their owner's) library)$", re.I)
_SH_SUBJ_TGT = re.compile(r"^(?:" + _TGT + r")$", re.I)                         # _shuffle / _shuffle_subj `_TGT` subject
_SH_SUBJ_BODY = re.compile(                                                     # _shuffle_subj post-"shuffles" portion
    r"^(?:their|its owner's|his or her) ([\w ]+?) "
    r"into (?:their|its owner's|his or her) library$", re.I)


def _sh_frame(subj, body):
    """Apply the shuffle templates in REGISTRATION ORDER to the SUBJECT and BODY spans read off the parse
    tree, returning the first grounded Effect (byte-identical to parse_effect) or None (abstain). `subj` is
    the optional leading `_TGT` (None if absent); `body` is the post-verb span (None if a bare 'shuffle')."""
    if subj is None or _SH_SUBJ_TGT.match(subj):                  # 1. _shuffle (subject `_TGT` or absent)
        if body is None:                                          # bare 'shuffle' -> no source object
            return Effect("shuffle", "-", _target(subj or "you"), "-")
        m = _SH_BODY.match(body)
        if m:
            obj = re.sub(r"^(?:your|their|his or her)\s+", "", (m.group(1) or "").strip(), flags=re.I)
            extra = "from_" + ground.slug(obj) if obj and obj.lower() not in ("it", "them") else "-"
            return Effect("shuffle", "-", _target(subj or "you"), extra)
    if subj is not None and _SH_SUBJ_TGT.match(subj) and body is not None:   # 2. _shuffle_subj (subject required)
        m = _SH_SUBJ_BODY.match(body)
        if m:
            return Effect("shuffle", "-", _target(subj), "from_" + ground.slug(m.group(1)))
    return None


# NEGATIVE-STATICS (cant_combat / cant_combat_set) operand validators — the `_cant_combat` and
# `_cant_combat_set` templates split at the structural NS_CANT ("can't") terminal the GRAMMAR owns
# (`nssubj NS_CANT nsverb nstail?`) into a SUBJECT span (`nssubj`, the template's group 1) and a REST
# span (the rejoined `nsverb`+`nstail`, the template's post-"can't" portion). Each frame becomes two
# anchored per-operand certifiers: a SUBJECT validator and a REST validator that captures the verb (and,
# for `_cant_combat`, the optional object `_TGT`). The transformer reads the spans off the tree and
# applies the pair in template ORDER (`_cant_combat` FIRST, then `_cant_combat_set`) — byte-identical to
# parse_effect, or abstain (an 'except by …'/'this combat'/non-skeleton rest, a subject outside the
# template's group 1, or a verb that isn't ours). No whole-clause frame regex.
_NS_CANT_SUBJ = re.compile(r"^(?:" + _TGT + r")$", re.I)                        # _cant_combat g1 (the `_TGT` subject)
_NS_CANT_REST = re.compile(                                                     # _cant_combat post-"can't" portion
    r"^(be blocked|block or be blocked|attack or block|block|attack)"
    r"(?: (" + _TGT + r"))?(?: this (?:turn|combat))?$", re.I)                  # duration OPTIONAL (a bare '<X>
    #                  can't attack/block' is a permanent restriction) + 'this combat' (Canal Courier)
_NS_CANT_SET_SUBJ = re.compile(                                                 # _cant_combat_set g1 (creatures set)
    r"^(?:[\w' -]+ )?creatures?(?: with(?:out)? [\w' -]+?)?$", re.I)
_NS_CANT_SET_REST = re.compile(                                                 # _cant_combat_set post-"can't" portion
    r"^(be blocked|attack or block|block|attack)(?: this turn| this combat)?$", re.I)

# DOESNT_UNTAP operand validators — the `_doesnt_untap` template split into two anchored operand-span
# certifiers (the GRAMMAR owns the shape `nssubj NS_DUVERB nstail`, the distinctive 'doesn't/don't untap'
# verb is the structural anchor). `_NS_UNTAP_SUBJ` certifies the subject span is a clean `_TGT` (template
# group 1; mirrors `_DB_TGT`/`_AT_TGT`). `_NS_UNTAP_TAIL` certifies the tail span is the template's
# 'during <controller> [next] untap step[s] [for as long as …]' skeleton and CAPTURES the single 'next'
# operand (group 1). A subject outside `_TGT`, or a tail that isn't this skeleton ('during this combat',
# a 'this turn' variant), abstains exactly as the template did. No whole-clause frame regex.
_NS_UNTAP_SUBJ = re.compile(r"^(?:" + _TGT + r")$", re.I)
_NS_UNTAP_TAIL = re.compile(
    r"^during (?:its controller's|their controller's|their controllers'|your|their)"
    r"( next)? untap steps?(?: for as long as (.+?))?$", re.I)   # g2 = the for-as-long-as duration (or None)

# the block-template verb-slot -> grounded verb; ONLY the three negative-statics verbs are ours. The same
# template ALSO grounds cant_attack / cant_attack_or_block / cant_block_or_be_blocked (OTHER families) ->
# those slot values are absent from this map, so the transformer abstains (defers to the regex) on them.
# the post-'can't' verb -> grounded verb (cant_<verb>). NOW the FULL _cant_combat set (the migration was
# scoped to be_blocked/block; extended to attack/attack-or-block/block-or-be-blocked — byte-identical to
# `_cant_combat`'s `"cant_" + verb.replace(" ","_")`).
_NS_OURS = {"be blocked": "cant_be_blocked", "block": "cant_block", "attack": "cant_attack",
            "attack or block": "cant_attack_or_block", "block or be blocked": "cant_block_or_be_blocked"}


def _ns_cant(subj: str, rest: str):
    # The grammar split the clause at the structural "can't" terminal into a SUBJECT span and a REST span
    # (the rejoined verb+tail). Reproduce parse_effect's template ORDER over those spans: _cant_combat
    # (FIRST, subject is a `_TGT`, rest carries the trailing 'this turn' and an optional `_TGT` object),
    # then _cant_combat_set (subject is a 'creatures' set, no object, optional 'this turn'). Byte-identical
    # to the whole-source templates (the `can't` delimiter is unique, so the subject is unambiguous).
    ms = _NS_CANT_SUBJ.match(subj)
    mr = _NS_CANT_REST.match(rest)
    if ms and mr:                                  # _cant_combat
        verb = _NS_OURS.get(mr.group(1))
        if verb is None:
            return None                            # cant_attack / cant_attack_or_block / … -> not our family
        extra = _target(mr.group(2)) if mr.group(2) else "-"
        return Effect(verb, "-", _target(subj), extra)
    ms = _NS_CANT_SET_SUBJ.match(subj)
    mr = _NS_CANT_SET_REST.match(rest)
    if ms and mr:                                  # _cant_combat_set
        verb = _NS_OURS.get(mr.group(1))
        if verb is None:
            return None                            # cant_attack / cant_attack_or_block -> not our family
        return Effect(verb, "-", ground.slug(subj))
    return None


# CASTING restriction (§601.3e) — '<player-set> can't cast <spell-set>[ <qualifier>]'. A NON-combat static
# the combat frames (_ns_cant) decline, so it reaches `_static_effect` and currently abstains. Grounded here
# as cant_cast with the whole post-'cast' object — the spell-set AND any qualifier (incl. a 'more than N …
# each turn' LIMIT) — preserved as a faithful descriptive slug. Dropping the qualifier would invert a limit
# into a blanket prohibition ('can't draw more than one' != 'can't draw'), so we keep it whole or abstain.
# Subject must be a player-set (only players cast). Abstains on a compound non-cast action ('cast spells or
# play lands') or a joined second clause — a conflation the slug can't represent faithfully (prime directive).
_NS_CAST_SUBJ = r"your opponents|each opponent|opponents|players|each player|you|enchanted player"
_NS_CAST_RE = re.compile(r"^(?:" + _NS_CAST_SUBJ + r")$", re.I)
_NS_CAST_CONFLATE = re.compile(r"\bor (?:play|activate|put|search|attack|block)\b|, and |\band can't\b", re.I)


def _ns_cast(subj: str, rest: str):
    """'<player-set> can't cast <spell-set> …' -> cant_cast(-, _target(subj), slug(spell-set + qualifier)),
    or None (abstain). Symmetric with `_ns_cant`: reads the same SUBJECT and REST spans the grammar carved,
    keys on the 'cast' verb the combat frames lack, and slugs the whole object so the qualifier/limit rides
    the fact rather than being dropped."""
    if not _NS_CAST_RE.match(subj):
        return None
    mr = re.match(r"^cast (.+)$", rest, re.I)
    if not mr:
        return None
    obj = mr.group(1).strip()
    if "spell" not in obj or _NS_CAST_CONFLATE.search(obj):
        return None                                # not a spell-cast restriction, or a conflation -> abstain
    return Effect("cant_cast", "-", _target(subj), ground.slug(obj))


# OTHER active-voice player restrictions (§116/§120/§104) — the same '<player-set> can't <verb> <obj>' shape
# as the casting frame, for the non-combat actions the combat frames also lack: play lands/cards, draw,
# search, and the §104 game-end pair (lose/win the game). Each maps to a distinct grounded cant_<verb>; the
# object (with any qualifier/LIMIT — 'more than one card each turn') rides the slug. Subject must be a
# player-set (only players do these). A compound action ('play lands OR cast spells', 'draw cards OR gain
# life', a different-subject '… AND your opponents can't …'), or a conditional/temporal rider ('… if …',
# '… as long as …'), is a conflation -> abstain (prime directive). The 'if <cond>' rider is INCLUDED in the
# conflate set on purpose: the leaf abstains, then parse_clause's _IF_TRAIL peels '<effect> if <cond>' and
# re-grounds the bare effect with the condition in its COND slot (structured) rather than buried in the slug.
# gain-life is NOT here: 'can't gain life' parses as the keyword-grant production, not nscant — a later slice.
_NS_PLAYER_SUBJ = re.compile(
    r"^(?:your opponents?|each opponent|opponents|players|other players|each player|you|"
    r"enchanted player|target player)$", re.I)
_NS_GAME = re.compile(r"^(lose|win) the game$", re.I)
# '<player-set> can't gain life [<duration>]' (§119) — routes to the grant production (see _ToEffect.grant),
# so it's matched against the grant transformer's _src there, not in the nscant chain. group 2 = duration rider.
_GAINLIFE_RESTR = re.compile(
    r"^(your opponents?|each opponent|opponents|players|each player|you|enchanted player|that player) "
    r"can't gain life(?: (.+))?$", re.I)
_NS_PRESTR_CONFLATE = re.compile(r"\bor\b|\band\b|,|\bif\b|\bunless\b|;|\bas long as\b", re.I)
_NS_PRESTR_VERBS = {"play": "cant_play", "draw": "cant_draw", "search": "cant_search",
                    "get": "cant_get_counters", "untap": "cant_untap"}
_NS_PRESTR_NOUN = {"cant_play": r"^(?:lands?|cards?)\b", "cant_draw": r"\bcards?\b",
                   "cant_search": r"\blibrar(?:y|ies)\b", "cant_get_counters": r"\bcounters?\b",
                   "cant_untap": r"\b(?:permanents?|lands?|creatures?|artifacts?|enchantments?)\b"}


def _ns_player_restrict(subj, rest):
    """'<player-set> can't <play lands|draw|search|lose/win the game|get counters|untap …> …' -> the matching
    grounded cant_<verb>(-, _target(subj), slug(object)), or None. Symmetric with `_ns_cast`/`_ns_cant`;
    abstains on a player-set miss, a compound/conditional rider (conflation), or an object that isn't the
    verb's own noun. The untap object is a §502 LIMIT ('more than two permanents during their untap steps');
    like every limit in this family it rides the slug whole (dropping it would invert the meaning)."""
    if not _NS_PLAYER_SUBJ.match(subj):
        return None
    g = _NS_GAME.match(rest)                        # §104 game-end: the verb fully states it (no object slug)
    if g:
        return Effect("cant_" + g.group(1) + "_game", "-", _target(subj))
    if _NS_PRESTR_CONFLATE.search(rest):
        return None
    head, _, obj = rest.partition(" ")
    verb = _NS_PRESTR_VERBS.get(head)
    if verb is None or not obj or not re.search(_NS_PRESTR_NOUN[verb], obj):
        return None                                # unknown action, or object isn't this verb's noun -> abstain
    return Effect(verb, "-", _target(subj), ground.slug(obj))


# PASSIVE-voice restrictions ('<X> can't be <countered|prevented|activated>') — the subject is the thing
# restricted (a spell-set / a damage descriptor / an ability set), not a player. _combat_restriction already
# grounds 'be countered' for subjects in its _TGT/creatures set (via card_restriction); this frame, reached
# only when that declines (it runs last, via _static_effect), grounds the spell-set / damage / ability
# subjects it can't match. The whole subject is slugged faithfully (a type list 'instant and sorcery spells
# you control' is the SUBJECT, not an action conflation, so its 'and' is kept); '~' -> 'self' so the slug
# isn't lossy. A trailing 'this turn' rides cond. Guarded so each verb only claims its own kind of subject.
_NS_PASSIVE = re.compile(r"^be (countered|prevented|activated|regenerated)( this turn)?$", re.I)


def _ns_passive_restrict(subj, rest):
    """'<spell-set> can't be countered' / '<damage> can't be prevented' / '<abilities> can't be activated'
    -> cant_be_countered | cant_prevent_damage | cant_be_activated (-, slug(subject), cond=this_turn?), or
    None. Each verb requires its own subject kind (a spell / damage / ability) so it never over-claims."""
    m = _NS_PASSIVE.match(rest)
    if not m:
        return None
    kind, cond = m.group(1).lower(), ("this_turn" if m.group(2) else "-")
    s = subj.replace("~", "self")
    if kind == "countered" and ("spell" in s or s in ("self", "it", "that")):
        return Effect("cant_be_countered", "-", ground.slug(s), "-", cond)
    if kind == "prevented" and "damage" in s:
        return Effect("cant_prevent_damage", "-", ground.slug(s), "-", cond)
    if kind == "activated" and "abilit" in s:
        return Effect("cant_be_activated", "-", ground.slug(s), "-", cond)
    # '<creature> can't be regenerated [this turn]' (§701.19) — the `_cant_regen` template: cant_be_regenerated
    # (-, _target(subj)); the subject is its OWN _TGT (or the literal 'a creature destroyed this way') and the
    # 'this turn' duration is DROPPED, exactly as the regex does. (Bare 'can't be regenerated'->'it' has no
    # nssubj so it never reaches here — stays on the regex template, FLIP-ONLY.)
    if kind == "regenerated" and (re.fullmatch(_TGT, subj.strip(), re.I) or subj.strip() == "a creature destroyed this way"):
        return Effect("cant_be_regenerated", "-", _target(subj.strip()))
    return None


# COUNTER-placement restrictions (§122) the player/passive frames don't cover: the SUBJECT is 'counters' (a
# put-on lock) or a permanent with a counter-COUNT limit. 'counters can't be put on <recipients>' (Solemnity)
# -> cant_put_counters(-, slug(recipients)) — the recipient TYPE LIST ('artifacts, creatures, …, or lands')
# is the object, kept whole. '<X> can't have [more than N] <kind> counters [on it]' (Rasputin) ->
# cant_have_counters(_target(X), slug(limit)) — the count limit rides the slug (never dropped).
def _ns_counter_restrict(subj, rest):
    """'counters can't be put on <X>' / '<X> can't have … counters …' -> cant_put_counters / cant_have_counters,
    or None. Only fires when the clause is actually about counters (so it never over-claims a generic 'have')."""
    if subj == "counters":
        m = re.match(r"^be put on (.+)$", rest)
        return Effect("cant_put_counters", "-", ground.slug(m.group(1))) if m else None
    m = re.match(r"^have (.+)$", rest)
    if m and "counter" in m.group(1):
        return Effect("cant_have_counters", "-", _target(subj), ground.slug(m.group(1)))
    return None


_PARSER = Lark(_GRAMMAR % {"verbs": _verb_alt()}, parser="earley", lexer="dynamic")


class _Quant(str):
    pass


class _ChsQuant(str):     # the choose-family quantifier (folded into the chosen-thing slug)
    pass


class _ChsRest(str):      # the chosen-thing noun phrase (opaque, slugged like the regex `(.+?)$`)
    pass


class _Source:
    def __init__(self, zone):
        self.zone = zone


class _Subj(str):
    pass


class _Body(str):
    pass


class _Amt(str):
    pass


class _Tgt(str):
    pass


class _DTgt(str):      # a deal-variant target (amount-first / target-first 'equal to' forms)
    pass


class _EqAmt(str):     # an 'equal to <amount>' span (either ordering)
    pass


class _DivTgt(str):    # the 'divided as you choose among <targets>' span (raw-slugged)
    pass


class _RvSubj(str):    # a player subject before 'reveal[s]' (validated against _PLAYER)
    pass


class _RvBody(str):    # the reassembled reveal clause body (everything after 'reveal[s]')
    pass


class _PvPre(str):     # the prevent pre-DMG span (pvpre) — 'all [combat]' / 'the next <count>'
    pass


class _PvTail(str):    # the prevent post-DMG span (pvtail) — 'that would be dealt … [to <tgt>] …'
    pass


class _PfSubj(str):    # the phasing subject span (pfsubj) — the permanent that phases out/in (validated _TGT)
    pass


class _RdPre(str):     # redirect amount span (rdpre) — 'the next <N>' / 'all [combat]'
    pass


class _RdA(str):       # redirect source-side span (rda) — 'that would be dealt to <A> [this turn] [by <src>]'
    pass


class _RdB(str):       # redirect recipient span (rdb) — '<B> [instead]'
    pass


class _SkSubj(str):    # skip subject span (sksubj) — the optional acting player (validated _TGT)
    pass


class _SkBody(str):    # skip body span (skbody) — '(your|its|their|his or her) [next] <phase>'
    pass


class _XtBody(str):    # exile-top body span (xtbody) — value unused; the clause is re-matched off src
    pass


class _XlObj(str):     # exile-until object span (xlobj) — value unused; the object is re-matched off src
    pass


class _AsKind(str):    # amass army-type span (askind) — slugged into the effect's extra
    pass


class _AsNum(str):     # amass count token (asnum) — validated against the template's (\d+|one|two|three|x)
    pass


class _MsNum(str):     # monstrosity count token (msnum) — validated against (\d+|one..five|x)
    pass


class _GdSubj(str):    # the goaded creature span (gdsubj) — validated _TGT
    pass


class _BdgSubj(str):   # the becomes-designation subject span (bdgsubj) — validated _TGT
    pass


class _FeSubj(str):    # the 'fight each other' subject span (fesubj) — validated _TGT, leading 'then' stripped
    pass


class _EndureSubj(str):    # the enduring creature's subject span (enduresubj) — validated via the re-matched _endure
    pass


class _KwnNum(str):    # the numbered-keyword-action count token (kwnnum) — validated (\d+|one..five|x)
    pass


class _CntObj(str):    # the object span after 'choose new targets for' (cntobj) — slugged to the effect target
    pass


class _AcrRest(str):   # the restriction span after 'activate [this ability] only' (§602.5) — slugged to extra
    pass


class _SwptBody(str):  # the span after 'switch' — "<X>'s power and toughness [until end of turn]" (swptbody)
    pass


class _SmaObj(str):    # the spell/card scope after 'mana of any … can be spent to cast/play' (smaobj)
    pass


# the `_switch_pt` template MINUS the leading 'switch ' (consumed by the SWITCHPT terminal): the _TGT object
# whose P/T is switched + the fixed 'power and toughness' tail + the dropped optional duration.
_SWPT_RE = re.compile(r"^(" + _TGT + r")'s power and toughness(?: until end of turn)?$", re.I)


class _FcVerb(str):    # the play/cast verb of a free-cast clause (fcastverb)
    pass


class _FcObj(str):     # the object span of a free-cast clause (fcastobj) — slugged to the effect target
    pass


class _PdCond(str):    # the condition span of an impulse play-duration clause (pdcond)
    pass


class _KviSubj(str):   # the intransitive-keyword-action subject span (kvisubj) — validated _TGT, dropped to target
    pass


class _ExcBody(str):   # the flat 'exchange …' object run (value unused; object sliced from src like _DbBody)
    pass


class _CpBody(str):    # the flat 'copy …' object run (value unused; the object is sliced from src like _DbBody)
    pass


class _RhBody(str):    # the flat 'return[s] [<subj>] <obj> [from <zone>]' run (value unused; sliced from src like _CpBody)
    pass


class _MrBody(str):    # the combat-requirement body span (mrbody) — '<subj> attacks/blocks [<obj>] [<dur>]'
    pass


class _GccSubj(str):   # the optional subject span before 'can attack/block' (the whole clause is re-sliced from _src)
    pass


class _SfSubj(str):    # a player subject before a subject-first object verb (validated against _PLAYER)
    pass


class _SfRest(str):    # the object span after the subject-first verb (value unused; the span is sliced from src)
    pass


class _PzBody(str):    # the reassembled put-to-zone clause body (everything after the leading verb)
    pass


class _LkSubj(str):    # the optional `_TGT` subject span before 'look[s]' (read off the tree by `look`)
    pass


class _LkBody(str):    # the post-verb 'at …' span after 'look[s]' (read off the tree by `look`)
    pass


class _ShSubj(str):    # the optional `_TGT` subject span before 'shuffle[s]' (read off the tree by `shuffle`)
    pass


class _ShBody(str):    # the post-verb source/library span after 'shuffle[s]' (read off the tree by `shuffle`)
    pass


class _GchRest(str):   # the keyword-list span after 'your choice of' (gchrest) — validated §702 in grant_choice
    pass


class _Dur(str):
    pass


class _FERest(str):                      # the 'for each <X>' object span of a count-scaled pump (mfeclause)
    pass


class _CSubj(str):
    pass


class _CCount(str):
    pass


class _CKind(str):
    pass


class _CTarget(str):
    pass


class _BcmTgt(str):       # the becomes target NP (the permanent being animated)
    pass


class _BcmTail(str):      # the raw post-P/T span (kept only so the parse consumes it; g3 is sliced)
    pass


class _RcCount(str):      # the count span (rccount) — the _remove_counter template's group 1
    pass


class _RcKind(str):       # the optional kind span (rckind) — the _remove_counter template's group 2
    pass


class _RcTarget(str):     # the target span (rctarget) — the _remove_counter template's group 3 (TARGET)
    pass


class _DbBody(str):       # the flat 'double …' object run (guarded + slugged like the object-verb leaf)
    pass


class _NsSubj(str):       # the subject NP before a negative-static verb (value unused; frame regex re-parses src)
    pass


class _NsVerb(str):       # the combat verb after "can't" (value unused; frame regex re-parses src)
    pass


class _NsTail(str):       # the post-verb tail of a negative-static clause (value unused; frame re-parses src)
    pass


class _AmLead(str):       # the optional player subject before 'add[s]' (validated by _AM_TGT, slugged by _target)
    pass


class _AmRest(str):       # the mana-spec span after 'add[s]' (fed to _mana_production; spacing-independent)
    pass


class _AmExpr(str):       # the state-derived <expr>/<thing> span of a conditional add (slugged by ground.slug)
    pass


class _AmSym(str):        # the single mana symbol of 'add an amount of <sym> equal to …' (color via _mana_production)
    pass


class _AmXMana(str):      # the 'X mana of any [one] color' span of shape 2 (validated/colored below)
    pass


class _AtSrc(str):        # the moved-object span (atsrc) — the _attach template's group 1 (EXTRA)
    pass


class _AtDest(str):       # the destination span (atdest) — the _attach template's group 2 (TARGET)
    pass


class _FgO(str):          # a fight operand span (fgo) — validated as _TGT in the fight transformer
    pass


class _TfBody(str):       # the flat 'transform …' object run (guarded + slugged like the object-verb leaf)
    pass


class _MfBody(str):       # the flat 'manifest …' body run (presence consumes the span; shape validated from _src)
    pass


_DET = re.compile(r"(?:each|every|all|any|another|target|the|a|an)\b")


def _pure_target_conj(tgt: str) -> bool:
    """True iff `tgt` is a conjunction of TARGET phrases ('each creature and each player') — every
    'and'-separated conjunct is a determiner-led noun phrase with no second ' to ' clause. Such a target
    grounds to a single slug byte-identical to the regex, so deal()/etc. may keep it. Anything else (a new
    effect or a second damage instance — 'and you gain N life', 'and N damage to you') is bucket B: NOT a
    pure target, so the caller abstains rather than copy the regex's lossy single-effect grounding."""
    parts = re.split(r"\s+and\s+", tgt)
    if len(parts) < 2:
        return False
    return all(_DET.match(p.strip()) and " to " not in p for p in parts)


_LIT_EFFECTS = {                                   # fixed-phrase clause -> the nullary Effect the regex made
    "clash with an opponent": ("clash", "-", "you"),
    "you become the monarch": ("become_monarch", "-", "you"),
    "you take the initiative": ("take_initiative", "-", "you"),
}


@v_args(inline=True)
class _ToEffect(Transformer):
    def lit(self, tok):                            # §720/§701 literal keyword-action effects (see litclause)
        v = _LIT_EFFECTS.get(str(tok).strip())
        return Effect(*v) if v else None

    def get_energy_v(self, tok):
        # 'you get {E}{E}…' (§107.16) — the EXACT `_get_energy` template: get_energy(<#{e}>, you). Count the
        # energy glyphs in the matched terminal text (== the regex's m.group(1).count('{') — 'you get' has no '{').
        return Effect("get_energy", str(tok).count("{"), "you")

    def kvmnum(self, tok):
        return _KwnNum(str(tok))                   # optional 'collect evidence N' count (reuses the kw-action marker)

    def kvmulti(self, *args):
        # a multi-word §701 keyword action ('manifest dread', 'the ring tempts you', …) — the `_bare_action`
        # leaf: slug the matched phrase to its verb, ground only if it's a real keyword action -> <verb>(-, you).
        # With an optional trailing count ('collect evidence N', the lone NUMBERED multi-word action) the EXACT
        # `_kwaction_n` template applies instead -> <verb>(<n>, you); the count is restricted to its numeric set.
        tok = next((str(a) for a in args if getattr(a, "type", None) == "KV_MULTI"), None)
        num = next((a for a in args if isinstance(a, _KwnNum)), None)
        if tok is None:
            return None
        v = ground.slug(tok)
        if v not in ground.keyword_actions():
            return None
        if num is not None:
            if not _MS_NUM.match(str(num).strip()):
                return None
            n = _amount(str(num).strip())
            return Effect(v, n if n is not None else "-", "you")
        return Effect(v, "-", "you")

    def smaobj(self, *toks):
        return _SmaObj(" ".join(str(t) for t in toks))

    def spend_mana_for(self, *args):
        # 'mana of any type can be spent to cast/play <X>' (§106.6) — the EXACT `_mana_any_for` template:
        # spend_mana_as(-, you, 'any_color_for_' + slug(<X>)). The leading anchor is consumed by MANA_ANY_FOR.
        obj = next((str(a) for a in args if isinstance(a, _SmaObj)), None)
        if obj is None or not obj.strip():
            return None
        return Effect("spend_mana_as", "-", "you", "any_color_for_" + ground.slug(obj.strip().lower()))

    def spend_mana_as_v(self, tok):
        # '[you] [may] spend mana as though it were mana of any color [to cast …]' — the EXACT `_spend_as`
        # template: spend_mana_as(-, you, 'any_color'). The optional 'to cast …' tail is dropped (always any_color).
        return Effect("spend_mana_as", "-", "you", "any_color")

    def roll_die_v(self, tok):
        # 'roll <count> d<N>' / 'roll <count> <word>-sided die' (§705) — the EXACT `_roll`/`_roll_sided`
        # templates re-applied to the matched phrase: roll_die(<n|1>, you, dN). _SIDED maps the spelled-out
        # face count (six->6) exactly as the regex does. Default count 1 when the quant word isn't numeric.
        s = str(tok).strip()
        m = re.match(r"^roll (a|an|one|two|three|\w+) (d\d+)s?$", s, re.I)
        if m:
            n = _amount(m.group(1))
            return Effect("roll_die", n if n is not None else 1, "you", m.group(2).lower())
        m = re.match(r"^roll (a|an|one|two|three|\w+) ([\w]+)-sided (?:die|dice)$", s, re.I)
        if m:
            n = _amount(m.group(1))
            sides = _SIDED.get(m.group(2).lower()) or (int(m.group(2)) if m.group(2).isdigit() else None)
            return Effect("roll_die", n if n is not None else 1, "you", f"d{sides}") if sides else None
        return None

    def put_back_any_order(self, tok):
        # 'put them back in any order' (§401) — the EXACT `_put_back_any_order` template: a fixed phrase
        # grounding to put_on_top(-, them, any_order). The whole clause is the phrase (no operands to read).
        return Effect("put_on_top", "-", "them", "any_order")

    def dcbody(self, *toks):
        return None                                # value unused; the clause is re-matched from self._src

    def distribute_v(self, *args):
        # 'distribute <N> <kind> counters among <targets>' (§122) — the EXACT `_distribute_counters` template
        # re-applied to self._src: put_counter(<n|X>, _target(targets), <kind>, distributed). A P/T kind
        # ('+1/+1') is kept RAW (the '/' branch); a word kind is slugged — byte-identical to the regex.
        src = getattr(self, "_src", None)
        if src is None:
            return None
        m = _DC_RE.match(src.strip())
        if not m:
            return None
        n = _amount(m.group(1))
        kind = m.group(2) if "/" in m.group(2) else ground.slug(m.group(2))
        return Effect("put_counter", n if n is not None else "X", _target(m.group(3)), kind, "distributed")

    def pmccount(self, *toks):
        return None                                # value unused; re-matched from self._src

    def pmctarget(self, *toks):
        return None

    def move_counters_v(self, *args):
        # 'put <its|all|all of its> counters on <tgt>' (§122 relocation) — the EXACT `_move_counters` template
        # re-applied to self._src: put_counter(slug(<its|all|all of its>), _target(tgt), moved).
        src = getattr(self, "_src", None)
        if src is None:
            return None
        m = _MC_RE.match(src.strip())
        if not m:
            return None
        return Effect("put_counter", ground.slug(m.group(1)), _target(m.group(2)), "moved")

    def mcfbody(self, *toks):
        return None

    def move_counter_from_v(self, *args):
        # 'move <N> <kind> counters from <src> onto|to <tgt>' (§122 relocation) — the EXACT `_move_counter_from`
        # template re-applied to self._src: put_counter(<n|X>, _target(tgt), <kind>, moved_from_<src>). A P/T
        # kind ('+1/+1') is kept raw; a word kind is slugged — byte-identical to the regex.
        src = getattr(self, "_src", None)
        if src is None:
            return None
        m = _MCF_RE.match(src.strip())
        if not m:
            return None
        n = _amount(m.group(1))
        kind = m.group(2) if "/" in m.group(2) else ground.slug(m.group(2))
        return Effect("put_counter", n if n is not None else "X", _target(m.group(4)), kind,
                      "moved_from_" + _target(m.group(3)))

    def extra_combat_v(self, tok):
        # '[after this [main] phase,] there is an additional combat phase [followed by an additional main phase]'
        # (§505) — the EXACT `_extra_combat` template: a CONSTANT tuple. The ECOMBAT terminal already matched the
        # whole phrase (start consumes all), so emit it directly. extra_combat(-, you).
        return Effect("extra_combat", "-", "you")

    def ycbody(self, *toks):
        return None                                # value unused; re-matched from self._src

    def you_control_v(self, *args):
        # clause-initial 'you control <X> [dur]' (§720 static control, no 'gain') — the EXACT `_control` template
        # re-applied to self._src: gain_control(-, _target(X), <until_end_of_turn|slug(dur)|->). A non-`_control`
        # clause abstains. The dominant corpus form is the aura 'you control enchanted <type>'.
        src = getattr(self, "_src", None)
        if src is None:
            return None
        m = _CTRL_RE.match(src.strip())
        if not m:
            return None
        g2 = m.group(2)
        extra = ("until_end_of_turn" if g2 and "end of turn" in g2
                 else (ground.slug(g2) if g2 else "-"))
        return Effect("gain_control", "-", _target(m.group(1)), extra)

    def lurebody(self, *toks):
        return None

    def lure_v(self, *args):
        # 'all creatures able to block <X> [this turn|this combat] do so' (§509) — the EXACT `_lure` template
        # re-applied to self._src: lure(-, _target(X)). Object outside `_TGT` abstains -> regex leaf.
        src = getattr(self, "_src", None)
        if src is None:
            return None
        m = _LURE_RE.match(src.strip())
        return Effect("lure", "-", _target(m.group(1))) if m else None

    def extra_turn_v(self, tok):
        # '[<player>] take[s] [an|N] extra turn(s) after this one' (§500.7) — the EXACT `_extra_turn` template
        # re-applied to the matched phrase: extra_turn(<n|->, _target(subj|you)). The leading subject is _TGT-
        # validated by the re-match (an over-broad head from the bounded terminal abstains here -> regex leaf).
        s = str(tok).strip()
        m = re.match(rf"^(?:({_TGT}) )?(?:takes?|take) (an|one|two|three|\w+) extra turns? after this one$", s, re.I)
        if not m:
            return None
        n = _amount(m.group(2))
        return Effect("extra_turn", n if n is not None else "-", _target(m.group(1) or "you"))

    def ceqmlead(self, *toks):
        return _Body(" ".join(str(t) for t in toks))   # leading 'the <kw>' span (src is re-matched)

    def cost_eq_mana(self, *args):
        # 'the <keyword> cost is equal to its mana cost' — reproduce `_granted_keyword_cost` byte-for-byte:
        # grant_keyword(<kw>, 'it', 'cost_equals_mana_cost') iff the kw is in the §702 roster, else abstain.
        src = getattr(self, "_src", None)
        m = _CEQMANA_RE.match(src.strip()) if src is not None else None
        if not m:
            return None
        kw = ground.slug(m.group(1))
        if kw not in _KW:
            return None
        return Effect("grant_keyword", kw, "it", "cost_equals_mana_cost")

    def quant(self, tok):
        return _Quant(str(tok))

    # --- CHOOSE family --------------------------------------------------------
    def chstok(self, tok):
        return str(tok)

    def chsquant(self, tok):
        return _ChsQuant(str(tok))

    def chsrest(self, *toks):
        return _ChsRest(" ".join(str(t) for t in toks))

    def chs(self, *args):
        quant = next((str(a) for a in args if isinstance(a, _ChsQuant)), None)
        rest = next((str(a) for a in args if isinstance(a, _ChsRest)), None)
        if quant is None or rest is None:
            return None
        quant = quant.strip().lower()
        rest = rest.strip()
        # Only own the quantifiers the regex `_choose` alternation actually selects (case-folded). The
        # shared QUANT terminal is broader (four/five/all/each/x, 'up to that many'); restricting here
        # keeps us identical-or-abstain — anything else defers to the regex.
        if quant not in _CHS_QUANTS:
            return None                            # 'four'/'five'/'all'/'x'/'up to that many' etc. -> regex
        if not rest:
            return None                            # empty chosen-thing (the modal 'choose one —' has '—')
        # The regex's `(.+?)` is opaque text we rejoin from tokens; a char outside the WORD class
        # (em-dash bullet of a MODAL list, a `{..}` mana symbol, a comma list) won't tokenize here ->
        # abstain (defer to the regex / unit-handler layer, per the directive's modal carve-out).
        full = (quant + " " + rest).strip()
        if _is_compound_object(full) or _is_compound_object(rest):
            return None                            # run-on second effect ('… then …', '… and <verb> …')
        if _CHS_TGT.match(full):
            # `_verb_target` precedence: the WHOLE object is a `_TGT` -> slug it via `_target`, article kept.
            return Effect("choose", "-", _target(full))
        q = "" if quant in ("a", "an", "one") else ground.slug(quant) + "_"
        return Effect("choose", "-", q + ground.slug(rest))

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
        # BATTLEFIELD RETURN with riders ('Return <obj> [from <zone>] to the battlefield [tapped]
        # [under <ctrl>'s control] [with <counter>] [attached to <Y>] [at the beginning of <step>]') — the
        # `ret` logic below abstains on the rider trailer; the registered `_return_bf` folds the whole family
        # into one canonical extra slug. Re-match its EXACT pattern on src + call it -> byte-identical.
        src = getattr(self, "_src", None)
        if src is not None:
            m = _RBF_RX.match(src.strip())
            if m:
                e = _RBF_FN(m)
                if e is not None:
                    return e
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

    def ossubj(self, *toks):
        return _Subj(" ".join(str(t) for t in toks))   # leading actor span (dropped; subject validated via _taputap on src)

    def pszsubj(self, *toks):
        return _Subj(" ".join(str(t) for t in toks))   # leading player span (dropped; validated via _subject_puts on src)

    def pszbody(self, *toks):
        return _Body(" ".join(str(t) for t in toks))   # object + destination span (src re-matched)

    def subject_puts(self, *args):
        # '<player> puts <obj> on top of/on the bottom of/into <zone>' — re-match the registered
        # `_subject_puts` (then `_owner_puts` 'on their choice of top/bottom') on src -> byte-identical
        # (put_on_top/put_on_bottom/put_in_* with 'by_<player>'); a non-put-to-zone clause abstains.
        src = getattr(self, "_src", None)
        if src is None:
            return None
        s = src.strip()
        m = _SP_RX.match(s)
        if m:
            e = _SP_FN(m)
            if e is not None:
                return e
        m = _OP_RX.match(s)
        if m:
            e = _OP_FN(m)
            if e is not None:
                return e
        return None

    def pgcsubj(self, *toks):
        return _CSubj(" ".join(str(t) for t in toks))

    def pgccount(self, tok):
        return _CCount(str(tok))

    def pgckind(self, tok):
        return _CKind(str(tok))

    def player_gets_counter(self, *args):
        # '<player> gets <n> poison/energy/experience/rad counter(s)' -> put_counter on the player. GROUNDED
        # NATIVELY from the grammar-captured count + kind (no card_effects template): amount = _amount(count)
        # (or 'X'), target = _target(subject), extra = kind. The subject must be a clean target phrase
        # (validated against _TGT — structural, NOT a template re-match): the (WORD|QUANT|NUM)+ span otherwise
        # over-captures a trigger/conditional/compound prefix ('When … dies, you' / 'target player draws …, and')
        # that the regex's _TGT subject rejects. A non-player counter kind (e.g. '+1/+1') also abstains.
        subj = next((str(a) for a in args if isinstance(a, _CSubj)), None)
        count = next((str(a) for a in args if isinstance(a, _CCount)), None)
        kind = next((str(a) for a in args if isinstance(a, _CKind)), None)
        if count is None or kind is None or subj is None:
            return None
        k = kind.strip().lower()
        if k not in ("poison", "energy", "experience", "rad"):
            return None
        if not re.fullmatch(_TGT, subj.strip(), re.I):       # reproduce _gets_counter's ({_TGT}) subject guard
            return None
        n = _amount(count.strip())
        return Effect("put_counter", n if n is not None else "X", _target(subj.strip()), k)

    def tapuntap_subj(self, *args):
        # '<player> taps/untaps <obj>' — the subject-prefixed `_taputap` form (subject DROPPED). Re-match
        # `_taputap` on src (its `(?:{_TGT} )?` validates + drops the leading subject) and ground the object
        # byte-for-byte. tap/untap only: other OVERBs have no subject-drop template, so a non-match abstains.
        src = getattr(self, "_src", None)
        if src is None:
            return None
        cm = _ENTERS_CTR_RE.match(src.strip())
        if cm:                                   # '<subj> enters with N <kind> counter(s) on it' -> put_counter
            n = _amount(cm.group(2))             # reproduce _enters_counters_eff exactly (kind: keep +P/+P, else slug)
            kind = cm.group(3) if "/" in cm.group(3) else ground.slug(cm.group(3))
            return Effect("put_counter", n if n is not None else 1, _target(cm.group(1) or "self"), kind, "on_enter")
        m = _TAPUNTAP_RE.match(src.strip())
        if m:
            return Effect(m.group(1).lower(), "-", _target(m.group(2)))
        return None

    def imperative(self, verb, *rest):
        verb = str(verb).lower()
        quant, _zone, otext = self._assemble(rest)
        if otext is None:
            return None
        if verb in ("tap", "untap", "taps", "untaps"):   # `_taputap`: tap/untap whose object _TGT spans a
            src = getattr(self, "_src", None)            # 'with <counter>'/'that has …' RIDER (the guards below
            tm = _TAPUNTAP_RE.match(src.strip()) if src is not None else None   # would abstain on it). Reproduce
            if tm:                                       # the whole-NP target byte-for-byte before the guards.
                return Effect(tm.group(1).lower(), "-", _target(tm.group(2)))
            tu = _TAP_OR_UNTAP_RE.match(src.strip()) if src is not None else None   # 'tap or untap <X>' idiom
            if tu:
                return Effect("untap", "-", _target(tu.group(1)), "or_tap")
        if _TOPLIB.match(otext) or _WITHCTR.search(otext) or _COORD.match(otext) or _MULTICLAUSE.search(otext):
            # the leaf abstains on this rider — but the regex's last-resort `_generic_object_verb` still
            # grounds the WHOLE NP as the slug target for an `_OBJ_VERBS` verb (when not a colon-cost/equal-to/
            # `if`/`unless`/compound). Reproduce it byte-for-byte before deferring: 'destroy each permanent with
            # a doom counter on it', 'regenerate target creature with a +1/+1 counter on it', etc. (Only this
            # rider path — a clean _TGT object like '~' has no guard match and is grounded below via `_target`
            # ~->self, NOT slug ~->''. Em-dash/brace wrappers lex-fail upstream, never reach here.)
            # SCOPED to destroy/regenerate: `_generic_object_verb` is the regex chain's LAST resort, but here it
            # runs WITHOUT the specific templates first — so it must not pre-empt a verb that has one. exile
            # (xtclause/xlclause) and the 'tap or untap' idiom (`_tap_or_untap`) DO -> they'd diverge; excluded.
            src = getattr(self, "_src", None)
            gm = _GENOBJ_RE.match(src.strip()) if src is not None else None
            if gm:
                gv = ground.slug(gm.group(1))
                if gv in ("destroy", "regenerate") and not _OBJ_BAD.search(gm.group(2)) and not _is_compound_object(gm.group(2)):
                    return Effect(gv, "-", ground.slug(gm.group(2)))
            return None                        # defer to the regex (better convention / coordinated verb)
        if verb == "sacrifice":
            # `_sacrifice_a` (`^sacrifice (a|an|another|two|three) ([\w ~']+?)$`) supplies the count amount,
            # but ONLY when the object-after-quant is in its char class `[\w ~']` (no hyphen/comma). A
            # hyphenated/comma object ('a non-Demon creature') makes `_sacrifice_a` FAIL and fall through to
            # `_verb_target`, which yields amount '-'. Gate the count on that exact boundary (faithful).
            obj_after = otext[len(quant) + 1:] if (quant and otext.startswith(quant + " ")) else ""
            n = (_amount(quant) if quant in ("a", "an", "another", "two", "three")
                 and re.fullmatch(r"[\w ~']+", obj_after) else None)
            return Effect("sacrifice", n if isinstance(n, int) else "-", _target(otext))
        if verb in _SIMPLE:
            return Effect(_SIMPLE[verb], "-", _target(otext))
        return None

    def dsrc(self, *toks):
        return _Subj(" ".join(str(t) for t in toks))   # the source is dropped (reuse _Subj marker)

    def damamt(self, tok):
        return _Amt(str(tok))

    def dtarget(self, *toks):
        return _Tgt(" ".join(str(t) for t in toks))

    def deal(self, *args):
        amt = next((str(a) for a in args if isinstance(a, _Amt)), None)
        tgt = next((str(a) for a in args if isinstance(a, _Tgt)), None)
        if amt is None or tgt is None:
            return None
        n = _amount(amt)
        if n is None:
            return None                        # non-numeric amount ('that much' etc) -> regex variant
        tgt = tgt.strip().lower()
        if "," in tgt or re.search(r"\b(?:then|gains?|draws?|loses?|deals?)\b", tgt):
            return None                        # comma list / new-effect verb -> compound splitter (bucket B)
        if " and " in tgt and not _pure_target_conj(tgt):
            return None                        # an 'and' that isn't a pure target conjunction -> bucket B
        return Effect("deal_damage", n, _target(tgt))

    # --- deal_damage VARIANTS -------------------------------------------------
    # span markers (distinct classes so the variant transformers pick the right slot)
    def dteqtgt(self, *toks):
        return _DTgt(" ".join(str(t) for t in toks))

    def deqamt(self, *toks):
        return _EqAmt(" ".join(str(t) for t in toks))

    def dvamt(self, *toks):
        return _EqAmt(" ".join(str(t) for t in toks))

    def ddivtgt(self, *toks):
        return _DivTgt(" ".join(str(t) for t in toks))

    def _coord(self, s: str) -> bool:
        # a coordinated / multi-clause span the regex would slug whole (lossy) — abstain (faithful-or-abstain)
        return "," in s or bool(re.search(r"\b(?:and|then|gains?|draws?|loses?)\b", s))

    def _tgt_wrapped(self, s: str) -> bool:
        # the variant target swallowed a trailing wrapper / conditional clause ('… unless that player
        # sacrifices it', '… if you do') — the regex's constrained _TGT wouldn't reach here; abstain.
        return bool(re.search(r"\b(?:unless|if|until|whenever|where)\b", s))

    def deal_eq(self, *args):           # 'deals damage equal to <amt> to <tgt>'
        amt = next((str(a) for a in args if isinstance(a, _EqAmt)), None)
        tgt = next((str(a) for a in args if isinstance(a, _DTgt)), None)
        if amt is None or tgt is None:
            return None
        amt, tgt = amt.strip(), tgt.strip()
        if not amt or not tgt or self._coord(amt) or self._coord(tgt) or self._tgt_wrapped(tgt):
            return None
        if re.search(r"\b(?:to|into|onto)\b", amt):
            return None                 # deqamt swallowed a 'to' as a WORD -> the split is wrong -> abstain
        if " to " in tgt and "up to" not in tgt:
            return None                 # the target swallowed a 'to' ('… equal to <amt> to it to any target')
                                        # -> ambiguous multi-'to' split -> defer to the regex's non-greedy parse
        return Effect("deal_damage", "equal_to_" + ground.slug(amt), _target(tgt))

    def deal_teq(self, *args):          # 'deals damage to <tgt> equal to <amt>'
        amt = next((str(a) for a in args if isinstance(a, _EqAmt)), None)
        tgt = next((str(a) for a in args if isinstance(a, _DTgt)), None)
        if amt is None or tgt is None:
            return None
        amt, tgt = amt.strip(), tgt.strip()
        if tgt == "itself":
            # 'X deals damage to itself equal to Y' -> `_damage_self`: the SOURCE (dsrc) is the self-target,
            # extra='itself'. Reproduce the regex byte-for-byte: target=_target(g1), amount=equal_to_<slug(g2)>.
            src = next((str(a) for a in args if isinstance(a, _Subj)), None)
            if src is None:
                return None
            src = src.strip()
            if not amt or self._coord(amt) or not _DSELF_TGT.match(src):
                return None             # coordinated amount (lossy) or non-_TGT source -> abstain (regex serves)
            return Effect("deal_damage", "equal_to_" + ground.slug(amt), _target(src), "itself")
        if not amt or not tgt or self._coord(amt) or self._coord(tgt) or self._tgt_wrapped(tgt):
            return None
        if " to " in tgt and "up to" not in tgt:
            return None                 # target swallowed a stray 'to' -> ambiguous split -> defer to regex
        return Effect("deal_damage", "equal_to_" + ground.slug(amt), _target(tgt))

    def deal_tm(self, *args):           # 'deals that much damage to <tgt>'
        tgt = next((str(a) for a in args if isinstance(a, _Tgt)), None)
        if tgt is None:
            return None
        tgt = tgt.strip().lower()
        if not tgt or self._coord(tgt):
            return None
        return Effect("deal_damage", "that_amount", _target(tgt))

    def deal_div(self, *args):          # 'deals N damage divided as you choose among <tgts>'
        amt = next((str(a) for a in args if isinstance(a, _Amt)), None)
        tgt = next((str(a) for a in args if isinstance(a, _DivTgt)), None)
        if amt is None or tgt is None:
            return None
        amt, tgt = amt.strip(), tgt.strip()
        if not tgt:
            return None
        # the regex slugs the divided-target OPAQUELY (no coordination split), so 'and/or' lists are
        # faithful here; only a genuine following clause ('then …') would be lossy (the splitter removed
        # those already) — mirror the regex exactly.
        if re.search(r"\bthen\b", tgt):
            return None
        n = _amount(amt)
        amount = n if n is not None else ground.slug(amt)
        return Effect("deal_damage", amount, ground.slug(tgt), "divided")   # target RAW-slugged (matches regex)

    def mtgt(self, *toks):
        return _Tgt(" ".join(str(t) for t in toks))

    def mdur(self, tok):
        return _Dur(str(tok))

    def boost(self, *args):
        tgt = next((str(a) for a in args if isinstance(a, _Tgt)), None)
        dur = next((str(a) for a in args if isinstance(a, _Dur)), None)
        pt = next((str(a) for a in args if re.match(r"^[+-]", str(a))), None)   # the P/T delta
        if tgt is None or pt is None:
            return None
        tgt = tgt.strip().lower()
        if _MULTICLAUSE.search(tgt) or ("," in tgt and not _TYPELIST_ANTHEM.match(tgt)):
            return None                        # multi-clause subject -> regex chain owns it (but a TYPE-LIST
            #                                    anthem subject — 'X, Y, and Z [you control]' — is kept)
        perpetual = tgt.endswith(" perpetually")
        if perpetual:
            tgt = tgt[:-len(" perpetually")].strip()   # '<X> perpetually gets …' -> cond=perpetual
        cond = "-"
        if dur:
            if perpetual:
                return None                    # both a duration and 'perpetually' -> ambiguous, abstain
            d = dur.strip().lower()
            cond = "-" if d == "until end of turn" else ground.slug(d)
        elif perpetual:
            cond = "perpetual"
        return Effect("modify_pt", pt.replace(" ", "").upper(), _target(tgt), "-", cond)   # X stays uppercase

    def mferest(self, *toks):
        return _FERest(" ".join(str(t) for t in toks))

    def boost_foreach(self, *args):
        # '<t> gets +N/+N until end of turn for each <X>' — count-scaled pump (§107.3). Reproduce
        # `_boost_foreach` EXACTLY: digits-only P/T delta, the literal 'until end of turn' duration, and
        # the '<delta>_per_<slug(X)>' amount with extra='until_end_of_turn'. Abstain on anything the regex's
        # narrower pattern wouldn't match (an X-delta, a different duration, a compound subject).
        tgt = next((str(a) for a in args if isinstance(a, _Tgt)), None)
        dur = next((str(a) for a in args if isinstance(a, _Dur)), None)
        rest = next((str(a) for a in args if isinstance(a, _FERest)), None)
        pt = next((str(a) for a in args if isinstance(a, str)
                   and not isinstance(a, (_Tgt, _Dur, _FERest)) and re.match(r"^[+-]", str(a))), None)
        if tgt is None or pt is None or rest is None:
            return None
        if dur is None or dur.strip().lower() != "until end of turn":
            return None                        # `_boost_foreach` matches only 'until end of turn'
        if "x" in pt.lower():
            return None                        # regex P/T is digits-only ([+-]\d+/[+-]\d+) -> X-deltas abstain
        t = tgt.strip().lower()
        if _MULTICLAUSE.search(t) or "," in t:
            return None                        # mirror boost's subject guards (greedy mtgt over-capture)
        return Effect("modify_pt", pt.replace(" ", "") + "_per_" + ground.slug(rest.strip()),
                      _target(t), "until_end_of_turn")

    def ccreator(self, *toks):
        return _Creator(" ".join(str(t) for t in toks))

    def cspec(self, *toks):
        return _Spec(" ".join(str(t) for t in toks))

    def cfeword(self, tok):
        return _FEWord(str(tok))

    def cforeach(self, _fe, word):
        return word                              # pass the _FEWord up (the FOREACH literal is dropped)

    def ctail(self, *toks):
        return _CTail(" ".join(str(t) for t in toks))

    def cpobj(self, *toks):
        return _CTail(" ".join(str(t) for t in toks))   # raw span (unused — src is re-matched); reuse _CTail

    def create_copy(self, *args):
        # '[<creator>] create[s] [N] token(s) that's a copy of <X>[, except <mods>]' — the EXACT `_create_copy`
        # template. Re-match src against its pattern and reproduce the tuple byte-for-byte (amount / extra /
        # creator-cond); a non-match (e.g. cpobj swallowed a run-on) abstains to the regex chain.
        src = getattr(self, "_src", None)
        if src is None:
            return None
        m = _CCP_RE.match(src.strip())
        if not m:
            return None
        n = _amount(m.group(2))
        amt = n if n is not None else "X"
        extra = "copy_of_" + _target(m.group(3)) + ("_except_" + ground.slug(m.group(4)) if m.group(4) else "")
        creator = _target(m.group(1)) if m.group(1) and m.group(1).lower() != "you" else "-"
        cond = "creator_" + creator if creator != "-" else "-"
        return Effect("create", amt, "token", extra, cond)

    def create_copy_of(self, *args):
        # 'create [N] copy/copies of <X>[, except <mods>]' (elided 'token that's') — the EXACT `_create_copy_of`
        # template re-matched on src: ground.slug (NOT _target) the copied object, abstain on a compound object,
        # 4-arg Effect (cond '-'). A non-match (creator-prefixed 'creates …', run-on) abstains to the regex chain.
        src = getattr(self, "_src", None)
        if src is None:
            return None
        m = _CCPOF_RE.match(src.strip())
        if not m or _is_compound_object(m.group(2)):
            return None
        n = _amount(m.group(1))
        amt = n if n is not None else "X"
        extra = "copy_of_" + ground.slug(m.group(2)) + ("_except_" + ground.slug(m.group(3)) if m.group(3) else "")
        return Effect("create", amt, "token", extra)

    def create_conj(self, *args):
        # 'create <c1> <spec1> token(s) and <c2> <spec2> token(s)' — the AST conjunction. Two token NPs
        # sharing one 'create' -> two create effects, each in the base `create` shape (amount=_amount(count),
        # extra=slug(spec), cond=creator). Returns a LIST. Mirrors the single `create` for the SIMPLE form
        # (no per-token 'with <kw>'/'named'/'tapped' modifier — those don't reach this production and stay
        # with `create`); abstains on a non-player creator or an odd spec (token/copy/number-of), as create.
        creator = next((a for a in args if isinstance(a, _Creator)), None)
        specs = [str(a) for a in args if isinstance(a, _Spec)]
        counts = [str(a).lower() for a in args
                  if not isinstance(a, (_Creator, _Spec, _FEWord, _CTail))
                  and re.fullmatch(r"(?:a|an|one|two|three|four|five|six|seven|eight|nine|ten|x|[0-9]+)",
                                   str(a).lower())]
        if len(specs) != 2 or len(counts) != 2:
            return None
        cond = "-"
        if creator is not None:
            c = creator.strip().lower()
            if c != "you":
                if not _PLAYER.match(c):
                    return None
                cond = "creator_" + _target(c)
        out = []
        for cnt, spec in zip(counts, specs):
            if re.search(r"\b(?:token|tokens|copy|copies)\b", spec.lower()) or spec.lower().startswith("number of"):
                return None
            n = _amount(cnt)
            out.append(Effect("create", n if n is not None else cnt, "token", ground.slug(spec), cond))
        return out

    def create(self, *args):
        src = getattr(self, "_src", None)
        if src is not None and (wm := _CWHEREX_RE.match(src.strip())):
            # 'create X <spec> tokens[, with <kw>], where X is <Y>' — count IS X (regex grounds 'X' lossily).
            # Ground amount='equal_to_<Y>' (faithful); spec/creator reproduce the base create exactly.
            cre = wm.group(1)
            if cre and cre.lower() != "you" and not _PLAYER.match(cre.lower()):
                return None                          # non-player creator phrase -> regex chain owns it
            cond = ("creator_" + _target(cre)) if (cre and cre.lower() != "you") else "-"
            return Effect("create", "equal_to_" + ground.slug(wm.group(3)), "token", ground.slug(wm.group(2)), cond)
        creator = next((a for a in args if isinstance(a, _Creator)), None)
        spec = next((str(a) for a in args if isinstance(a, _Spec)), None)
        fe = next((str(a) for a in args if isinstance(a, _FEWord)), None)
        count = next((str(a).lower() for a in args
                      if not isinstance(a, (_Creator, _Spec, _FEWord, _CTail))
                      and re.fullmatch(r"(?:a|an|one|two|three|four|five|six|seven|eight|nine|ten|x|[0-9]+)",
                                       str(a).lower())), None)
        that_many = any(str(a).lower() == "that many" for a in args)   # anaphoric §107.3 'create THAT MANY <spec> tokens'
        if spec is None or (count is None and not that_many):
            return None
        sl = spec.lower()
        # 'create a number of <spec> tokens equal to <X>' is the count-scaled _create_equal template (the
        # count word is 'a' and the spec starts 'number of …'); reproduce it from src. _create_equal only
        # matches a 'you'/implicit creator, so a non-you creator still abstains here (regex chain owns it).
        if sl.startswith("number of"):
            src = getattr(self, "_src", None)
            if src is not None:
                m = _CEQ_RE.match(src.strip())
                if m:
                    return Effect("create", "equal_to_" + ground.slug(m.group(2)), "token", ground.slug(m.group(1)))
            return None
        # 'create a token that's a copy of …' / 'X tokens that are copies of …' is the _create_copy
        # template; here cspec greedily ran past the real boundary to a LATER 'token' (e.g. 'artifact
        # token you control') and would emit a garbled spec — defer to the regex copy handler.
        if re.search(r"\b(?:token|tokens|copy|copies)\b", sl):
            return None
        # A comma or 'then' in the trailing remainder signals a multi-token list ('… token, a 2/2 …')
        # or a sequenced second effect ('… token, then draw …') — the regex crams the whole run into
        # the spec (lossy), and lark dropping it would drop conjuncts. Abstain: regex chain owns these.
        tail = next((str(a) for a in args if isinstance(a, _CTail)), "")
        tail_uq = re.sub(r'"[^"]*"', "", tail)   # a comma/'then' INSIDE a granted quoted ability ('token with
        if "," in tail_uq or re.search(r"\bthen\b", tail_uq):   # "When ~ dies, …"') is not a clause separator
            return None                          # — mask quoted spans first, then detect a real multi-token list
        # CREATOR: closed player allow-list (reuse _PLAYER) — a greedy non-player prefix -> abstain.
        cre = None
        if creator is not None:
            c = creator.strip().lower()
            if c == "you":
                cre = None                       # 'you' is the default controller (regex: cond='-')
            elif _PLAYER.match(c):
                cre = c
            else:
                return None                      # non-player creator phrase -> regex chain owns it
        if that_many:
            amt = "that_amount"                   # FAITHFUL anaphoric count — the regex grounds this lossily
        else:                                     # (amount='X' + 'many_' leaked into the spec via _create_token)
            n = _amount(count)
            amt = n if n is not None else "X"     # _amount('x') -> 'X'; digits/number words -> int
            if fe is not None:
                amt = f"{amt}_per_{ground.slug(fe)}"  # regex keeps only the first word of 'for each X'
        cond = ("creator_" + _target(cre)) if cre else "-"
        return Effect("create", amt, "token", ground.slug(spec), cond)

    def gtgt(self, *toks):
        return _Tgt(" ".join(str(t) for t in toks))

    def gchrest(self, *toks):
        return _GchRest(" ".join(str(t) for t in toks))

    def grant_choice(self, *args):
        # '<tgt> gains your choice of <kw>, <kw>, or <kw> [until end of turn]' — §700.2 keyword choice.
        # Split the list on ',' / 'or' / 'and'; EVERY option must be a §702 keyword (_kw_ok) or abstain. ->
        # grant_keyword(-, tgt, 'choice_<kw>_or_<kw>…' [, until_end_of_turn]). Faithful: no option dropped.
        tgt = next((str(a) for a in args if isinstance(a, _Tgt)), None)
        rest = next((str(a) for a in args if isinstance(a, _GchRest)), None)
        dur = next((str(a) for a in args if isinstance(a, _Dur)), None)
        if rest is None:
            return None
        parts = [p.strip() for p in re.split(r",|\bor\b|\band\b", rest.lower()) if p.strip()]
        kws = [_kw_ok(p) for p in parts]
        if len(kws) < 2 or not all(kws):
            return None                       # need ≥2 grounded §702 options, else abstain (regex/other owns it)
        who = _target(tgt.strip().lower()) if tgt else "self"
        cond = "until_end_of_turn" if dur else "-"
        return Effect("grant_keyword", "-", who, "choice_" + "_or_".join(kws), cond)

    def gkw(self, *toks):
        return _Body(" ".join(str(t) for t in toks))     # reuse _Body marker for the keyword phrase

    def _grant_dur(self, tgt, dur):
        """Resolve the (amount, cond, target-text) for a grant, handling the EOT duration and a trailing
        'perpetually' adverb (cond=perpetual, mirroring `boost`). Returns None on an unsupported/ambiguous
        duration so the regex fallback owns it; else (amount, cond, tgt_text)."""
        t = (tgt.strip() if tgt else "")
        perpetual = t.lower().endswith(" perpetually")
        if perpetual:
            t = t[:-len(" perpetually")].strip()       # '<X> perpetually gains …' -> cond=perpetual
        amount, cond = "-", "-"
        if dur is not None:
            d = dur.strip().lower()
            # duration -> amount slot. 'until end of turn' is unchanged (was already faithful); the non-EOT
            # durations the regex slugs INTO the kw (lossy: 'banding_until_end_of_combat') ground faithfully
            # here as amount='until_end_of_combat'/'until_your_next_turn'/… with a clean kw. 'this turn' is
            # omitted (its faithful slug is ambiguous vs until_end_of_turn) -> regex chain owns it.
            if perpetual or d not in ("until end of turn", "until end of combat",
                                      "until your next turn", "until end of your next turn"):
                return None
            amount = ground.slug(d)
        elif perpetual:
            cond = "perpetual"
        return amount, cond, t

    def grant(self, *args):
        # CAN'T-GAIN-LIFE restriction (§119): '<player-set> can't gain life [<duration>]'. The dynamic lexer
        # WORD-tokenizes "can't" when NS_CANT yields no parse, so this routes to gclause/grant (gain∈GVERB),
        # NOT nscant — hence it's grounded HERE rather than in the nscant chain. cant_gain_life(-, subj,
        # cond=<duration>). A compound ('… or remove poison counters') is a conflation -> defer to the normal
        # grant logic (which abstains), keeping faithful-or-abstain.
        src0 = getattr(self, "_src", None)
        if src0 is not None:
            gm = _GAINLIFE_RESTR.match(src0.strip())
            if gm and not re.search(r"\bor\b|\band\b|,", gm.group(2) or ""):
                qual = (gm.group(2) or "").strip()
                return Effect("cant_gain_life", "-", _target(gm.group(1)), "-",
                              ground.slug(qual) if qual else "-")
        tgt = next((str(a) for a in args if isinstance(a, _Tgt)), None)
        phrase = next((str(a) for a in args if isinstance(a, _Body)), None)
        dur = next((str(a) for a in args if isinstance(a, _Dur)), None)
        if phrase is None:
            return None
        # 'gain(s)' dynamic life amounts (§119) — 'N life for each X' / 'life equal to X' / '[twice|half]
        # that much life [plus N]'. These route to gclause (gain ∉ PVERB) but the plain-life branch below
        # only handles a bare numeric amount; reproduce `_gain_foreach`/`_gain_life_equal`/`_that_gain` from src.
        src = getattr(self, "_src", None)
        if src is not None:
            s = src.strip()
            m = _GAIN_ALL_CT_RE.match(s)       # '<X> gains all creature types [eot]' -> becomes (§205, _gain_all_creature_types)
            if m:
                return Effect("becomes", "-", _target(m.group(1)), "every_creature_type")
            m = _HAVE_DRAW_RE.match(s)         # '[then] <player> may have you draw N cards' (§603 causative) —
            if m:                             # FAITHFUL: YOU are the drawer; the player just directs it (extra)
                amt = m.group(2)
                n = 1 if amt in ("a", "a card") else _amount(amt)
                if n is not None:
                    return Effect("draw", n, "you", "by_" + _target(m.group(1)))
            m = _GFE_RE.match(s)
            if m:
                n = _amount(m.group(2))
                amt = (str(n) if n is not None else ground.slug(m.group(2))) + "_per_" + ground.slug(m.group(3))
                return Effect("gain_life", amt, _target(m.group(1) or "you"))
            m = _GLE_RE.match(s)
            if m:
                return Effect("gain_life", "equal_to_" + ground.slug(m.group(2)), _target(m.group(1) or "you"))
            m = _GTM_RE.match(s)
            if m:
                return Effect("gain_life", _that_amt(m.group(2), m.group(3)), _target(m.group(1) or "you"))
            m = _GAIN_TGT_RE.match(s)         # '<player NP> gains N life' — explicit _TGT subject the plain
            if m and not _PLAYER.match(m.group(1).strip()) and not _compound_subj(m.group(1)):   # broad/rel-clause NP,
                                              # NOT a '<clause> and you gain N life' run-on (the plain branch below)
                n = _amount(m.group(2))       # reproduce `_gain` byte-for-byte (plain _PLAYER subjects below)
                if n is not None:
                    return Effect("gain_life", n, _target(m.group(1)))
        # 'gain(s) <amount> life' is gain_life — the 'gain(s)' verb is now owned by this rule (removed
        # from PVERB to kill the pcount<->grant ambiguity). Reproduce pcount's gain_life tuple exactly;
        # abstain on a duration/perpetual or a non-player subject (pcount's domain handles only those).
        toks = phrase.strip().lower().split()
        if toks and toks[-1] == "life":
            amt_s = " ".join(toks[:-1]).strip()
            n = 1 if amt_s in ("a", "an") else (None if amt_s == "" else _amount(amt_s))
            if n is None or dur is not None:
                return None
            t = tgt.strip().lower() if tgt else ""
            if t and (t.endswith(" perpetually") or _TGT_BAD.search(t) or not _PLAYER.match(t)):
                return None
            return Effect("gain_life", n, _target(t) if t else "you")
        # 'gain(s) control of <X> [until end of turn | for as long as …]' is §720 gain_control. The
        # 'gain'/'gains' verb routes here (removed from PVERB), and gkw greedily captures 'control of …'
        # (so `_kw_ok` returns None below) — own that shape here, reproducing `_control`/`_control_subj`.
        if toks and toks[0] == "control":
            verb = next((str(a).lower() for a in args if not isinstance(a, (_Tgt, _Body, _Dur))), "")
            obj_full = " ".join(toks[1:])
            if obj_full.startswith("of "):                # '(?:of )?' in the regex
                obj_full = obj_full[3:]
            if dur is not None:
                obj_full = (obj_full + " " + dur.strip()).strip()   # fold MDUR back; gc-split re-peels
            obj, dur_extra = _gc_dursplit(obj_full)
            if not obj or not _GC_TGT.match(obj):
                return None                              # object isn't a clean <TGT> noun phrase -> abstain
            tt = tgt.strip().lower() if tgt else None
            # SHAPE A ('_control'): implicit/you subject, no 'by_'. The regex's frame is
            # '(?:~ |you )?(?:gain )?control …' — the verb token, when present, is the LITERAL 'gain' (NOT
            # 'gains'); 'gains' only appears in _control_subj, which REQUIRES a subject. So SHAPE A is reached
            # only when the verb is exactly 'gain' (or absent), AND either there is no subject or the subject
            # is exactly 'you'. A no-subject 'gains control of …' matches NEITHER regex template (wrong verb
            # for _control, no subject for _control_subj) -> abstain, instead of over-claiming as the regex
            # would return None. ('you gains …' likewise falls through to SHAPE B / abstain.)
            if verb in ("", "gain") and (tt is None or tt == "you"):
                return Effect("gain_control", "-", _target(obj), dur_extra)
            # SHAPE B ('_control_subj'): '<subject> gains? control of <X> [dur]' -> extra='by_<subject>',
            # cond=duration. The gaining player must be a clean §720 controller phrase — `_PLAYER` (a
            # closed allow-list) rejects a greedy gtgt that swallowed a run-on coordinated imperative
            # ('untap … and'), a 'may' wrapper, or a compound 'X and Y each' subject (all of which the
            # regex leaf would ground as a DIFFERENT verb or not at all) -> abstain. (Faithful: a couple
            # of exotic but real subjects — 'target opponent chosen at random' — also fall here; the
            # regex fallback still owns them.)
            if tt is None or not _PLAYER.match(tt):
                return None                              # _control_subj REQUIRES a subject -> none here -> abstain
            return Effect("gain_control", "-", _target(obj), "by_" + _target(tt), dur_extra)
        # CONDITIONAL static grant: '<X> has/gains <kw> [for ]as long as <cond>'. gkw swallows the whole
        # '<kw> as long as <cond>' run, so the regex slugs it ALL into the keyword (garbage
        # 'flying_as_long_as_…', cond='-'). Split it: a clean §702 keyword in the extra slot, the condition
        # in the COND slot ('as_long_as_<cond>', mirroring modify_pt's 'for_as_long_as_<cond>'). The kw
        # becomes a REAL keyword the engine can read; cond gates the static (cond_met — may be inert for now).
        clm = re.match(r"^(.+?) (for as long as|as long as) (.+)$", phrase.strip(), re.I)
        if clm:
            ckw = _kw_ok(clm.group(1).strip())
            if ckw and _clean_kw(ckw):
                cres = self._grant_dur(tgt, dur)
                if cres is not None:
                    camount, _c0, cttext = cres
                    if not (cttext and _TGT_BAD.search(cttext)):
                        cwho = _target(cttext) if cttext else _target("~")
                        ccond = ground.slug(clm.group(2)) + "_" + ground.slug(clm.group(3))
                        return Effect("grant_keyword", camount, cwho, ckw, ccond)
        kw = _kw_ok(phrase.strip())
        if not kw or not _clean_kw(kw):
            return None                          # not a clean single §702 keyword grant -> abstain
        res = self._grant_dur(tgt, dur)
        if res is None:
            return None
        amount, cond, ttext = res
        if ttext and _TGT_BAD.search(ttext):
            return None                          # target carries clause material (over-match) -> abstain
        who = _target(ttext) if ttext else _target("~")
        return Effect("grant_keyword", amount, who, kw, cond)

    def grant_ab(self, *args):
        tgt = next((str(a) for a in args if isinstance(a, _Tgt)), None)
        quoted = next((str(a) for a in args
                       if not isinstance(a, (_Tgt, _Dur)) and str(a).startswith('"')), None)
        dur = next((str(a) for a in args if isinstance(a, _Dur)), None)
        if tgt is None or quoted is None or len(quoted) < 2:
            return None
        res = self._grant_dur(tgt, dur)
        if res is None:
            return None
        amount, cond, ttext = res
        if not ttext or _TGT_BAD.search(ttext):
            return None                          # empty / clause-laden target -> abstain
        ab = ground.slug(quoted[1:-1])[:160]      # strip the surrounding quotes (regex '"(.+)"')
        if not ab:
            return None
        return Effect("grant_ability", amount, _target(ttext), ab, cond)

    def psubj(self, *toks):
        return _Subj(" ".join(str(t) for t in toks))

    def pbody(self, *toks):
        return _Body(" ".join(str(t) for t in toks))

    @staticmethod
    def _lose_abilities_eff(subj, body):
        # '<permanent> loses all [other] abilities / this ability / <kw-list> [until end of turn]' (§613.6) —
        # the EXACT `_lose_abilities` / `_lose_specific` templates: lose_abilities(-, _target(subj), <extra>)
        # where extra is '-' (all abilities), 'this_ability', or the '_'-joined §702 keyword list. The
        # subject must be a clean `_TGT`; a kw-list that isn't ALL §702 keywords (e.g. an 'or' list) abstains.
        if subj is None:
            return None
        s = subj.strip()
        if not _AT_TGT.match(s):
            return None
        b = re.sub(r"\s+until end of turn$", "", body.strip(), flags=re.I).strip().lower()
        if re.match(r"^all(?: other)? abilities$", b):
            return Effect("lose_abilities", "-", _target(s))
        if b == "this ability":
            return Effect("lose_abilities", "-", _target(s), "this_ability")
        kws = _kw_list(b)
        return Effect("lose_abilities", "-", _target(s), "_".join(kws)) if kws else None

    def pcount(self, *args):
        subj = next((str(a) for a in args if isinstance(a, _Subj)), None)
        body = next((str(a) for a in args if isinstance(a, _Body)), "")
        verb = next((str(a).lower() for a in args if not isinstance(a, (_Subj, _Body))), "")
        g = _PVERB.get(verb)
        if g is None:
            return None
        if verb in ("loses", "lose"):          # §613.6 ABILITY removal routes here via PVERB 'lose[s]' but
            la = self._lose_abilities_eff(subj, body)   # the subject is a PERMANENT and the body is abilities,
            if la is not None:                 # not a player-count amount -> handle before the player gate
                return la
        if verb in ("draw", "draws"):          # DRAW '<N> cards for each <X>' count-scaled amount (§613) —
            src = getattr(self, "_src", None)  # routes here via PVERB but the body is a per-X amount; reproduce
            if src is not None and "for each" in src.lower():   # `_draw_foreach` (its own _TGT subject) exactly
                fm = _DFE_RE.match(src.strip())
                if fm:
                    g2 = fm.group(2)
                    base = "1" if g2 in ("a", "a card") else (str(_amount(g2)) if _amount(g2) is not None else ground.slug(g2))
                    return Effect("draw", base + "_per_" + ground.slug(fm.group(3)), _target(fm.group(1) or "you"))
        if verb in ("draw", "draws", "mill", "mills"):   # DRAW/MILL 'that many cards' anaphoric amount (§107.3)
            src = getattr(self, "_src", None)            # — reproduce `_{draw,mill}_that_many` ([twice|half] that
            if src is not None:                          # many [plus|minus N]). BEFORE _FLOW_RE: regex tries
                m = _MTM_RE.match(src.strip())           # the *_that_many templates ahead of _flow_amount.
                if m:
                    rv = "draw" if m.group(2).lower().startswith("draw") else "mill"
                    return Effect(rv, _that_amt(m.group(3), m.group(4)), _target(m.group(1) or "you"))
        if verb in ("draw", "draws"):          # DRAW '<N> additional cards' (§120) — `_draw_additional`: the
            src = getattr(self, "_src", None)  # 'additional' modifier the body strip can't number; reproduce it
            if src is not None and "additional" in src:
                am = _DADD_RE.match(src.strip())
                if am:
                    n = _amount(am.group(2))
                    return Effect("draw", n if n is not None else 1, _target(am.group(1) or "you"), "additional")
        if verb in ("draw", "draws", "mill", "mills"):   # DRAW/MILL dynamic amount-expr (§120/§614) —
            src = getattr(self, "_src", None)            # 'up to N' / 'cards equal to X' / 'as many cards
            if src is not None:                          # as X' / 'half [of] X'; reproduce `_flow_amount`
                fm = _FLOW_RE.match(src.strip())
                if fm:
                    expr = fm.group(3).strip()
                    mm = re.match(r"up to (\w+) cards?$", expr, re.I)
                    if mm:
                        n = _amount(mm.group(1))
                        amt = "up_to_" + (str(n) if n is not None else ground.slug(mm.group(1)))
                    elif re.match(r"cards? equal to ", expr, re.I):
                        amt = "equal_to_" + ground.slug(re.sub(r"^cards? equal to ", "", expr, flags=re.I))
                    elif re.match(r"as many cards as ", expr, re.I):
                        amt = "as_many_as_" + ground.slug(re.sub(r"^as many cards as ", "", expr, flags=re.I))
                    else:
                        amt = "half_" + ground.slug(re.sub(r"^half(?: of)? ", "", expr, flags=re.I))
                    rv = "draw" if fm.group(2).lower().startswith("draw") else "mill"
                    return Effect(rv, amt, _target(fm.group(1) or "you"))
        if verb in ("lose", "loses"):                    # LOSE life dynamic amounts (§119) — 'life equal to
            src = getattr(self, "_src", None)            # X' / 'half [poss] life[, rounded]' / '[twice|half]
            if src is not None:                          # that much life [plus N]'; reproduce `_lose_*` exactly
                s = src.strip()
                m = _LHF_RE.match(s)
                if m:
                    amt = "half" + ("_rounded_" + m.group(2) if m.group(2) else "")
                    return Effect("lose_life", amt, _target(m.group(1) or "you"))
                m = _LLE_RE.match(s)
                if m:
                    return Effect("lose_life", "equal_to_" + ground.slug(m.group(2)), _target(m.group(1) or "you"))
                m = _LTM_RE.match(s)
                if m:
                    return Effect("lose_life", _that_amt(m.group(2), m.group(3)), _target(m.group(1) or "you"))
        if verb in ("discard", "discards"):              # DISCARD whole-hand / referenced-set (§701.8) —
            src = getattr(self, "_src", None)            # reproduce `_discard_hand` / `_discard_set` (the body
            if src is not None:                          # is 'your hand'/'their hand'/'those cards'/…, not 'N cards')
                s = src.strip()
                if _DH_RE.match(s):
                    return Effect("discard", "all", "you")
                if _DAYH_RE.match(s):           # 'discard all the cards in your hand' — generic-leaf shape:
                    return Effect("discard", "-", _target("all the cards in your hand"))   # whole phrase -> TARGET
                m = _DSET_RE.match(s)
                if m:
                    return Effect("discard", "-", _target(m.group(1) or "you"), ground.slug(m.group(2)))
                m = _DTM_RE.match(s)         # '[twice|half] that many cards [plus|minus N] [at random]'
                if m:
                    return Effect("discard", _that_amt(m.group(2), m.group(3)), _target(m.group(1) or "you"))
                m = _DOFT_RE.match(s)        # 'discard <N> of them/those/these [cards]' — the loot partitive
                if m:
                    n = _amount(m.group(2))
                    if n is not None:
                        return Effect("discard", n, _target(m.group(1) or "you"), "of_them")
        if subj is not None and not _PLAYER.match(subj.strip()):
            if verb in ("draw", "draws"):      # `_draw_tgt`: a draw whose subject is a _TGT the player-gate
                _src = getattr(self, "_src", None)  # rejects (quantified players / 'each player who …'); the
                dm = _DRAW_TGT_RE.match(_src.strip()) if _src is not None else None   # regex grounds it here too
                if dm and not _compound_subj(dm.group(1) or dm.group(3) or ""):   # not a '<clause> and you draws' run-on
                    who = dm.group(1) if dm.group(1) else dm.group(3)
                    amt = dm.group(2) if dm.group(1) else dm.group(4)
                    n = 1 if amt in ("a", "a card") else _amount(amt)
                    if n is not None:
                        return Effect("draw", n, _target(who))
            if verb in ("lose", "loses"):      # `_lose`: a lose_life whose subject is a _TGT the player-gate
                _src = getattr(self, "_src", None)   # rejects ('each opponent who can't loses N life'); the
                lm = _LOSE_TGT_RE.match(_src.strip()) if _src is not None else None   # regex grounds it here too
                if lm and not _compound_subj(lm.group(1)):   # not a '<clause> and <subj> loses N life' run-on
                    n = _amount(lm.group(2))
                    if n is not None:
                        return Effect("lose_life", n, _target(lm.group(1)))
            if verb in ("mill", "mills"):      # `_mill`: a mill whose subject is a _TGT the player-gate rejects
                _src = getattr(self, "_src", None)   # ('any number of target players each mill N cards')
                mm = _MILL_TGT_RE.match(_src.strip()) if _src is not None else None
                if mm and not _compound_subj(mm.group(1)):   # not a '<clause> and <subj> mills N cards' run-on
                    n = 1 if mm.group(2) == "a card" else _amount(mm.group(2))
                    if n is not None:
                        return Effect("mill", n, _target(mm.group(1)))
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

    def csubj(self, *toks):
        return _CSubj(" ".join(str(t) for t in toks))

    def ccount(self, tok):
        return _CCount(str(tok))

    def ckwords(self, *toks):
        return _CKind(" ".join(str(t) for t in toks))

    def ckind(self, tok):
        # PTDELTA arrives as a raw Token (no ckwords reduction); ckwords arrives already wrapped.
        return tok if isinstance(tok, _CKind) else _CKind(str(tok))

    def ctarget(self, *toks):
        return _CTarget(" ".join(str(t) for t in toks))

    def putctr_conj(self, *args):
        # 'put <c1> <k1> counter(s) and <c2> <k2> counter(s) on <tgt>' — the AST conjunction. Two counter
        # NPs (count_i, kind_i) sharing one target -> two put_counter effects, each in the base putctr shape
        # (amount=_amount(count), extra=<+P/+T verbatim | slug(keyword)>). Returns a LIST. Subject (a player
        # before 'put') is DROPPED, but only when it's a clean player phrase, else abstain (like putctr).
        counts = [str(a) for a in args if isinstance(a, _CCount)]
        kinds = [str(a) for a in args if isinstance(a, _CKind)]
        tgt = next((str(a) for a in args if isinstance(a, _CTarget)), None)
        subj = next((str(a) for a in args if isinstance(a, _CSubj)), None)
        if len(counts) != 2 or len(kinds) != 2 or tgt is None:
            return None
        if subj is not None:
            s = subj.strip().lower()
            s = s[:-4].strip() if s.endswith(" may") else s
            if not _PLAYER.match(s):
                return None
        t = _target(tgt.strip().lower())
        out = []
        for cnt, knd in zip(counts, kinds):
            knd = knd.strip()
            kl = knd.lower()
            if re.search(r"\bcounters?\b", kl) or re.search(r"\b(?:into|onto|battlefield|graveyard|library|hand)\b", kl):
                return None                       # a re-lexed counter/zone word -> mis-split, abstain (as putctr)
            n = _amount(cnt.strip().lower())
            k = knd if "/" in knd else ground.slug(knd)
            out.append(Effect("put_counter", n if n is not None else "-", t, k))
        return out

    def putctr(self, *args):
        subj = next((str(a) for a in args if isinstance(a, _CSubj)), None)
        count = next((str(a) for a in args if isinstance(a, _CCount)), None)
        kind = next((str(a) for a in args if isinstance(a, _CKind)), None)
        tgt = next((str(a) for a in args if isinstance(a, _CTarget)), None)
        if count is None or kind is None or tgt is None:
            return None
        # 'put a number of <kind> counters on <obj> equal to <X>' (§122 count-scaled) — the 'a number of'
        # kind/count makes the span logic below abstain (L2103/L2108); reproduce `_put_counter_equal` from src.
        src = getattr(self, "_src", None)
        if src is not None:
            m = _PCE_RE.match(src.strip())
            if m:
                if _is_compound_object(m.group(2)):
                    return None
                k = m.group(1) if "/" in m.group(1) else ground.slug(m.group(1))
                return Effect("put_counter", "equal_to_" + ground.slug(m.group(3)), _target(m.group(2)), k)
            # 'put X <kind> counters on <obj>, where X is <Y>' — the regex crams the where-clause into the
            # target and leaves amount='X'; ground amount='equal_to_<Y>' with a CLEAN target (faithful).
            wm = _PCWX_RE.match(src.strip())
            if wm and not _is_compound_object(wm.group(2)):
                k = wm.group(1) if "/" in wm.group(1) else ground.slug(wm.group(1))
                return Effect("put_counter", "equal_to_" + ground.slug(wm.group(3)), _target(wm.group(2)), k)
            # 'put <n> <kind> counter(s) on <obj> for each <Y>' (§107.3) — the regex crams ' for each <Y>'
            # into the target and keeps amount=<n>; ground '<n>_per_<Y>' (draw/gain convention) + clean target.
            fm = _PCFE_RE.match(src.strip())
            if fm and not _is_compound_object(fm.group(3)):
                n = _amount(fm.group(1))
                base = str(n) if n is not None else ground.slug(fm.group(1))
                k = fm.group(2) if "/" in fm.group(2) else ground.slug(fm.group(2))
                return Effect("put_counter", base + "_per_" + ground.slug(fm.group(4)), _target(fm.group(3)), k)
            # 'put that many <kind> counters on <obj>' — carried count. amount='that_amount' (draw convention)
            # + clean kind; the regex botches word kinds ('many_vitality'/'X'). +1/+1 stays byte-identical.
            tm = _PCTM_RE.match(src.strip())
            if tm and not _is_compound_object(tm.group(2)):
                k = tm.group(1) if "/" in tm.group(1) else ground.slug(tm.group(1))
                return Effect("put_counter", "that_amount", _target(tm.group(2)), k)
        # SUBJECT (regex's non-capturing '(?:<TGT> )?puts?' — DROPPED). Only own a clean player phrase;
        # a compound/wrapper subject ('each player chooses … and puts', 'may') -> abstain to the regex.
        if subj is not None:
            s = subj.strip().lower()
            if s.endswith(" may"):
                s = s[:-4].strip()                # the 'may' wrapper is stripped above the leaf in prod
            if not _PLAYER.match(s):
                return None
        count = count.strip().lower()
        kind = kind.strip()
        tgt = tgt.strip().lower()
        # The dynamic lexer can still re-lex a 'counter'/'into'/'onto' as a bare WORD inside the kind or
        # target span. The regex binds the FIRST 'counter on', so any such re-crossing means we mis-split
        # a multi-counter list ('your choice of a vigilance counter, …'), or a zone-move ('… counters on
        # them into your hand', '… onto the battlefield … counter on it') — abstain, the regex owns those.
        kl = kind.lower()
        if re.search(r"\bcounters?\b", kl) or re.search(r"\b(?:into|onto|battlefield|graveyard|library|hand)\b", kl):
            return None
        if "," in kind:
            return None                           # regex kind span ([\w ]+?) is comma-free -> abstain on lists
        if re.search(r"\b(?:into|onto)\b", tgt):
            return None
        # KIND: a P/T delta is kept verbatim (with the '/'); else slug the word(s). Regex's PTDELTA is
        # digits-only ([+-]\d+/[+-]\d+), so '+x/+x' falls through to its [\w ]+? branch and FAILS to
        # match -> abstain to stay faithful. A 'number of'/'same number' kind is the equal-to/copy form.
        if "/" in kind:
            if re.search(r"[a-z]", kind):
                return None                       # '+x/+x' etc. — regex abstains here
            kind_slug = kind
        else:
            if kind.startswith("number of") or "same number" in kind:
                return None                       # 'put a number of … equal to' / copy form -> regex
            kind_slug = ground.slug(kind)
        # COUNT -> amount, faithful to `_put_counter`/`_put_counter_many`. The regex count is a SINGLE
        # word (or 'up to <word>'); a multi-word QUANT ('any number of') the regex can't ground -> abstain.
        if " " in count and not count.startswith("up to ") and count != "that many":
            return None
        if count == "that many":
            if "/" not in kind:
                return None                       # 'that many <named>' -> regex's lossy 'many_<kind>'/X path
            amt = "that_amount"
        elif count.startswith("up to "):
            rest = count[6:].strip()
            a = _amount(rest)
            if a is None:
                return None                       # 'up to that many' etc. — regex can't ground it
            amt = "up_to_" + str(a)
        else:
            a = _amount(count)
            amt = a if a is not None else "X"
        # TARGET: a run-on object ('… and gain control of it') is a second effect; abstain so the
        # splitter owns it (faithful to `_put_counter`'s `_is_compound_object` guard). Also abstain on
        # a following clause the leaf must NOT swallow: a 'then'/'deals N damage' sequencer (the regex's
        # ordered templates ground that as the OTHER verb — `_MULTICLAUSE`), an ' and it <verb>' run-on
        # that `_is_compound_object` misses ('it' isn't a predicate-lead there), a ', where X is …' /
        # 'for each …' scaling appendix, and an ' or remove … counter' alternative.
        if _MULTICLAUSE.search(tgt) or _is_compound_object(tgt):
            return None
        if re.search(r"\band (?:it|they|you) \w", tgt):   # ' and it deals …' — a new clause the leaf split owns
            return None
        if ", where " in tgt or tgt.endswith(", where") or "for each " in tgt or " or remove " in tgt:
            return None
        return Effect("put_counter", amt, _target(tgt), kind_slug)

    # --- REMOVE_COUNTER (mirror of putctr) ------------------------------------
    def rccount(self, tok):
        return _RcCount(str(tok))

    def rckwords(self, *toks):
        return _RcKind(" ".join(str(t) for t in toks))

    def rckind(self, tok):
        # PTDELTA arrives as a raw Token (no rckwords reduction); rckwords arrives already wrapped.
        return tok if isinstance(tok, _RcKind) else _RcKind(str(tok))

    def rctarget(self, *toks):
        return _RcTarget(" ".join(str(t) for t in toks))

    def rcremove(self, *args):
        # 'remove <count> [<kind>] counter[s] from <tgt>' — the EXACT `_remove_counter` template. The
        # grammar gives us the COUNT, optional KIND, and TARGET as distinct spans (anchored by the
        # structural COUNTER/FROM terminals); the transformer reads them off the tree and reproduces the
        # template's count/kind/target grounding line-for-line. The target span is certified by the
        # anchored `_TGT` operand validator `_RC_TGT` (template g3): a target outside `_TGT` abstains
        # exactly as the template did -> the regex fallback owns the whole clause (byte-identical-or-abstain).
        count = next((str(a) for a in args if isinstance(a, _RcCount)), None)
        kind = next((str(a) for a in args if isinstance(a, _RcKind)), None)
        tgt = next((str(a) for a in args if isinstance(a, _RcTarget)), None)
        if count is None or tgt is None:
            return None
        tgt = tgt.strip()
        if not _RC_TGT.match(tgt):
            return None                          # target outside `_TGT` (template g3) -> abstain to regex
        # COUNT span vs. template g1 `(a|an|one|two|three|all|any number of|x|\w+)`: every g1 alternative
        # is a SINGLE word EXCEPT 'any number of'. The dynamic lexer can lex a multi-word QUANT ('up to
        # two') as ONE count token, but the template only ever took its FIRST word as g1 and folded the
        # remainder into the kind group `[\w ]+?` ('up to two' -> g1 'up', kind prefix 'to two'). Reproduce
        # that split so the tuple stays byte-identical (g1='up', kind='to two' -> slug 'to_two'; and 'up to
        # N <delta>' -> kind 'to N <delta>' which carries a '/' -> the kind guard below abstains, as g1 did).
        count = count.strip()
        if " " in count and count.lower() != "any number of":
            first, rest = count.split(" ", 1)
            count = first
            kind = _RcKind(rest + (" " + kind if kind is not None else ""))
        # COUNT -> amount (template g1): `_amount`, else 'all'/'any'/'X' (the template's own fallback).
        n = _amount(count)
        if n is None:
            g1 = count.lower()
            n = "all" if g1 == "all" else ("any" if g1 == "any number of" else "X")
        # KIND -> extra (template g2): absent -> '-'; a P/T delta verbatim; else slugged. The template's
        # kind group is `([+-]\d+/[+-]\d+|[\w ]+?)` — EITHER a pure P/T delta OR a `[\w ]+?` word run with
        # NO '/'+'-' chars. The dynamic lexer can fold a delta into a WORD inside a multi-word `rckwords`
        # ('that many +1/+1' -> rckwords 'many +1/+1'); that mixes a delta into a word run, which the
        # template can't ground -> abstain (a bare PTDELTA-only kind stays its own span and is fine).
        if kind is None:
            kind_slug = "-"
        else:
            kind = kind.strip()
            if "/" in kind:
                if not re.fullmatch(r"[+-]\d+/[+-]\d+", kind):
                    return None                  # delta mixed into a word run / non-numeric delta -> regex
                kind_slug = kind
            else:
                kind_slug = ground.slug(kind)
        return Effect("remove_counter", n, _target(tgt), kind_slug)

    # --- DOUBLE ---------------------------------------------------------------
    def dbbody(self, *toks):
        return _DbBody(" ".join(str(t) for t in toks))   # value unused; presence consumes the run

    def dbl(self, *args):
        # 'double <object>' -> double(-, slug(<object>)), faithful to the object-verb leaf. Slice the
        # object from the lowercased source (after the leading 'double '), apply the leaf's guards, and
        # mirror the `_verb_target`-then-`_generic_object_verb` precedence (a whole-`_TGT` object keeps
        # its article via `_target`; otherwise plain slug). They coincide on every corpus clause.
        src = getattr(self, "_src", None)
        if src is None:
            return None
        m = re.match(r"^double (.+)$", src.strip(), re.I)
        if not m:
            return None
        rest = m.group(1)
        if _DB_OBJ_BAD.search(rest) or _is_compound_object(rest):
            return None                            # compound/run-on or structural marker -> regex
        tgt = _target(rest) if _DB_TGT.match(rest) else ground.slug(rest)
        return Effect("double", "-", tgt)

    # --- NEGATIVE STATICS (cant_be_blocked / cant_block / doesnt_untap) -------
    def nssubj(self, *toks):
        return _NsSubj(" ".join(str(t) for t in toks))   # subject NP (template g1): nscant + nsuntap read it

    def nsverb(self, *toks):
        return _NsVerb(" ".join(str(t) for t in toks))   # verb phrase: nscant rejoins it with nstail as the REST span

    def nstail(self, *toks):
        return _NsTail(" ".join(str(t) for t in toks))   # tail span: nscant rejoins it onto nsverb; nsuntap reads it

    def nscant(self, *args):
        # "<subj> can't <verb> [<obj>] [this turn]" — the EXACT `_cant_combat`-then-`_cant_combat_set`
        # templates. The grammar split the clause at the structural "can't" terminal into a SUBJECT span
        # (`nssubj`, template g1) and the verb/tail; we read the subject and REJOIN the verb+tail into the
        # REST span (the template's post-"can't" portion). `_ns_cant` applies the two frames in template
        # ORDER over those spans (each as an anchored subject validator + a rest validator that captures the
        # verb and the optional `_TGT` object) — byte-identical to parse_effect, or abstain (an 'except by
        # …'/'this combat'/non-skeleton rest, a non-template subject, or a verb that isn't ours).
        subj = next((str(a) for a in args if isinstance(a, _NsSubj)), None)
        verb = next((str(a) for a in args if isinstance(a, _NsVerb)), None)
        tail = next((str(a) for a in args if isinstance(a, _NsTail)), None)
        if subj is None or verb is None:
            return None
        # NS_CANT (high-prio) steals "can't" corpus-wide, so '<player NP> who can't loses N life' — where
        # "can't" is a RELATIVE-CLAUSE modifier (an elided 'who can't <prior action>'), NOT a combat
        # prohibition — routes here instead of pcount/lose_life. Detect the real lose_life shape via a src
        # re-match of the `_lose*` templates and ground it (byte-for-byte), before the cant-combat frames.
        src = getattr(self, "_src", None)
        if src is not None and not _compound_subj(src.strip()):   # not a '<clause> and … who can't loses …' run-on
            s = src.strip()
            m = _LOSE_TGT_RE.match(s)
            if m:
                n = _amount(m.group(2))
                if n is not None:
                    return Effect("lose_life", n, _target(m.group(1)))
            m = _LHF_RE.match(s)
            if m:
                return Effect("lose_life", "half" + ("_rounded_" + m.group(2) if m.group(2) else ""),
                              _target(m.group(1) or "you"))
            m = _LLE_RE.match(s)
            if m:
                return Effect("lose_life", "equal_to_" + ground.slug(m.group(2)), _target(m.group(1) or "you"))
            m = _LTM_RE.match(s)
            if m:
                return Effect("lose_life", _that_amt(m.group(2), m.group(3)), _target(m.group(1) or "you"))
        rest = verb.strip() + (" " + tail.strip() if tail is not None else "")
        # casting restriction (§601.3e) first — the combat frames don't key on 'cast', so _ns_cant returns
        # None for it; _ns_cast grounds '<player-set> can't cast <spell-set> …' as cant_cast, else falls through.
        sl, rl = subj.strip().lower(), rest.strip().lower()
        return (_ns_cast(sl, rl) or _ns_player_restrict(sl, rl) or _ns_passive_restrict(sl, rl)
                or _ns_counter_restrict(sl, rl) or _ns_cant(sl, rl))

    def nsuntap(self, *args):
        # "<subj> doesn't/don't untap during <ctrl>'s [next] untap step[s] [for as long as …]" — the EXACT
        # `_doesnt_untap` template. The grammar gives us the SUBJECT span (template g1) and the TAIL span;
        # the distinctive 'doesn't/don't untap' verb (NS_DUVERB) is the structural anchor. We certify the
        # subject is a clean `_TGT` (`_NS_UNTAP_SUBJ`) and the tail is the template's 'during <controller>
        # [next] untap step[s] …' skeleton (`_NS_UNTAP_TAIL`, whose one group is the 'next' operand), then
        # emit doesnt_untap(-, _target(subj), 'next' if the optional ' next' matched else '-') — byte-
        # identical to the template, or abstain (subject outside `_TGT`, a tail that isn't the skeleton).
        subj = next((str(a) for a in args if isinstance(a, _NsSubj)), None)
        tail = next((str(a) for a in args if isinstance(a, _NsTail)), None)
        if subj is None or tail is None:
            return None
        subj = subj.strip()
        if not _NS_UNTAP_SUBJ.match(subj):
            return None                          # subject outside `_TGT` (template g1) -> abstain to regex
        m = _NS_UNTAP_TAIL.match(tail.strip())
        if not m:
            return None                          # tail isn't the 'during <ctrl> … untap step' skeleton
        cond = "for_as_long_as_" + ground.slug(m.group(2).replace("~", "self")) if m.group(2) else "-"   # capture the duration ('~'->self so not lossy)
        return Effect("doesnt_untap", "-", _target(subj), "next" if m.group(1) else "-", cond)

    # --- ATTACH ---------------------------------------------------------------
    def atsrc(self, *toks):
        return _AtSrc(" ".join(str(t) for t in toks))    # moved object (the _attach template's g1)

    def atdest(self, *toks):
        return _AtDest(" ".join(str(t) for t in toks))   # destination (the _attach template's g2)

    def atattach(self, *args):
        # 'attach <obj> to <dest>' -> attach(-, _target(dest), _target(obj)) — the EXACT `_attach`
        # template (MOVED object g1 -> EXTRA, DESTINATION g2 -> TARGET). The grammar gives us the body as
        # two spans joined by a TOPREP; because either span may carry internal 'to's ('up to N target',
        # 'attached to a creature'), the chosen split TOPREP is ambiguous, so we REJOIN the full body
        # ('atsrc to atdest') and reproduce the regex's own split: the FIRST ' to ' whose two halves are
        # EACH a clean `_TGT` (`_AT_TGT`), source non-greedy. No viable split (an internal 'to' leaving a
        # non-`_TGT` half) or a destination outside `_TGT` -> abstain to the regex's whole-object-slug
        # `_generic_object_verb` form. Byte-identical to the `_attach` frame (verified equivalent split).
        prep = next((str(a) for a in args if getattr(a, "type", None) == "TOPREP"), None)
        src = next((str(a) for a in args if isinstance(a, _AtSrc)), None)
        dest = next((str(a) for a in args if isinstance(a, _AtDest)), None)
        if prep is None or src is None or dest is None:
            return None
        body = src.strip() + " " + prep.strip() + " " + dest.strip()
        split = self._at_split(body)
        if split is None:
            return None
        obj, to = split                              # moved object (g1 -> EXTRA), destination (g2 -> TARGET)
        return Effect("attach", "-", _target(to), _target(obj))

    @staticmethod
    def _at_split(body):
        # The regex `^(~|it|_TGT) to (_TGT)$` split, done structurally: scan ' to ' left-to-right
        # (mirrors the non-greedy source) and take the FIRST where both halves are `_TGT`-anchored.
        # The split prep must be the literal 'to' (the template has no into/onto), which ' to ' enforces.
        i = 0
        while True:
            j = body.find(" to ", i)
            if j < 0:
                return None
            obj, to = body[:j], body[j + 4:]
            if _AT_TGT.match(obj) and _AT_TGT.match(to):
                return (obj, to)
            i = j + 1

    # --- FIGHT (§701.12) ------------------------------------------------------
    def fgo(self, *toks):
        return _FgO(" ".join(str(t) for t in toks))

    def fight(self, *args):
        # '<A> fights <B>' -> fight(-, _target(A), _target(B)) — the EXACT `_fight` template (A->TARGET,
        # B->EXTRA). TRUE grammar: FG_FIGHTS already split the clause into two operand spans; we only read
        # them off the tree and validate each as a clean `_TGT` (`_AT_TGT`, the shared `^_TGT$` validator).
        # No whole-clause re-parse. A span outside `_TGT` -> abstain (the regex's other forms own it).
        spans = [str(a).strip() for a in args if isinstance(a, _FgO)]
        if len(spans) != 2:
            return None
        a, b = spans
        if not _AT_TGT.match(a) or not _AT_TGT.match(b):
            return None
        return Effect("fight", "-", _target(a), _target(b))

    # --- TRANSFORM (mirror of DOUBLE) -----------------------------------------
    def tfbody(self, *toks):
        return _TfBody(" ".join(str(t) for t in toks))   # value unused; presence consumes the run

    def tftransform(self, *args):
        # 'transform <object>' -> transform(-, slug(<object>)), faithful to the object-verb leaf. Slice
        # the object from the lowercased source (after the leading 'transform '), apply the leaf's guards,
        # and mirror the `_verb_target`-then-`_generic_object_verb` precedence (a whole-`_TGT` object keeps
        # its article via `_target`; otherwise plain slug). They coincide on every corpus clause.
        src = getattr(self, "_src", None)
        if src is None:
            return None
        m = re.match(r"^transform (.+)$", src.strip(), re.I)
        if not m:
            return None
        rest = m.group(1)
        if _TF_OBJ_BAD.search(rest) or _is_compound_object(rest):
            return None                            # compound/run-on or structural marker -> regex
        tgt = _target(rest) if _TF_TGT.match(rest) else ground.slug(rest)
        return Effect("transform", "-", tgt)

    def mfbody(self, *toks):
        return _MfBody(" ".join(str(t) for t in toks))

    def mfmanifest(self, *args):
        # 'manifest the top [N] card[s] of your library' -> manifest(N, top_of_library). The singular form
        # ('the top card') stays byte-identical to the retired `_manifest_top` leaf (N=1); the plural form
        # ('the top three cards') carries the count. The grammar anchors on the MF_MANIFEST verb; we validate
        # the fixed shape from the raw source (structural slice, not an interpretive frame) and ABSTAIN on any
        # other manifest phrasing ('manifest those cards' / 'manifest dread') so those keep their handling.
        src = getattr(self, "_src", None)
        if src is None:
            return None
        m = re.match(r"^manifest the top (?:(\d+|one|two|three|four|five|six|seven|eight|nine|ten) "
                     r"cards|card) of your library$", src.strip().lower())
        if m:
            n = _amount(m.group(1)) if m.group(1) else 1
            if isinstance(n, int):
                return Effect("manifest", n, "top_of_library")
        return None

    def bcmtgt(self, *toks):
        return _BcmTgt(" ".join(str(t) for t in toks))

    def bccolor_v(self, *args):
        # '<subj> becomes/is <basic-color> [in addition to its other colors] [until end of turn]' — the
        # EXACT `_becomes_color` template: becomes(-, _target(subj), ground.slug(color)). Re-match src
        # against the template's own pattern (literal-color slice, 'the color of your choice' excluded so it
        # defers to `_becomes_choice`); a non-match (compound, type word) abstains to the regex.
        src = getattr(self, "_src", None)
        if src is None:
            return None
        if _is_compound_object(src.strip()):
            return None                            # a run-on '<subj> gains X and becomes <color>' — an earlier
            # template (grant_keyword) wins in the regex chain; abstain so the greedy `_TGT` can't swallow the
            # preceding effect into a garbage subject. Defer to the regex (byte-identical).
        m = _BCC_RE.match(src.strip())
        if not m:
            return None
        return Effect("becomes", "-", _target(m.group(1)), ground.slug(m.group(2)))

    def bcprest(self, *toks):
        return None                                # value unused; presence consumes the run (object re-sliced from src)

    def bccopy_v(self, *args):
        # '<subj> becomes a copy of <X>[, except <mods>] [until end of turn]' — the EXACT `_becomes_copy`
        # template: becomes(-, _target(subj), 'copy_of_'+_target(obj)[+'_except_'+slug(mods)]). Re-match src
        # against the template's pattern; a run-on (caught by `_is_compound_object`) or non-match abstains.
        src = getattr(self, "_src", None)
        if src is None:
            return None
        if _is_compound_object(src.strip()):
            return None                            # '… and <verb> …' run-on -> defer to the regex chain
        m = _BCP_RE.match(src.strip())
        if not m:
            return None
        extra = "copy_of_" + _target(m.group(2)) + ("_except_" + ground.slug(m.group(3)) if m.group(3) else "")
        return Effect("becomes", "-", _target(m.group(1)), extra)

    def bchmid(self, *toks):
        return None                                # value unused; presence consumes 'the <X>' (re-sliced from src)

    def bcchoice_v(self, *args):
        # '<subj> becomes the <X> of your choice [until end of turn]' — the EXACT `_becomes_choice` template:
        # becomes(-, _target(subj), 'chosen_'+ground.slug(X)). Re-match src against the template's pattern;
        # a run-on (caught by `_is_compound_object`) or non-match abstains to the regex.
        src = getattr(self, "_src", None)
        if src is None:
            return None
        if _is_compound_object(src.strip()):
            return None
        m = _BCH_RE.match(src.strip())
        if not m:
            return None
        return Effect("becomes", "-", _target(m.group(1)), "chosen_" + ground.slug(m.group(2)))

    def ctrest(self, *toks):
        return None                                # value unused; presence consumes the type body (re-matched from src)

    def bctype_v(self, *args):
        # '<subj> is/are/becomes a/an <card-type> [in addition to its other types] [until end of turn | for
        # as long as <cond>]' — the EXACT `_becomes_type` template: becomes(-, _target(subj), slug(type),
        # for_as_long_as_<cond>|-). Re-match src against the template's closed-word-list pattern (so only a
        # real card type grounds; color-type/subtype/'other land types' defer to the regex). Run-on guarded.
        src = getattr(self, "_src", None)
        if src is None:
            return None
        if _is_compound_object(src.strip()):
            return None
        m = _BCT_RE.match(src.strip())
        if m:
            cond = "for_as_long_as_" + ground.slug(m.group(3)) if m.group(3) else "-"
            return Effect("becomes", "-", _target(m.group(1)), ground.slug(m.group(2)), cond)
        # FALLBACK (`_all_types`, registered right after `_becomes_type` and BEFORE the color-type/added forms):
        # '<subj> is/are/becomes every <creature|basic land|nonbasic land|land> type [in addition …]' ->
        # 'every_'+slug(kind)+'_type'. Precedes _TAP_RE so 'every creature type in addition …' grounds here.
        m = _ALLT_RE.match(src.strip())
        if m:
            return Effect("becomes", "-", _target(m.group(1)), "every_" + ground.slug(m.group(2)) + "_type")
        # FALLBACK (matching the regex chain's `_becomes_type` -> `_becomes_color_type` order): a color-led
        # type ('is a black Zombie') the card-type list above didn't match -> the `_becomes_color_type` slug.
        m = _BCCT_RE.match(src.strip())
        if m:
            return Effect("becomes", "-", _target(m.group(1)), ground.slug(m.group(2)))
        # FALLBACK 2 (`_type_add`, after the two above per the regex chain order): 'a/an <X> in addition to
        # (its|their) other [creature|land] types|colors' -> 'added_'+slug(X) (subtype / plural-'their' /
        # 'colors' adds). Replicate the template's own object guard (compound / embedded copula run-on).
        m = _BTA_RE.match(src.strip())
        if m and not (_is_compound_object(m.group(2)) or _BT_RUNON.search(m.group(2))):
            return Effect("becomes", "-", _target(m.group(1)), "added_" + ground.slug(m.group(2)))
        # FALLBACK 3 (`_type_add_plural`, registered right after `_type_add`): the ARTICLE-LESS plural form
        # '<subj> are <Type>s in addition to their other types' -> 'added_'+slug(X). Same template object guard.
        m = _TAP_RE.match(src.strip())
        if m and not (_is_compound_object(m.group(2)) or _BT_RUNON.search(m.group(2))):
            return Effect("becomes", "-", _target(m.group(1)), "added_" + ground.slug(m.group(2)))
        # FALLBACK 4 (NET-NEW, no regex template): '<subj> becomes a/an <subtype> [until eot| for as long as …]'
        # — a §205 type SET to a permanent SUBTYPE (Coward/Warrior/Flagbearer/Demon Spirit/…) that the closed
        # card-type list (_BCT) doesn't cover. GATED: every word of the type phrase must be a grounded §205
        # subtype (ground.permanent_subtypes) — so a non-subtype ('a black Zombie' -> color, already handled by
        # _BCCT above; junk) abstains instead of slugging garbage. -> becomes(-, _target(subj), slug(subtype),
        # <for_as_long_as|->). Comes LAST so the card-type/color-type/in-addition forms keep precedence.
        m = _BCSUB_RE.match(src.strip())
        if m and not _is_compound_object(m.group(2)):
            words = m.group(2).split()
            if words and all(ground.slug(w) in _PERM_SUBTYPES for w in words):
                cond = "for_as_long_as_" + ground.slug(m.group(3)) if m.group(3) else "-"
                return Effect("becomes", "-", _target(m.group(1)), ground.slug(m.group(2)), cond)
        return None

    def bptpre(self, *toks):
        return None                                # value unused; the clause is re-matched from src

    def bptpost(self, *toks):
        return None

    def basept_v(self, *args):
        # base-P/T set, anchored on 'base power and toughness' — re-match src against the three templates in
        # the regex chain's precedence: `_becomes_base_pt` -> `_base_pt_perpetual` -> `_base_pt`. Run-on guarded.
        src = getattr(self, "_src", None)
        if src is None:
            return None
        src = src.strip()
        if _is_compound_object(src):
            return None
        # the P/T amount is the RAW capture (not slugged), so restore the canonical uppercase 'X' the regex
        # sees on the original-case clause (src is lowercased here): 'x/x' -> 'X/X', digits unchanged.
        m = _BBPT_RE.match(src)                     # 'becomes a <type> with base P/T' -> base_pt_<type>
        if m:
            cond = "for_as_long_as_" + ground.slug(m.group(4)) if m.group(4) else "-"
            return Effect("becomes", m.group(3).upper(), _target(m.group(1)), "base_pt_" + ground.slug(m.group(2)), cond)
        m = _BPTP_RE.match(src)                     # 'perpetually has base P/T' -> base_pt, perpetual
        if m:
            return Effect("becomes", m.group(2).upper(), _target(m.group(1)), "base_pt", "perpetual")
        m = _BPT_RE.match(src)                      # 'has/have/with base P/T' -> base_pt
        if m:
            return Effect("becomes", m.group(2).upper(), _target(m.group(1)), "base_pt")
        return None

    def alltail(self, *toks):
        return None                                # value unused; src is re-matched

    def alltypes_v(self, *args):
        # '<subj> is/are/becomes every <kind> type [in addition to other types] [until eot]' (§205) — the EXACT
        # `_all_types` template re-applied to self._src: becomes(-, _target(subj), 'every_'+slug(kind)+'_type').
        src = getattr(self, "_src", None)
        if src is None:
            return None
        m = _ALLT_RE.match(src.strip())
        if not m:
            return None
        return Effect("becomes", "-", _target(m.group(1)), "every_" + ground.slug(m.group(2)) + "_type")

    def btaorest(self, *toks):
        return None

    def btalso_v(self, *args):
        # '<subj> is/are also a/an <type>' -> the EXACT `_type_also`: becomes(-, _target(subj), 'added_'+slug(X)).
        src = getattr(self, "_src", None)
        if src is None:
            return None
        m = _TAO_RE.match(src.strip())
        if not m:
            return None
        return Effect("becomes", "-", _target(m.group(1)), "added_" + ground.slug(m.group(2)))

    def bcchtail(self, *toks):
        return None

    def bcchosen_v(self, *args):
        # '<subj> is the chosen type' -> the `_becomes_chosen` TYPE branch: becomes(-, _target(subj),
        # 'chosen_type'). (The CHOSENTYPE anchor is 'the chosen type' only; 'the chosen color' is the color slice.)
        src = getattr(self, "_src", None)
        if src is None:
            return None
        m = _BCHN_RE.match(src.strip())
        if not m:
            return None
        return Effect("becomes", "-", _target(m.group(1)), "chosen_" + m.group(2).lower())

    def bcmtail(self, *toks):
        return _BcmTail(" ".join(str(t) for t in toks))    # value unused; presence consumes the span

    def bcmbecomes(self, *args):
        # '<tgt> becomes/is/are [a] N/N <typetail> [with <kw>] [until end of turn]' — the dominant
        # `_becomes` animate. We OWN only this P/T-bearing shape; copy/color/base-pt/added/type
        # variants stay with the regex. Output must be byte-identical to `_becomes`.
        tgt = next((a for a in args if isinstance(a, _BcmTgt)), None)
        # the P/T is the BCM_PT Token (kept raw so we can slice the original tail by its end_pos)
        pt = next((a for a in args if not isinstance(a, (_BcmTgt, _BcmTail, _Quant))
                   and "/" in str(a)), None)
        quant = next((str(a) for a in args if isinstance(a, _Quant)), None)
        if tgt is None or pt is None:
            return None
        if quant is not None and quant.strip().lower() not in ("a", "an"):
            return None                        # regex's optional slot here is ONLY 'a'/'an' -> abstain
        tgt = str(tgt).strip()
        if not tgt or not _BCM_TGT.fullmatch(tgt):
            return None                        # subject isn't a legitimate `_TGT` NP (swallowed wrapper) -> abstain
        # slice the post-P/T span from the ORIGINAL (lowercased) source via the token's end position —
        # this is exactly the regex's T (the string g3/with/until-end-of-turn consume). Reconstructing
        # from joined tokens would lose original spacing/punctuation, so we slice instead.
        src = getattr(self, "_src", None)
        if src is None or not hasattr(pt, "end_pos") or pt.end_pos is None:
            return None
        tail = src[pt.end_pos:]
        g3 = _bcm_g3(tail)
        if g3 is None:
            return None                        # remainder isn't a clean 'with/until end of turn' -> abstain
        amount = str(pt).upper()               # P/T verbatim; input was lowercased so re-upper X ('x/x'->'X/X')
        return Effect("becomes", amount, _target(tgt), ground.slug(g3) or "-")

    # --- REVEAL ---------------------------------------------------------------
    def rvsubj(self, *toks):
        return _RvSubj(" ".join(str(t) for t in toks))

    def rvbody(self, *toks):
        return _RvBody(" ".join(str(t) for t in toks))

    def reveal(self, *args):
        subj = next((str(a) for a in args if isinstance(a, _RvSubj)), None)
        body = next((str(a) for a in args if isinstance(a, _RvBody)), None)
        if body is None:
            return None
        # The RVREVEAL Token is kept raw so the object span is sliced from the ORIGINAL (lowercased) source
        # by its end position — byte-identical to the regex's `.+?`/`[\w ]+?` capture, unaffected by any
        # token re-spacing in the rejoined `body`.
        vtok = next((a for a in args if not isinstance(a, (_RvSubj, _RvBody))
                     and str(a).rstrip("s").lower() == "reveal"), None)
        src = getattr(self, "_src", None)
        obj = (src[vtok.end_pos:].strip() if src is not None and vtok is not None
               and getattr(vtok, "end_pos", None) is not None else None)
        body = body.strip().lower()
        if subj is None:
            # IMPERATIVE. (1) the structured 'reveal the top N cards of <owner> library' frame (_reveal_top);
            m = _RV_TOP.match(body)
            if m:
                n = _amount(m.group(1)) if m.group(1) else 1
                if n is not None:                  # a non-numeric count word means _reveal_top abstains and
                    owner = m.group(2).strip().lower()   # the template chain falls through to _reveal_generic
                    tgt = "top_of_library" if owner == "your" else "top_of_" + ground.slug(owner) + "_library"
                    return Effect("reveal", n, tgt)
            if obj is None:
                return None
            # (2) obj is a clean `_TGT` ('reveal it/that card/each of those cards') -> `_verb_target` form
            # (target=slug, EXTRA='-'), which the regex chain reaches BEFORE _reveal_among/_reveal_generic.
            if _RV_VT.match(obj):
                if _is_compound_object(obj):
                    return None                    # _verb_target abstains on run-on -> chain also abstains
                return Effect("reveal", "-", _target(obj))
            # (3) 'reveal a/an/one/up to N <kind> from among them' (_reveal_among) — slug the inner kind only.
            am = _RV_AMONG.match(obj)
            if am:
                return Effect("reveal", "-", "you", ground.slug(am.group(1)))
            # (4) GENERIC 'reveal <object>' (_reveal_generic) — faithful slug, compound-guarded.
            if _is_compound_object(obj):
                return None                        # object runs into a 2nd effect -> regex owns the whole
            return Effect("reveal", "-", "you", ground.slug(obj))
        # SUBJECT-FORM. Only a clean closed player phrase (faithful; a swallowed/compound subject abstains).
        s = subj.strip().lower()
        if not _PLAYER.match(s):
            return None
        m = _RV_SUBJ_TOP.match(body)               # '<player> reveals the top N cards of their/your library'
        if m:
            n = _amount(m.group(1)) if m.group(1) else 1
            return Effect("reveal", n if n is not None else 1, _target(s))
        if _RV_SUBJ_HAND.match(body):              # '<player> reveals their hand'
            return Effect("reveal", "-", _target(s), "hand")
        # GENERIC subject-form '<player> reveals <object>' (_subject_obj_verb): object in TARGET, by_<player>
        # in EXTRA; compound-guarded. The object span is sliced from src (not the subject-stripped body).
        if obj is None or _is_compound_object(obj):
            return None
        return Effect("reveal", "-", _target(obj), "by_" + _target(s))

    # --- PREVENT_DAMAGE -------------------------------------------------------
    def pvpre(self, *toks):
        return _PvPre(" ".join(str(t) for t in toks))

    def pvtail(self, *toks):
        return _PvTail(" ".join(str(t) for t in toks))

    def prevent_that(self, *args):
        # 'prevent that damage' / 'prevent the next N damage' / 'prevent N of that damage' (§615 consequent,
        # no tail) — the EXACT `_prevent_that` template re-applied to self._src: prevent_damage(<n|that>, -).
        # The grammar already guaranteed PVPREVENT … DMG with nothing after; the re-match certifies the body
        # is one of the three forms (else abstain -> a body the regex doesn't accept stays on the regex leaf).
        s = (getattr(self, "_src", None) or "").strip()
        m = re.match(r"^prevent (that damage|the next (\w+) damage|(\w+) of that damage)$", s, re.I)
        if not m:
            return None
        n = _amount(m.group(2) or m.group(3)) if (m.group(2) or m.group(3)) else None
        return Effect("prevent_damage", n if n is not None else "that", "-")

    def prevent(self, *args):
        # 'prevent <pre> damage <tail>' — the EXACT `_fog`/`_prevent` templates. The grammar carved the
        # body at the structural DMG terminal into a leading span (`pvpre`) and a trailing span (`pvtail`);
        # we read them off the tree and certify each with an anchored per-operand validator that captures
        # the operands. FOG ('all [combat]' / 'that would be dealt this turn') is tried first (disjoint
        # 'all' pre), then NEXT ('the next <count>' / 'that would be dealt [this turn] to <span>'). A clause
        # outside both skeletons abstains -> the regex fallback (_prevent_all_scoped/_prevent_that/shield).
        pre = next((str(a) for a in args if isinstance(a, _PvPre)), None)
        tail = next((str(a) for a in args if isinstance(a, _PvTail)), None)
        if pre is None or tail is None:
            return None
        pre = pre.strip().lower()
        tail = tail.strip().lower()
        mp = _PV_FOG_PRE.match(pre)                 # 'all [combat] damage that would be dealt this turn' (_fog)
        if mp and _PV_FOG_TAIL.match(tail):
            return Effect("prevent_damage", "all", "combat" if mp.group(1) else "all")
        mp = _PV_NEXT_PRE.match(pre)               # 'the next N damage that would be dealt [this turn] to <span>'
        if mp:
            m = _PV_NEXT_TAIL.match(tail)
            if not m:
                return None                        # 'the next N' with a non-_prevent tail -> regex
            n = _amount(mp.group(1))
            amt = n if n is not None else "X"
            span = m.group(1).strip()
            # Mirror the regex's optional trailing ' this turn': the template's `(?:this turn )?to … (?: this
            # turn)?` consumes a LEADING 'this turn' (when the body reads 'dealt this turn to <span>') and then
            # <span> has no trailing 'this turn'; otherwise the trailing ' this turn' is the optional suffix and
            # is stripped off <span>. (When neither holds — junk after 'this turn' — span keeps it, matching the
            # regex's greedy `_TGT` swallow, e.g. 'any target this turn by a source of your choice'.)
            lead_this_turn = "dealt this turn to " in tail
            if not lead_this_turn and span.endswith(" this turn"):
                span = span[:-len(" this turn")].strip()
            # The regex's `_prevent` target is `(any number of targets|{_TGT})` — a clean NP that the `_TGT`
            # alternatives can't reach across a comma, a '/', a 'divided as you choose' rider, or a ', where
            # X is …' scaling appendix (those clauses make the template FAIL, so the regex abstains). Lark's
            # WORD swallows them, which would emit a garbled target slug — a LOSSY net-new fact. Abstain.
            if "," in span or "/" in span or "divided as you choose" in span or " where " in span:
                return None
            if span == "any number of targets":
                tgt = "any_number_of_targets"      # _prevent's special-case
            else:
                tgt = _target(span)
            return Effect("prevent_damage", str(amt) if isinstance(amt, int) else amt, tgt)
        # SCOPED / SOURCE prevention (_prevent_all_scoped / _prevent_all_source): 'prevent all [combat|
        # noncombat] damage <rider>' where <rider> is a PASSIVE 'that would be dealt <scope>' or an ACTIVE
        # '[that ]<source> would deal [to <recipient>] [this turn|this combat]'. Both funnel through
        # _pv_scope, recording the whole rider as a faithful descriptive scope slug (amount 'all', target
        # '-') — byte-identical to the regex. PASSIVE is tried first (it requires the literal 'would be
        # dealt'; ACTIVE requires 'would deal' — disjoint, so order is cosmetic). The plain no-scope fog
        # ('all [combat] damage that would be dealt this turn') already returned above, so a noncombat fog
        # falls here -> scope 'noncombat this turn', matching the regex (whose _fog excludes noncombat).
        mk = _PV_ALL_KIND.match(pre)
        if not mk:
            return None                            # not 'all …' (and not 'the next') -> regex
        kind = mk.group(1) or ""
        ms = _PV_SCOPED_TAIL.match(tail)           # passive: 'that would be dealt <scope>'
        if ms:
            return _pv_scope(kind, ms.group(1))
        ms = _PV_SOURCE_TAIL.match(tail)           # active: '[that ]<source> would deal [to <X>] [this turn]'
        if ms:
            scope = "by " + ms.group(1).strip()
            if ms.group(2):
                scope += " " + ms.group(2).strip()
            if ms.group(3):
                scope += " " + ms.group(3).strip()
            return _pv_scope(kind, scope)
        return None                                # an 'all …' rider outside both skeletons -> regex

    # --- PHASE_OUT / PHASE_IN (§702.26) ---------------------------------------
    def pfsubj(self, *toks):
        return _PfSubj(" ".join(str(t) for t in toks))

    def phaseout(self, *args):
        # '<permanent> phases out/in [until …]' — the EXACT `_phase` template: phase_<dir>(-, _target(subj)),
        # the optional trailing 'until …' rider (reused `trailer`) DROPPED. The subject is the leading span
        # before the PHASE terminal; a subject outside `_TGT` (`_AT_TGT`) abstains to the regex.
        subj = next((a for a in args if isinstance(a, _PfSubj)), None)
        tok = next((a for a in args if getattr(a, "type", None) == "PHASE"), None)
        if subj is None or tok is None:
            return None
        s = str(subj).strip()
        if not _AT_TGT.match(s):
            return None
        direction = "out" if "out" in str(tok).lower() else "in"
        return Effect("phase_" + direction, "-", _target(s))

    # --- REDIRECT_DAMAGE (§614.9) ---------------------------------------------
    def rdpre(self, *toks):
        return _RdPre(" ".join(str(t) for t in toks))

    def rda(self, *toks):
        return _RdA(" ".join(str(t) for t in toks))

    def rdb(self, *toks):
        return _RdB(" ".join(str(t) for t in toks))

    def excess_redirect(self, *args):
        # 'Excess damage is dealt to <B> [instead]' (§120.4) — the overflow past a creature's lethal damage
        # spills to <B>; no source-side <A> (it's the prior sentence's target). redirect_damage(excess, B).
        rdb = next((str(a) for a in args if isinstance(a, _RdB)), None)
        if rdb is None:
            return None
        bm = _RD_B.match(rdb.strip())
        if not bm:
            return None
        return Effect("redirect_damage", "excess", _target(bm.group(1)))

    def redirect(self, *args):
        # '<amount> damage that would be dealt to <A> … is dealt to <B> [instead]' — the EXACT `_redirect`
        # (next-N, this-turn required) / `_redirect_all` (all/all-combat, optional this-turn-or-by rider)
        # templates: redirect_damage(<amt>, _target(B), 'from_'+_target(A)). The three spans are read off
        # the tree and A/B certified by the `_TGT`-reusing operand regexes; outside the skeleton -> abstain.
        pre = next((str(a) for a in args if isinstance(a, _RdPre)), None)
        rda = next((str(a) for a in args if isinstance(a, _RdA)), None)
        rdb = next((str(a) for a in args if isinstance(a, _RdB)), None)
        if pre is None or rda is None or rdb is None:
            return None
        bm = _RD_B.match(rdb.strip())
        if not bm:
            return None
        b = bm.group(1)
        mn = _RD_PRE_NEXT.match(pre.strip())
        if mn:
            am = _RD_A_NEXT.match(rda.strip())            # 'to <A> this turn' (this-turn required)
            if not am:
                return None
            n = _amount(mn.group(1))
            amt = n if n is not None else "X"
        else:
            ma = _RD_PRE_ALL.match(pre.strip())
            if not ma:
                return None                               # not 'the next N' / 'all [combat]' -> regex
            am = _RD_A_ALL.match(rda.strip())             # 'to <A>(?: this turn| by <src>)?' (rider dropped)
            if not am:
                return None
            amt = "all_combat" if ma.group(1) else "all"
        return Effect("redirect_damage", amt, _target(b), "from_" + _target(am.group(1)))

    # --- SKIP (§500.7) --------------------------------------------------------
    def sksubj(self, *toks):
        return _SkSubj(" ".join(str(t) for t in toks))

    def skbody(self, *toks):
        return _SkBody(" ".join(str(t) for t in toks))

    def skipverb(self, *args):
        # '[<player>] skip[s] (your|its|their|his or her) [next] <phase>' — the EXACT `_skip` template:
        # skip(-, _target(player or 'you'), slug(<phase>)). The phase is read from the captured body span
        # with the template's possessive+next+phase regex; a non-player subject or non-phase body abstains.
        subj = next((a for a in args if isinstance(a, _SkSubj)), None)
        body = next((a for a in args if isinstance(a, _SkBody)), None)
        if body is None:
            return None
        m = _SK_BODY.match(str(body).strip())
        if not m:
            return None
        if subj is not None and not _AT_TGT.match(str(subj).strip()):
            return None
        who = _target(str(subj).strip()) if subj is not None else "you"
        return Effect("skip", "-", who, ground.slug(m.group(1)))

    # --- AMASS (§701.43) ------------------------------------------------------
    def askind(self, *toks):
        return _AsKind(" ".join(str(t) for t in toks))

    def asnum(self, tok):
        return _AsNum(str(tok))

    def amassverb(self, *args):
        # 'amass <army-type> <N>' — the EXACT `_amass` template: amass(<n|1>, you, slug(<type>)). The count
        # token is restricted to the template's own (\d+|one|two|three|x) set; anything else abstains.
        kind = next((a for a in args if isinstance(a, _AsKind)), None)
        num = next((a for a in args if isinstance(a, _AsNum)), None)
        if kind is None or num is None or not _AS_NUM.match(str(num).strip()):
            return None
        n = _amount(str(num).strip())
        return Effect("amass", n if n is not None else 1, "you", ground.slug(str(kind)))

    # --- MONSTROSITY (§701.x) / GOAD passive (§701.38) ------------------------
    def msnum(self, tok):
        return _MsNum(str(tok))

    def monstrosity_v(self, *args):
        # 'Monstrosity <N>' — the `_kwaction_n` leaf for this verb: monstrosity(<n>, you). Count restricted
        # to the template's (\d+|one..five|x) set; anything else abstains.
        num = next((a for a in args if isinstance(a, _MsNum)), None)
        if num is None or not _MS_NUM.match(str(num).strip()):
            return None
        n = _amount(str(num).strip())
        return Effect("monstrosity", n if n is not None else "-", "you")

    def gdsubj(self, *toks):
        return _GdSubj(" ".join(str(t) for t in toks))

    def goaded_v(self, *args):
        # '<creature> is goaded' — the EXACT `_goaded` template: goad(-, _target(subj)). Subject _TGT or abstain.
        subj = next((a for a in args if isinstance(a, _GdSubj)), None)
        if subj is None or not _AT_TGT.match(str(subj).strip()):
            return None
        return Effect("goad", "-", _target(str(subj).strip()))

    def bdgsubj(self, *toks):
        return _BdgSubj(" ".join(str(t) for t in toks))

    def bdgtail(self, *toks):
        return None                                # optional trailing duration — dropped (like _BCT's until-eot)

    def bcdesig_v(self, *args):
        # '<subj> becomes foretold/plotted' — a §701 status designation (foretell/plot), the tail of a
        # face-down-exile trigger: becomes(-, _target(subj), <desig>). Subject _TGT or abstain.
        subj = next((a for a in args if isinstance(a, _BdgSubj)), None)
        desig = next((str(a) for a in args if getattr(a, "type", None) == "BECOMESDESIG"), None)
        if subj is None or desig is None or not _AT_TGT.match(str(subj).strip()):
            return None
        d = re.sub(r"^becomes? ", "", desig.strip(), flags=re.I).strip()
        return Effect("becomes", "-", _target(str(subj).strip()), ground.slug(d))

    def bcnosusp(self, *args):
        # '<subj> is/are/become no longer suspected' — §701.60 suspect REMOVAL: suspect(-, _target(subj),
        # no_longer). Subject _TGT or abstain. ('suspect' is a grounded keyword action, so the marker is on
        # the extra slot — the engine reads the removal from there.)
        subj = next((a for a in args if isinstance(a, _BdgSubj)), None)
        if subj is None or not _AT_TGT.match(str(subj).strip()):
            return None
        return Effect("suspect", "-", _target(str(subj).strip()), "no_longer")

    # --- FLIP_COIN / FIGHT-each-other -----------------------------------------
    def flipcoin(self, tok):
        # 'Flip a coin [until you lose a flip]' — the EXACT `_flip` template.
        return Effect("flip_coin", "until_lose" if "until you lose a flip" in str(tok).lower() else "-", "you")

    def fesubj(self, *toks):
        return _FeSubj(" ".join(str(t) for t in toks))

    def fighteach(self, *args):
        # '[then] <creatures> fight each other' — the EXACT `_fight_each` template: fight(-, _target(subj),
        # 'each_other'). The optional leading 'then' is stripped (the regex's `(?:then )?`); subject _TGT or abstain.
        subj = next((a for a in args if isinstance(a, _FeSubj)), None)
        if subj is None:
            return None
        s = re.sub(r"^then ", "", str(subj).strip(), flags=re.I).strip()
        if not _AT_TGT.match(s):
            return None
        return Effect("fight", "-", _target(s), "each_other")

    # --- NUMBERED KEYWORD ACTIONS (bolster/adapt/incubate/support) ------------
    def kwnnum(self, tok):
        return _KwnNum(str(tok))

    def kwaction_n(self, *args):
        # '<bolster|adapt|incubate|support> <N>' — the `_kwaction_n` leaf for these verbs: <verb>(<n>, you).
        # Count restricted to the template's (\d+|one..five|x) set; verb re-checked against keyword_actions.
        vtok = next((str(a) for a in args if getattr(a, "type", None) == "KWACTION_N"), None)
        num = next((a for a in args if isinstance(a, _KwnNum)), None)
        if vtok is None or num is None or not _MS_NUM.match(str(num).strip()):
            return None
        v = ground.slug(vtok)
        if v not in ground.keyword_actions():
            return None
        n = _amount(str(num).strip())
        return Effect(v, n if n is not None else "-", "you")

    def enduresubj(self, *toks):
        return _EndureSubj(" ".join(str(t) for t in toks))

    def endurenum(self, tok):
        return None                                # presence consumes the count; src is re-matched

    def endure_v(self, *args):
        # '<creature> endures N' (§701, BLB/DSK 2024) — the EXACT `_endure` template re-applied to self._src:
        # endure(<n|->, _target(subj)). The subject is the actor/target (the enduring creature), NOT 'you' — so
        # this is a subject-prefixed production, re-matched against _endure's own `(?:(_TGT) )?endures? (\w+)`.
        src = getattr(self, "_src", None)
        if src is None:
            return None
        m = _ENDURE_RE.match(src.strip())
        if not m or "endure" not in ground.effect_verbs():
            return None
        n = _amount(m.group(2))
        return Effect("endure", n if n is not None else "-", _target(m.group(1) or "~"))

    # --- INTRANSITIVE KEYWORD ACTIONS (investigate/explore/proliferate) -------
    def kvisubj(self, *toks):
        return _KviSubj(" ".join(str(t) for t in toks))

    def cntobj(self, *toks):
        return _CntObj(" ".join(str(t) for t in toks))

    def choose_new_targets(self, *args):
        # '[you may] choose new targets for <X>' (§707.10 copy-redirect) -> choose_new_targets(-, _target(X)).
        obj = next((str(a) for a in args if isinstance(a, _CntObj)), None)
        if obj is None:
            return None
        return Effect("choose_new_targets", "-", _target(obj.strip().lower()))

    def swptbody(self, *toks):
        return _SwptBody(" ".join(str(t) for t in toks))

    def switch_pt_v(self, *args):
        # 'switch <X>'s power and toughness [until end of turn]' (§613.4e) — the EXACT `_switch_pt` template
        # re-applied to the post-'switch' span: switch_pt(-, _target(X)). _TGT object or abstain; duration dropped.
        body = next((str(a) for a in args if isinstance(a, _SwptBody)), None)
        if body is None:
            return None
        m = _SWPT_RE.match(body.strip())
        return Effect("switch_pt", "-", _target(m.group(1))) if m else None

    def acronlyrest(self, *toks):
        return _AcrRest(" ".join(str(t) for t in toks))

    def activate_only(self, *args):
        # 'Activate [this ability] only <restriction>' (§602.5) -> activate_only(-, -, slug(restriction)).
        rest = next((str(a) for a in args if isinstance(a, _AcrRest)), None)
        if rest is None or not rest.strip():
            return None
        return Effect("activate_only", "-", "-", ground.slug(rest.strip().lower()))

    def triggers_only(self, *args):
        # 'This ability triggers only <restriction>' (§603.3) -> triggers_only(-, -, slug(restriction)).
        rest = next((str(a) for a in args if isinstance(a, _AcrRest)), None)
        if rest is None or not rest.strip():
            return None
        return Effect("triggers_only", "-", "-", ground.slug(rest.strip().lower()))

    def do_this_only(self, *args):
        # 'Do this only <restriction>' (a §603.3e frequency cap on the prior effect) -> do_this_only(-, -, slug).
        rest = next((str(a) for a in args if isinstance(a, _AcrRest)), None)
        if rest is None or not rest.strip():
            return None
        return Effect("do_this_only", "-", "-", ground.slug(rest.strip().lower()))

    def fcastverb(self, tok):
        return _FcVerb(str(tok))

    def fcastobj(self, *toks):
        return _FcObj(" ".join(str(t) for t in toks))

    def free_cast(self, *args):
        # '[you may] play/cast <X> [this turn] without paying its mana cost' (§601 free-cast). The modifier was
        # DROPPED/garbled by the regex leaf; here it's faithful: <verb>(-, _target(X), without_paying_mana_cost).
        verb = next((str(a).lower() for a in args if isinstance(a, _FcVerb)), None)
        obj = next((str(a) for a in args if isinstance(a, _FcObj)), None)
        if verb not in ("play", "cast") or obj is None:
            return None
        o = obj.strip().lower()
        if o.endswith(" this turn"):                  # drop the optional duration (string op, not regex)
            o = o[:-len(" this turn")].strip()
        if not o:
            return None
        return Effect(verb, "-", _target(o), "without_paying_mana_cost")

    def pdcond(self, *toks):
        return _PdCond(" ".join(str(t) for t in toks))

    def play_dur(self, *args):
        # '[you may] play/cast <X> for as long as <cond>' (impulse-exile) -> <verb>(-, _target(X), -,
        # for_as_long_as_<cond>). The duration was garbled into the target by the regex leaf.
        verb = next((str(a).lower() for a in args if isinstance(a, _FcVerb)), None)
        obj = next((str(a) for a in args if isinstance(a, _FcObj)), None)
        cond = next((str(a) for a in args if isinstance(a, _PdCond)), None)
        if verb not in ("play", "cast") or obj is None or cond is None:
            return None
        return Effect(verb, "-", _target(obj.strip().lower()), "-", "for_as_long_as_" + ground.slug(cond.strip().lower()))

    def play_flash(self, *args):
        # '[you may] play/cast <X> as though it/they had flash' (§117.1a impulse instant-speed) ->
        # <verb>(-, _target(X), as_though_flash). The modifier was dropped/garbled by the regex leaf.
        verb = next((str(a).lower() for a in args if isinstance(a, _FcVerb)), None)
        obj = next((str(a) for a in args if isinstance(a, _FcObj)), None)
        if verb not in ("play", "cast") or obj is None:
            return None
        return Effect(verb, "-", _target(obj.strip().lower()), "as_though_flash")

    def play_from(self, *args):
        # '[you may] play/cast <X> from <zone>' (graveyard-recursion etc.) -> <verb>(-, _target(X), from_<zone>).
        # Abstains on any non-play/cast verb (return/exile/... keep their own, higher-priority parse).
        verb = next((str(a).lower() for a in args if isinstance(a, _FcVerb)), None)
        obj = next((str(a) for a in args if isinstance(a, _FcObj)), None)
        src = next((a for a in args if isinstance(a, _Source)), None)
        if verb not in ("play", "cast") or obj is None or src is None or not src.zone:
            return None
        return Effect(verb, "-", _target(obj.strip().lower()), "from_" + src.zone)

    def turn_face(self, *args):
        # '[you may] turn <X> face up/down' (§708) -> turn_face_up|down(-, _target(X)) per the FACE_DIR token.
        # Abstains unless the verb is 'turn' (object verbs keep their own, higher-priority parse via objall).
        verb = next((str(a).lower() for a in args if isinstance(a, _FcVerb)), None)
        obj = next((str(a) for a in args if isinstance(a, _FcObj)), None)
        fdir = next((str(a).lower() for a in args if getattr(a, "type", None) == "FACE_DIR"), None)
        if verb not in ("turn", "turns") or obj is None or fdir is None:
            return None
        return Effect("turn_face_up" if "up" in fdir else "turn_face_down", "-", _target(obj.strip().lower()))

    def kvintrans(self, *args):
        # '[<subject>] investigate[s]/explore[s]/proliferate[s]' — the `_bare_action`/`_subject_action` leaves:
        # <verb>(-, _target(subject|you)). The 3rd-person 's' is stripped to the keyword-action base exactly
        # as `_subject_action` does (slug as-is if a keyword action, else strip trailing 's'). Subject _TGT or abstain.
        tok = next((str(a) for a in args if getattr(a, "type", None) == "KVINTRANS"), None)
        subj = next((a for a in args if isinstance(a, _KviSubj)), None)
        mult = next((str(a) for a in args if getattr(a, "type", None) == "KVIMULT"), None)
        if tok is None:
            return None
        v = ground.slug(tok)
        if v not in ground.keyword_actions():
            v = v.rstrip("s")
            if v not in ground.keyword_actions():
                return None
        if subj is not None and not _AT_TGT.match(str(subj).strip()):
            return None
        # repeat multiplier ('investigate twice' -> amount 2) — count in the amount slot, like scry/amass N.
        amt = "-"
        if mult is not None:
            m = mult.strip().lower()
            if m == "that many times":
                amt = "that_amount"                 # anaphoric count ('investigate that many times'), like draw
            elif m in ("once", "twice", "thrice"):
                amt = {"once": 1, "twice": 2, "thrice": 3}[m]
            elif m.endswith(" times"):
                amt = _amount(m[:-len(" times")].strip())
            if not (isinstance(amt, int) or amt == "that_amount"):
                return None
        return Effect(v, amt, _target(str(subj).strip()) if subj is not None else "you")

    # --- EXCHANGE (§701.10) ---------------------------------------------------
    def excbody(self, *toks):
        return _ExcBody(" ".join(str(t) for t in toks))   # value unused; the object is sliced from src

    def exchangeverb(self, *args):
        # 'exchange <object>' -> exchange(-, slug(<object>)) — the EXACT mirror of `dbl` (exchange IS in
        # `_OBJ_VERBS`, so the generic slug leaf grounds it): slice the object from src after 'exchange ',
        # apply the leaf's `_DB_OBJ_BAD`/`_is_compound_object` guards, reproduce _TGT-keeps-article / else-slug.
        src = getattr(self, "_src", None)
        if src is None:
            return None
        m = re.match(r"^exchange (.+)$", src.strip(), re.I)
        if not m:
            return None
        rest = m.group(1)
        if _DB_OBJ_BAD.search(rest) or _is_compound_object(rest):
            return None
        tgt = _target(rest) if _DB_TGT.match(rest) else ground.slug(rest)
        return Effect("exchange", "-", tgt)

    # --- COPY (§707) ----------------------------------------------------------
    def cpbody(self, *toks):
        return _CpBody(" ".join(str(t) for t in toks))   # value unused; presence consumes the run

    def copyverb(self, *args):
        # 'copy <object>' -> copy(-, _target(<object>)) — the EXACT `_verb_target` leaf. Unlike `double`,
        # copy is NOT in `_OBJ_VERBS`, so the generic slug leaf (`_generic_object_verb`) never grounds it:
        # only `_verb_target` (`^(\w+) (_TGT)$`) does, requiring a clean whole-`_TGT` object that isn't a
        # run-on. So we slice the object from the source after 'copy ' and ground ONLY when it is a `_TGT`
        # (`_DB_TGT`) and not `_is_compound_object`; anything else (a rider like ', except the copy is …',
        # a non-`_TGT` object, a compound) abstains to the regex — NO slug fallback (that was the over-grounding bug).
        src = getattr(self, "_src", None)
        if src is None:
            return None
        m = re.match(r"^copy (.+)$", src.strip(), re.I)
        if not m:
            return None
        rest = m.group(1)
        # §707.2 copy-MODIFICATION rider: 'copy <obj>, except <the copy is …>' (Spark Double, Storm of
        # Saruman, the 'except it's a token / isn't legendary / is a 5/5' family). Split the rider OFF so the
        # object is the clean _TGT and the modification rides `extra` (faithful — the stated difference is
        # recorded, not garbled into the object; this is NOT the old slug-fallback that over-grounded).
        extra = "-"
        em = re.match(r"^(.+?),? except (.+)$", rest, re.I)
        if em:
            rest, extra = em.group(1).strip(), "except_" + ground.slug(em.group(2))
        if not _DB_TGT.match(rest) or _is_compound_object(rest):
            return None
        return Effect("copy", "-", _target(rest), extra)

    # --- EXILE top-of-library / until-leaves (§701.x) -------------------------
    def xtbody(self, *toks):
        return _XtBody(" ".join(str(t) for t in toks))     # value unused; the clause is re-matched off src

    def exiletop(self, *args):
        # 'exile the top [<N>] card[s] of <owner> librar(y|ies)' -> the EXACT `_exile_top` template:
        # n = _amount(<N>) (default 1), owner 'your' -> 'top_of_library', else 'top_of_'+slug(owner)+
        # '_library'. The grammar (EXILE … XLIB) reaches this only on the library shape (the generic
        # imperative leaf's `_TOPLIB` guard already abstains here); re-match the template regex on src so
        # the N + owner slug are byte-identical, abstaining (None) exactly where `_exile_top` does.
        src = getattr(self, "_src", None)
        if src is None:
            return None
        m = _XL_TOP.match(src.strip())
        if not m:
            return None
        n = _amount(m.group(1)) if m.group(1) else 1
        if n is None:
            return None
        owner = ("top_of_library" if m.group(2).lower() == "your"
                 else "top_of_" + ground.slug(m.group(2)) + "_library")
        return Effect("exile", n, owner)

    def xlobj(self, *toks):
        return _XlObj(" ".join(str(t) for t in toks))      # value unused; the object is re-matched off src

    def exileuntil(self, *args):
        # 'exile <object> until ~ leaves the battlefield' -> the EXACT `_exile_until` template:
        # exile(-, _target(<object>), 'until_self_leaves'). The grammar (EXILE … XLEAVES) reaches this only
        # on the until-leaves shape (the generic imperative leaf's `trailer` eats the rider and abstains);
        # re-match the template regex (object certified by the shared `_TGT`) on src so the slug is byte-
        # identical, and ABSTAIN (over a lossy fact) on a compound/run-on object the regex would garble.
        src = getattr(self, "_src", None)
        if src is None:
            return None
        m = _XL_UNTIL.match(src.strip())
        if not m or _is_compound_object(m.group(1)):
            return None
        return Effect("exile", "-", _target(m.group(1)), "until_self_leaves")

    # --- RETURN_TO_HAND (§614 bounce) -----------------------------------------
    def rhbody(self, *toks):
        return _RhBody(" ".join(str(t) for t in toks))   # value unused; presence consumes the run

    def rethand(self, *args):
        # 'Return [<player>] <object> [from <zone>] to <owner>'s hand' -> return_to_hand(-, _target(<obj>),
        # <from_<zone>|->) — the EXACT `_bounce`/`_regrowth`/`_return_zone` templates. The RETHAND terminal
        # (the trailing 'to <owner-poss> hand[s]' destination) is already split off by the grammar; we slice
        # the body out of `self._src` after the leading 'return[s] ' verb (byte-identical slug, never a
        # re-joined approximation, like `copyverb`), drop an optional leading PLAYER subject (`_RH_SUBJ`,
        # exactly as `_return_zone`'s `(?:_TGT )?` does), then split a trailing 'from <zone>' SOURCE off the
        # object into extra (object stops at 'from'). Grounds ONLY when the object is a clean `_TGT`
        # (`_RH_TGT`) and not an `_is_compound_object` run-on EFFECT compound; a NOUN conjunction ('A and B',
        # both nouns) is NOT compound and DOES ground. Anything else abstains to the regex — NO slug fallback.
        src = getattr(self, "_src", None)
        if src is None:
            return None
        # split at the 'return[s]' verb so a leading PLAYER subject ('<player> returns <obj> …', the
        # `_return_zone` subject-first shape) is dropped exactly as that template's `(?:_TGT )?` prefix does.
        m = re.match(r"^(.*?)returns? (.+) to (?:its owner's|your|their owners'|their owner's|their|his or her) hands?"
                     r"(?: at the beginning of [\w' ]+?)?$",   # DROP a trailing delayed-return timing, as `_return_zone` does
                     src.strip(), re.I)
        if not m:
            # REVERSED phrasing 'return to <owner-hand> <object>' (`_return_zone_rev`, hand only — the other
            # zones don't lex RETHAND so never reach here). Byte-identical: return_to_hand(-, _target(obj)).
            rm = re.match(r"^return to [\w' ]*?hands? (.+?)$", src.strip(), re.I)
            if rm and not _is_compound_object(rm.group(1)):
                return Effect("return_to_hand", "-", _target(rm.group(1)))
            return None
        subj = m.group(1).strip()
        if subj and not _RH_SUBJ.match(subj):
            return None                            # a leading lead-in that isn't a clean PLAYER -> regex owns it
        body = m.group(2).strip()
        # split a trailing 'from <…> graveyard/battlefield/exile/hand/library' SOURCE off the object.
        obj = body
        extra = "-"
        fm = _RH_FROM.search(obj)
        if fm:
            obj = obj[:fm.start()].strip()
            extra = "from_" + fm.group(1).lower()
        if not obj or _is_compound_object(obj):
            return None                            # empty / run-on EFFECT compound -> regex owns it
        return Effect("return_to_hand", "-", _target(obj), extra)

    # --- MUST_ATTACK / MUST_BLOCK (§508/§509) ---------------------------------
    def mrbody(self, *toks):
        return _MrBody(" ".join(str(t) for t in toks))

    def mustreq(self, *args):
        # '<subj> attacks/blocks [<obj>] [<dur>] if able' — the EXACT _must_attack / _must_block_tgt /
        # _must_block_able templates, re-applied to the body span (the MRABLE 'if able' anchor is already
        # stripped by the grammar). ATTACK: must_attack(-, subj, <directed|->); BLOCK with object:
        # must_block(-, subj, <obj>); BLOCK bare: must_block(-, subj). A lossy/compound subject (which the
        # regex grabs into a garbled `_TGT`) yields no clean split here -> abstain to the regex.
        body = next((str(a) for a in args if isinstance(a, _MrBody)), None)
        if body is None:
            return None
        body = body.strip()
        m = _MR_ATTACK_OR_BLOCK.match(body)              # the combined disjunction first ('attacks or blocks')
        if m:
            return Effect("must_attack_or_block", "-", _target(m.group(1)))
        m = _MR_ATTACK.match(body)
        if m:
            return Effect("must_attack", "-", _target(m.group(1)), _target(m.group(2)) if m.group(2) else "-")
        m = _MR_BLOCK_TGT.match(body)
        if m:
            return Effect("must_block", "-", _target(m.group(1)), _target(m.group(2)))
        m = _MR_BLOCK_ABLE.match(body)
        if m:
            return Effect("must_block", "-", _target(m.group(1)))
        m = _MR_MUST_BE_BLOCKED.match(body)          # PASSIVE '<X> must be blocked [dur]' (_must_be_blocked)
        if m:
            return Effect("must_be_blocked", "-", _target(m.group(1)))
        m = _MR_MUST_ATTACK_BLOCK.match(body)        # 'must <verb>' phrasing '<X> must attack/block [dur]'
        if m:
            return Effect("must_" + m.group(2), "-", _target(m.group(1)))
        return None

    # --- GRANT_COMBAT (can attack/block …; §509/§508) -------------------------
    def gccsubj(self, *toks):
        return _GccSubj(" ".join(str(t) for t in toks))

    def gcctail(self, *toks):
        return None   # presence consumes the permission span; the slug is re-sliced from `self._src`

    def grantcombat(self, *args):
        # '<subj> can attack/block …' — the EXACT `_as_though_combat` (whole 'attack/block … as though …'
        # span slugged, NO trailing-duration strip) and `_can_block_more` (count slugged, trailing 'this
        # turn'/'each combat' DROPPED) templates, re-applied to the WHOLE clause sliced from `self._src`
        # (so the slug is byte-identical, never a re-joined token approximation). Hard-guard a quote char
        # and `_is_compound_object`: a quoted-ability grant ('… can attack" and has "…') / ' and '-joined
        # compound never grounds here (the `_grant_ability` quoted template / compound splitters own it).
        # A clause matching NEITHER regex (a 'can't …' restriction, a non-`_TGT` subject) -> abstain.
        src = getattr(self, "_src", None)
        if src is None:
            return None
        s = src.strip()
        if '"' in s or _is_compound_object(s):
            return None
        m = _GCC_ASTHOUGH.match(s)
        if m:
            return Effect("grant_ability", "-", _target(m.group(1)), "can_" + ground.slug(m.group(2)))
        m = _GCC_BLOCKMORE.match(s)
        if m:
            return Effect("grant_ability", "-", _target(m.group(1) or "self"), "can_block_" + ground.slug(m.group(2)))
        return None

    # --- SUBJECT-FIRST object verbs (sacrifice / exile) -----------------------
    def sfsubj(self, *toks):
        return _SfSubj(" ".join(str(t) for t in toks))

    def sfrest(self, *toks):
        return _SfRest(" ".join(str(t) for t in toks))   # value unused; the object is sliced from src

    def subjverb(self, *args):
        subj = next((str(a) for a in args if isinstance(a, _SfSubj)), None)
        # the SF_VERB Token, kept raw so we can slice the ORIGINAL object span by its end position (the
        # dynamic lexer may re-space tokens; slicing from `self._src` keeps the slug byte-identical).
        vtok = next((a for a in args
                     if not isinstance(a, (_SfSubj, _SfRest)) and "/" not in str(a)
                     and str(a).rstrip("s").lower() in ("sacrifice", "exile")), None)
        if subj is None or vtok is None:
            return None
        src = getattr(self, "_src", None)
        if src is None or not hasattr(vtok, "end_pos") or vtok.end_pos is None:
            return None
        s = subj.strip().lower()
        if not _SF_PLAYER.match(s):
            return None                            # subject isn't a clean closed player phrase -> abstain
        obj = src[vtok.end_pos:].strip()           # the exact post-verb object span (regex g-tail)
        if not obj:
            return None
        verb = str(vtok).rstrip("s").lower()       # 'sacrifices'->'sacrifice', 'exiles'->'exile'
        if verb == "exile":
            # `_subject_obj_verb`: Effect('exile','-',_target(obj),'by_'+_target(subj)); compound-guarded.
            # (The extra `_SF_AND_VERB` guard catches a '… and gets/loses …' run-on whose surface verb the
            # grounded-lemma `_is_compound_object` misses and which an earlier template may re-ground.)
            if _is_compound_object(obj) or _SF_AND_VERB.search(obj):
                return None                        # object runs into a 2nd effect -> regex chain owns it
            return Effect("exile", "-", _target(obj), "by_" + _target(s))
        # SACRIFICE. Template #1 (`_sacrifice_subj` it/that X/them/those X) is registered BEFORE the count
        # form #2, so it WINS on those four shapes: Effect('sacrifice','-',_target(obj),'by_'+_target(subj)).
        if _SF_SAC_NAMED.match(obj):
            return Effect("sacrifice", "-", _target(obj), "by_" + _target(s))
        # Template #2 (`_sacrifice_subj` count form): Effect('sacrifice','-',_target(subj),slug(obj)). The
        # regex split is `(a|an|one|two|three|\w+) (.+)` — the head is a SINGLE bare word (no hyphen, since
        # '\w' excludes '-') and there must be a non-empty tail. Reproduce that boundary exactly, then slug
        # the whole object (== slug(head+' '+tail)). The count form has NO compound guard in the regex, so a
        # run-on rider ('… and loses 10 life', '… then discards …', a comma list) is slugged WHOLE there
        # (a lossy tuple); we ABSTAIN on those (faithful-or-abstain — the regex fallback owns the lossy whole).
        parts = obj.split(" ", 1)
        head = parts[0]
        if len(parts) < 2 or not parts[1].strip():
            return None                            # no tail -> #2's `(.+)` can't match -> abstain
        if head not in ("a", "an", "one", "two", "three") and not _SF_WORD.match(head):
            return None                            # hyphenated/odd head -> #2's `\w+ ` split fails -> abstain
        if _is_compound_object(obj) or _SF_AND_VERB.search(obj):
            return None                            # run-on 2nd effect ('… and gets …', '… then …', ':') -> abstain
        if "," in obj:
            return None                            # comma list/rider the regex crams whole (lossy) -> abstain
        return Effect("sacrifice", "-", _target(s), ground.slug(obj))

    # --- PUT-TO-ZONE ----------------------------------------------------------
    def pzbody(self, *toks):
        return _PzBody(" ".join(str(t) for t in toks))

    def pzput(self, *args):
        # a leading 'put' imperative — reconstruct the full clause ('put ' + body) and dispatch to the
        # template-precedence frame regexes (faithful to parse_effect's put-family templates).
        body = next((str(a) for a in args if isinstance(a, _PzBody)), None)
        if body is None:
            return None
        # 'put a number of <PTDELTA> counters on <obj> equal to <X>' routes HERE (the PTDELTA kind makes the
        # putctr cclause fail, so pzbody greedily claims it) but is a put_counter, not a zone move — reproduce
        # `_put_counter_equal` from src before `_pz_frame` (which abstains on it). Mirror of the putctr branch.
        src = getattr(self, "_src", None)
        if src is not None:
            m = _PCE_RE.match(src.strip())
            if m:
                if _is_compound_object(m.group(2)):
                    return None
                k = m.group(1) if "/" in m.group(1) else ground.slug(m.group(1))
                return Effect("put_counter", "equal_to_" + ground.slug(m.group(3)), _target(m.group(2)), k)
            # REANIMATE 'put <obj> [from <zone>] onto the battlefield [riders]' -> return_to_battlefield. The
            # `_pz_frame` below abstains on 'onto the battlefield'; reproduce the registered `_reanimate_put`
            # verbatim (byte-identical to parse_effect).
            pm = _RPUT_RX.match(src.strip())
            if pm:
                e = _RPUT_FN(pm)
                if e is not None:
                    return e
            # LEADING-put 'Put <obj> into <zone>' -> put_in_graveyard (the registered `_put_zone`). EXCLUDE the
            # 'into <X> hand' result (put_in_hand): parse_effect ROUTES some 'Put <obj> into your hand' to an
            # EARLIER return_to_hand template, so reproducing _put_zone's put_in_hand there cross-verb-DIFFERs
            # (the genuine put_in_hand 'into hand' clauses already ground via `_pz_frame`). Graveyard is clean.
            zm = _PZ_RX.match(src.strip())
            if zm:
                e = _PZ_FN(zm)
                if e is not None and e.verb != "put_in_hand":
                    return e
        return _pz_frame("put " + body.strip())

    def pzhand(self, *args):
        # a leading 'conjure' clause ('conjure a card named X into your hand'). Reconstruct the full
        # clause ('conjure[s] ' + body) and run the _put_zone frame, which grounds it to put_in_hand only
        # if the body ENDS in 'into <owner> hand' (else abstains). The conjure verb token ('conjure' or
        # 'conjures') is recovered so the slug is byte-identical to the regex.
        body = next((str(a) for a in args if isinstance(a, _PzBody)), None)
        verb = next((str(a).lower() for a in args if not isinstance(a, _PzBody)), "conjure")
        if body is None:
            return None
        return _pz_frame(verb + " " + body.strip())

    # --- LOOK -----------------------------------------------------------------
    def lksubj(self, *toks):
        return _LkSubj(" ".join(str(t) for t in toks))   # the optional `_TGT` subject span (read by `look`)

    def lkbody(self, *toks):
        return _LkBody(" ".join(str(t) for t in toks))   # the post-verb 'at …' span (read by `look`)

    def look(self, *args):
        # TRUE grammar production: the LK_LOOK verb is the structural anchor that carves the clause into a
        # SUBJECT span (`lksubj`, optional) and a BODY span (`lkbody`, the 'at …' portion). The transformer
        # READS the two spans off the tree and applies the `_look_at`/`_look_top`/`_look_that_many`
        # templates as anchored per-operand validators (`_lk_frame`), byte-identical to parse_effect — no
        # whole-clause re-parse.
        #
        # SUBJECT GATE: the greedy `lksubj` can swallow a WHOLE preceding clause whose final word happens
        # to be 'look[s]' ('destroy target creature that looks …'), and the `_look_at` frame's leading
        # `(<TGT>)` would then mis-ground it as a LOOK. The regex never reaches `_look_at` for those —
        # an EARLIER template (destroy/…) claims the clause first. We reproduce that by requiring any
        # subject before the verb to be a clean closed `_PLAYER` phrase (a real player is the only thing
        # that 'looks'); anything else means the subject over-matched -> abstain (the regex chain owns it).
        subj = next((str(a) for a in args if isinstance(a, _LkSubj)), None)
        body = next((str(a) for a in args if isinstance(a, _LkBody)), None)
        if subj is not None and not _PLAYER.match(subj.strip().lower()):
            return None
        return _lk_frame(subj.strip() if subj is not None else None,
                         body.strip() if body is not None else None)

    # --- SHUFFLE --------------------------------------------------------------
    def shsubj(self, *toks):
        return _ShSubj(" ".join(str(t) for t in toks))   # the optional `_TGT` subject span (read by `shuffle`)

    def shbody(self, *toks):
        return _ShBody(" ".join(str(t) for t in toks))   # the post-verb source/library span (read by `shuffle`)

    def shuffle(self, *args):
        # TRUE grammar production: the SH_SHUFFLE verb is the structural anchor that carves the clause into
        # a SUBJECT span (`shsubj`, optional) and a BODY span (`shbody`, optional). The transformer READS
        # the two spans off the tree and applies the `_shuffle`/`_shuffle_subj` templates as anchored
        # per-operand validators (`_sh_frame`), byte-identical to parse_effect — no whole-clause re-parse.
        #
        # SUBJECT GATE (same hazard as `look`): the greedy `shsubj` can swallow a preceding clause ending
        # in 'shuffle[s]' ('destroy target creature that shuffles'), and `_shuffle`'s leading `(<TGT>)`
        # would mis-ground it as a SHUFFLE — but the regex reaches `_shuffle` only after the earlier
        # destroy/… templates fail. Require any subject before the verb to be a clean closed `_PLAYER`
        # phrase (only a player shuffles); else the subject over-matched -> abstain (regex chain owns it).
        subj = next((str(a) for a in args if isinstance(a, _ShSubj)), None)
        body = next((str(a) for a in args if isinstance(a, _ShBody)), None)
        if subj is not None and not _PLAYER.match(subj.strip().lower()):
            return None
        return _sh_frame(subj.strip() if subj is not None else None,
                         body.strip() if body is not None else None)

    # --- ADD_MANA -------------------------------------------------------------
    def amlead(self, *toks):
        return _AmLead(" ".join(str(t) for t in toks))    # the optional subject (validated/slugged below)

    def amrest(self, *toks):
        return _AmRest(" ".join(str(t) for t in toks))    # the mana-spec span (fed to _mana_production)

    def amadd(self, *args):
        # GRAMMAR-OWNED shape (`amlead? AM_ADD AM_ADDL? amrest`); the old whole-clause `_AM_FRAME` re-parse
        # is gone. The optional subject span `amlead` is VALIDATED with the anchored `_TGT` regex `_AM_TGT`
        # (exactly the frame's `(_TGT)?` subject group — a clean `_TGT` or abstain). The mana-spec span
        # `amrest` is fed to `_mana_production` (reused verbatim), with the mana-symbol glyphs re-uppercased
        # so the §107.4 colour lookup matches the lowercased source. `_mana_production` parses {…} symbols by
        # findall and English phrases by fullmatch, so the grounded `prod` (hence amount + extra) is
        # INDEPENDENT of the span's inter-token spacing — the tuple is byte-identical to the old
        # `_add_mana`/`_AM_FRAME` output, or abstain when the spec is rejected or the subject isn't `_TGT`.
        lead = next((str(a) for a in args if isinstance(a, _AmLead)), None)
        rest = next((str(a) for a in args if isinstance(a, _AmRest)), None)
        if rest is None:
            return None
        if lead is not None and not _AM_TGT.match(lead.strip()):
            return None
        prod = _mana_production(_am_upper_syms(rest.strip()))
        if not prod:
            return None
        return Effect("add_mana", len(prod), _target((lead or "you").strip()),
                      "_".join(dict.fromkeys(prod)))

    # --- CONDITIONAL/VARIABLE ADD_MANA (state-derived amounts, §106.3) ---------
    def amexpr(self, *toks):
        return _AmExpr(" ".join(str(t) for t in toks))    # the <expr>/<thing> span (slugged by ground.slug)

    def amxmana(self, *toks):
        return _AmXMana(" ".join(str(t) for t in toks))   # the 'X mana of any [one] color' span (validated below)

    def amadd_eq(self, *args):
        # SHAPE 1 'add an amount of <sym> equal to <expr>' (§106.3). Grammar owns the structure
        # (`amlead? AM_ADD AM_ADDL? AM_AMOUNTOF AM_MANASYM EQUALTO amexpr`); we read the single mana
        # symbol token + the <expr> SPAN off the tree. The color is taken from `_mana_production` (reused
        # — same §107.4 lookup as fixed mana), the amount is the repo-standard `equal_to_<slug(expr)>`
        # (IDENTICAL to deal_damage/gain_life/put_counter conditional amounts), target via _target.
        lead = next((str(a) for a in args if isinstance(a, _AmLead)), None)
        sym = next((str(a) for a in args
                    if not isinstance(a, (_AmLead, _AmExpr)) and str(a).startswith("{")), None)
        expr = next((str(a) for a in args if isinstance(a, _AmExpr)), None)
        if sym is None or not expr or not expr.strip():
            return None
        if lead is not None and not _AM_TGT.match(lead.strip()):
            return None
        prod = _mana_production(_am_upper_syms(sym.strip()))
        if not prod or len(prod) != 1:                     # exactly one mana type ('an amount of {G}')
            return None
        return Effect("add_mana", "equal_to_" + ground.slug(expr.strip()),
                      _target((lead or "you").strip()), prod[0])

    def amadd_wherex(self, *args):
        # SHAPE 2 'add X mana of any [one] color, where X is <expr>' (§106.3). Grammar owns the structure
        # (`amlead? AM_ADD AM_ADDL? amxmana AM_WHEREX amexpr`); the `amxmana` span must be exactly the
        # 'X mana of any color' / 'X mana of any one color' formal phrase (else abstain), and the <expr>
        # SPAN slugs to the amount `equal_to_<slug(expr)>`. Color slug mirrors _mana_production's
        # 'any_color' / 'any_one_color'.
        lead = next((str(a) for a in args if isinstance(a, _AmLead)), None)
        xmana = next((str(a) for a in args if isinstance(a, _AmXMana)), None)
        expr = next((str(a) for a in args if isinstance(a, _AmExpr)), None)
        if xmana is None or not expr or not expr.strip():
            return None
        if lead is not None and not _AM_TGT.match(lead.strip()):
            return None
        xs = xmana.strip().rstrip(",").strip()
        if re.fullmatch(r"x mana of any one color", xs, re.I):
            color = "any_one_color"
        elif re.fullmatch(r"x mana of any color", xs, re.I):
            color = "any_color"
        else:
            return None                                    # unrecognized X-mana phrase -> abstain
        return Effect("add_mana", "equal_to_" + ground.slug(expr.strip()),
                      _target((lead or "you").strip()), color)

    def amadd_foreach(self, *args):
        # SHAPE 3 'add <mana-spec> for each <thing>' (§106.3, e.g. 'Add {G} for each creature you control').
        # Grammar owns the structure (`amlead? AM_ADD AM_ADDL? amrest FOREACH amexpr`); the mana-spec
        # `amrest` is grounded by `_mana_production` (reused — colors byte-identical to fixed mana) and the
        # <thing> SPAN slugs to the count-scaled amount `<N>_per_<slug(thing)>` (N = symbol count, so a
        # single '{G}' -> '1_per_…', mirroring the create/draw/gain_life '_per_' convention exactly).
        lead = next((str(a) for a in args if isinstance(a, _AmLead)), None)
        rest = next((str(a) for a in args if isinstance(a, _AmRest)), None)
        thing = next((str(a) for a in args if isinstance(a, _AmExpr)), None)
        if rest is None or not thing or not thing.strip():
            return None
        if lead is not None and not _AM_TGT.match(lead.strip()):
            return None
        prod = _mana_production(_am_upper_syms(rest.strip()))
        if not prod:
            return None
        return Effect("add_mana", f"{len(prod)}_per_" + ground.slug(thing.strip()),
                      _target((lead or "you").strip()), "_".join(dict.fromkeys(prod)))


class _Zone:
    def __init__(self, verb):
        self.verb = verb


class _Trailer:
    pass


class _Creator(str):
    pass


class _Spec(str):
    pass


class _FEWord(str):
    pass


class _CTail(str):
    pass


_T = _ToEffect()


@functools.lru_cache(maxsize=None)
def parse_clause_lark(clause: str):
    """A card-effect clause -> Effect via the CFG, or None (abstain). Case-folded; the grammar owns the
    imperative core + zone-moves so far. MEMOIZED on the clause string (the Earley parse is the per-clause
    hot spot, and many clauses recur across cards) — pure, result consumed read-only."""
    s = clause.strip().rstrip(".").lower()
    if s[:1] == "•":                            # a leading modal/choice bullet ('• ~ deals N damage …') is
        s = s[1:].strip()                       # structural noise — strip it so the option body parses (the
        if not s:                               # regex leaf eats it via its '.+?' source; this matches that)
            return None
    if s.endswith('"') and s.count('"') % 2 == 1:   # an ORPHAN closing quote left by a split out of a quoted
        s = s[:-1].rstrip(".").strip()              # granted ability ('… you gain life equal to X."') — structural
                                                    # noise. Gated on an ODD '"' count so a BALANCED quote (the
                                                    # whole '~ gains "flying"' grant) is untouched. The regex slugs
                                                    # the stray '"' away too, so this matches it (or improves on a
                                                    # garbage grounding like 'regenerate ~."' -> '' vs 'self').
    if s.startswith("return ") and _ret_ambiguous(s):
        return None                            # ambiguous from/to split — defer to regex
    try:
        tree = _PARSER.parse(s)
    except Exception:
        return None
    _T._src = s                                # the lowercased source, so bcmbecomes can slice the raw P/T tail
    e = _T.transform(tree)
    e = e.children[0] if hasattr(e, "children") else e
    return e if isinstance(e, Effect) else None


@functools.lru_cache(maxsize=None)
def parse_clauses_lark(clause: str):
    """Like parse_clause_lark, but for the AST CONJUNCTION productions that ground a clause to SEVERAL
    effects (e.g. compound counters 'put <c1> <k1> and <c2> <k2> counters on <tgt>') — returns a LIST of
    Effects, or None. Consumed by card_effects.parse_clauses (the multi-effect entry); parse_clause stays
    single-Effect (it never sees a list)."""
    s = clause.strip().rstrip(".").lower()
    try:
        tree = _PARSER.parse(s)
    except Exception:
        return None
    _T._src = s
    e = _T.transform(tree)
    e = e.children[0] if hasattr(e, "children") else e
    return e if isinstance(e, list) and e and all(isinstance(x, Effect) for x in e) else None
