"""test_doubling.py — §614 replacement DOUBLERS (token + +1/+1 counter doubling): Doubling Season,
Parallel Lives, Anointed Procession, Primal Vigor. Verified perfect + imperfect information.
Run: python3 test_doubling.py"""
import sim, bridge_to_engine as bridge, card_corpus, driver, observe, effect_handlers
effect_handlers.load()

_ok = [0, 0]
def check(name, cond):
    _ok[0] += 1; _ok[1] += bool(cond)
    print(("  ok  " if cond else " FAIL"), name)

# --- interpreter -> bridge: real doublers emit the driver-only `doubler` fact -----------------------
db = sim.load_db(); corpus = {c["name"]: c for c in card_corpus.load_cards()}
def _doubler_rows(name):
    facts, _ = bridge.card_facts(name, "alice", "x1", db, corpus)
    return facts.get("doubler", set())
check("Doubling Season emits doubler tokens", ("doubling_season", "tokens") in _doubler_rows("Doubling Season"))
check("Parallel Lives emits doubler tokens", ("parallel_lives", "tokens") in _doubler_rows("Parallel Lives"))
check("Primal Vigor emits doubler counters", ("primal_vigor", "counters") in _doubler_rows("Primal Vigor"))

# --- TOKEN doubling at _create_token ---------------------------------------------------------------
def _tok_state(n_doublers):
    s = {"is_player": {("alice",), ("bob",)}, "on_battlefield": set(), "printed_control": set(),
         "instance_of": set(), "doubler": set(), "counter": set(), "_tok": 0}
    for i in range(n_doublers):
        d = f"pl{i}"; s["on_battlefield"].add((d,)); s["printed_control"].add(("alice", d))
        s["instance_of"].add((d, "parallel_lives")); s["doubler"].add(("parallel_lives", "tokens"))
    return s
def _ntok(s):
    return sum(1 for (c,) in s["on_battlefield"] if c.startswith("1_1_white_soldier"))

s = _tok_state(0); driver._create_token(s, "1_1_white_soldier_creature", "alice", 1)
check("no doubler: create 1 -> 1 token", _ntok(s) == 1)
s = _tok_state(1); driver._create_token(s, "1_1_white_soldier_creature", "alice", 1)
check("one doubler: create 1 -> 2 tokens", _ntok(s) == 2)
s = _tok_state(2); driver._create_token(s, "1_1_white_soldier_creature", "alice", 1)
check("two doublers stack multiplicatively: create 1 -> 4 tokens", _ntok(s) == 4)
s = _tok_state(1); driver._create_token(s, "1_1_white_soldier_creature", "alice", 3)
check("one doubler: create 3 -> 6 tokens", _ntok(s) == 6)
# a doubler the OPPONENT controls doesn't double alice's tokens
s = _tok_state(1); s["printed_control"] = {("bob", "pl0")}; driver._create_token(s, "1_1_white_soldier_creature", "alice", 1)
check("opponent's doubler doesn't double your tokens", _ntok(s) == 1)

# --- +1/+1 COUNTER doubling at _bump_counter ------------------------------------------------------
def _ctr_state(kind="counters"):
    return {"is_player": {("alice",), ("bob",)}, "on_battlefield": {("cc",), ("pv",)},
            "printed_control": {("alice", "cc"), ("alice", "pv")},
            "instance_of": {("pv", "primal_vigor")}, "doubler": {("primal_vigor", kind)}, "counter": set()}
def _ctr(s, obj):
    return next((n for (o, k, n) in s["counter"] if o == obj and k == "p1p1"), 0)

s = _ctr_state(); driver._bump_counter(s, "cc", "p1p1", 1)
check("counter doubler: put 1 +1/+1 -> 2", _ctr(s, "cc") == 2)
s = _ctr_state(); driver._bump_counter(s, "cc", "m1m1", 1)
check("counter doubler does NOT double -1/-1 counters", next((n for (o, k, n) in s["counter"] if o == "cc" and k == "m1m1"), 0) == 1)
s = {"is_player": {("alice",)}, "on_battlefield": {("cc",)}, "printed_control": {("alice", "cc")},
     "instance_of": set(), "doubler": set(), "counter": set()}
driver._bump_counter(s, "cc", "p1p1", 1)
check("no doubler: put 1 +1/+1 -> 1", _ctr(s, "cc") == 1)

# --- BOTH info modes: doubled tokens/counters are PUBLIC, seen by both seats -----------------------
s = _tok_state(1); driver._create_token(s, "1_1_white_soldier_creature", "alice", 1)
va = observe.observe(s, "alice"); vb = observe.observe(s, "bob")
check("imperfect info: opponent sees BOTH doubled tokens on the battlefield",
      sum(1 for (c,) in vb.get("on_battlefield", set()) if c.startswith("1_1_white_soldier")) == 2)
check("imperfect info: controller sees them too", _ntok(va) == 2 if False else
      sum(1 for (c,) in va.get("on_battlefield", set()) if c.startswith("1_1_white_soldier")) == 2)
s = _ctr_state(); driver._bump_counter(s, "cc", "p1p1", 1)
check("imperfect info: opponent sees the doubled +1/+1 counters (public)",
      ("cc", "p1p1", 2) in observe.observe(s, "bob").get("counter", set()))

# --- LIFE-GAIN doubling at _adjust_life (Alhammarret's Archive / Rhox Faithmender) -----------------
check("Alhammarret's Archive emits life_repl double", ("alhammarret_s_archive", "double") in
      bridge.card_facts("Alhammarret's Archive", "alice", "x1", db, corpus)[0].get("life_repl", set()))

def _life_state(kind):
    return {"is_player": {("alice",), ("bob",)}, "life": {("alice", 20), ("bob", 20)},
            "on_battlefield": {("arch",)}, "printed_control": {("alice", "arch")},
            "instance_of": {("arch", "alhammarret_s_archive")}, "life_repl": {("alhammarret_s_archive", kind)}}
def _life(s, p):
    return next(v for (q, v) in s["life"] if q == p)

s = _life_state("double"); driver._adjust_life(s, "alice", 3)
check("life doubler: gain 3 -> +6 (life 26)", _life(s, "alice") == 26)
s = _life_state("double"); driver._adjust_life(s, "alice", -3)
check("life doubler does NOT affect life LOSS (lose 3 -> 17)", _life(s, "alice") == 17)
s = _life_state("plus1"); driver._adjust_life(s, "alice", 3)
check("life plus1: gain 3 -> +4 (life 24)", _life(s, "alice") == 24)
# the opponent's archive doesn't double alice's gain
s = _life_state("double"); s["printed_control"] = {("bob", "arch")}; driver._adjust_life(s, "alice", 3)
check("opponent's life doubler doesn't double your gain (life 23)", _life(s, "alice") == 23)
# no replacement -> plain gain
s = {"is_player": {("alice",)}, "life": {("alice", 20)}, "on_battlefield": set(), "printed_control": set(), "instance_of": set()}
driver._adjust_life(s, "alice", 3)
check("no life replacement: gain 3 -> 23", _life(s, "alice") == 23)

print(f"\n{_ok[1]}/{_ok[0]} checks passed")
import sys; sys.exit(0 if _ok[1] == _ok[0] else 1)
