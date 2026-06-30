"""interpreter — the English→Datalog transpilation pipeline.

Parses the MTG Comprehensive Rules (rules_parser) and card oracle text (card_corpus)
through the lark grammars + spaCy transpiler (transpile, transpile_card, card_effects,
ground) and the build_* rule emitters into the engine's Datalog (dlgen). The runtime
that EXECUTES that Datalog (driver, engine_*, sim, bridge_to_engine) stays at the repo root.
"""
