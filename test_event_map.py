"""test_event_map.py — new TRIGGER event mappings (the 'event' dropped-bucket lever): each-player upkeep/
end-step, attacks-or-blocks, your-second-draw, and 'is put into a graveyard' = dies. Verifies firing
SEMANTICS and that the bridge↔datalog event_map stays in sync. Run: python3 test_event_map.py"""
import re, driver, bridge_to_engine as bridge

_ok = [0, 0]
def check(name, cond):
    _ok[0] += 1; _ok[1] += bool(cond)
    print(("  ok  " if cond else " FAIL"), name)

def _base(phrase, verb="draw", amt="1", tgt="you", extra="-"):
    return {"is_player": {("alice",), ("bob",)},
            "on_battlefield": {("src",)}, "printed_type": {("src", "creature")},
            "printed_control": {("alice", "src")},
            "instance_of": {("src", "sl")}, "card_ability": {("sl", "a0", "triggered")},
            "ability_trigger": {("sl", "a0", phrase)},
            "card_effect": {("sl", "a0", 0, verb, amt, tgt, extra, "-")},
            "counter": set(), "tapped": set()}

def _fires(state):
    return any("src" in str(r) for r in driver.run(state, ["fires"]).get("fires", set()))

# --- bridge <-> datalog event_map MUST stay in sync (the table is shared) ---------------------------
eng = set(re.findall(r'event_map\(\s*"([^"]+)"', open("datalog/engine_rules.dl").read()))
br = set(bridge._EVENT.keys())
check("event_map in sync: bridge == datalog (no drift)", eng == br)

# --- each-player upkeep: fires on EVERY player's upkeep (vs your-upkeep only on yours) --------------
def _upkeep(active, phrase):
    s = _base(phrase); s["active_player"] = {(active,)}; s["current_step"] = {("upkeep",)}; return s
check("each_upkeep fires on YOUR upkeep", _fires(_upkeep("alice", "the_beginning_of_each_upkeep")))
check("each_upkeep fires on the OPPONENT's upkeep too", _fires(_upkeep("bob", "the_beginning_of_each_upkeep")))
check("your_upkeep fires on YOUR upkeep", _fires(_upkeep("alice", "the_beginning_of_your_upkeep")))
check("your_upkeep does NOT fire on the opponent's upkeep", not _fires(_upkeep("bob", "the_beginning_of_your_upkeep")))
check("each_opponent_upkeep fires on the OPPONENT's upkeep", _fires(_upkeep("bob", "the_beginning_of_each_opponent_s_upkeep")))
check("each_opponent_upkeep does NOT fire on your own upkeep", not _fires(_upkeep("alice", "the_beginning_of_each_opponent_s_upkeep")))

# --- attacks OR blocks: fires off either combat event ----------------------------------------------
def _combat(kind):
    s = _base("attacks_or_blocks"); s["active_player"] = {("alice",)}; s["current_step"] = {("combat_damage",)}
    s[kind] = {("src", "x")}; return s
check("attacks_or_blocks fires when src ATTACKS", _fires(_combat("attacks")))
check("attacks_or_blocks fires when src BLOCKS", _fires(_combat("blocks")))
check("attacks_or_blocks does NOT fire with no combat", not _fires(_base("attacks_or_blocks")))

# --- you draw your SECOND card each turn: the controller's 2nd draw -------------------------------
def _draw(n):
    s = _base("you_draw_your_second_card_each_turn"); s["just_drew"] = {("alice",)}
    s["draw_ord"] = {("alice", i) for i in range(1, n + 1)}; return s
check("you_draw_second fires on the 2nd draw (draw_ord 2)", _fires(_draw(2)))
check("you_draw_second does NOT fire on only the 1st draw", not _fires(_draw(1)))

# --- 'is put into a graveyard from the battlefield' == dies (dies is DERIVED from lethal toughness) -
def _dying(phrase, lethal):
    s = _base(phrase)
    s["printed_power"] = {("src", 1)}
    s["printed_toughness"] = {("src", 0 if lethal else 1)}    # 0 toughness -> dies as an SBA -> ev_dies
    return s
check("'put into graveyard from battlefield' fires as dies (lethal)", _fires(_dying("is_put_into_a_graveyard_from_the_battlefield", True)))
check("the alias matches the plain 'dies' mapping (both fire when lethal)", _fires(_dying("dies", True)))
check("...does NOT fire when src is healthy (toughness 1)", not _fires(_dying("is_put_into_a_graveyard_from_the_battlefield", False)))

print(f"\n{_ok[1]}/{_ok[0]} checks passed")
import sys; sys.exit(0 if _ok[1] == _ok[0] else 1)
