"""effect_handlers/no_untap.py — §702.x / §508-ish "doesn't untap during its controller's untap step".

WHAT — a permanent that "doesn't untap during its controller's untap step." ~134 cards carry the
`doesnt_untap` verb. There are TWO timing flavors, distinguished cleanly by the cards.dl `extra` column:

  * CONTINUOUS (extra == "-"): a static on the permanent — it just never untaps while this is in effect
    (Mana Vault / Basalt Monolith / Colossus of Sardia 'self'; or 'it'/'that permanent' the ability locked
    down, e.g. Amber Prison, Whip Vine). This is the FAITHFUL slice we handle: a driver-only `doesnt_untap`
    set keyed by instance id; the driver's untap step (§502.3) skips untapping anything in it.

  * ONE-SHOT / "during your next untap step" (extra == "next"): 'X doesn't untap during YOUR next untap
    step' — a delayed, turn-scoped lock that fires once on a SPECIFIC upcoming untap step and then clears.
    Modeling that needs the 'whose next untap step / consume-on-fire' timing we don't track here, so we
    ABSTAIN on it rather than turn it into a permanent never-untaps (which would be WRONG).

This driver-only `doesnt_untap` set is BOARD STATE (a permanent's tapped/untap status is public, §122), so it
reads identically in perfect and imperfect information; observe keeps it visible (it lives on public ids).

TARGET SCOPE — faithful on the SELF / anaphoric shapes that resolve to the SOURCE permanent without real
targeting: 'self'/'it'/'them'/'itself'. These are the self-locking statics (Mana Vault-style) and the
single-source anaphora ('~ doesn't untap …', '… and it doesn't untap'), where the locked permanent IS the
ability's source. A 'target_*' / 'that_creature' shape needs a RESOLVED target instance that the player-
scoped trigger_effect/pending path doesn't carry here, so we ABSTAIN rather than mismark the source.

NOTE: cards.dl ALSO carries `doesnt_untap(card, who)` as STATIC EDB facts (Mana Vault 'self' etc.) on a
separate, non-card_effect path; those statics are interpreted but inert in the engine and are out of scope
for this handler (they'd need an engine-input change). This handler owns the card_effect VERB path only.
"""

from effect_handlers import encoder, applier

# anaphoric / self shapes -> the locked permanent IS the ability's SOURCE
_SELF_TGT = {"self", "it", "them", "itself"}


@encoder("doesnt_untap")
def encode(verb, amt, tgt, extra):
    # ABSTAIN on the one-shot 'during your NEXT untap step' timing (extra=="next") — we only model the
    # CONTINUOUS lock faithfully. (extra is the cards.dl 4th column; "-" = continuous static.)
    if str(extra) == "next":
        return None
    if str(tgt) in _SELF_TGT:
        return ("doesnt_untap", 0, "self")
    return None   # target_*/that_*/plural/board-scope -> needs a resolved target this path lacks, abstain


@applier("doesnt_untap")
def apply(D, state, a, n, tgt, src, ctrl):
    """Flag the SOURCE permanent so the driver's untap step (§502.3) never untaps it."""
    state.setdefault("doesnt_untap", set()).add((src,))
    print(f"    {a}: {src} doesn't untap during its controller's untap step")
