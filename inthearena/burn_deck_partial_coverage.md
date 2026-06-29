# Burn deck — partial coverage (covered, but the transpile dropped a clause)

Generated from inthearena/starting_burn.txt against the current corpus. Each card below HAS facts
(so a coverage check that counts 'has any fact' calls it covered) but the lark/transpile pipeline
ABSTAINED on a clause (`card_facts(...)` returned it in `dropped`), so a key behavior is missing
in-engine. Fixing a transpile PATTERN (a `kind` group below) usually fixes several cards at once.

Cards affected: 19/64 nonland-distinct deck cards.

## dropped kind: `effect` — Effect not interpreted (the spell/ability does nothing in-engine)  (10)

- **Blazing Volley** — dropped `('effect', 'deal_damage')`
  - oracle: Blazing Volley deals 1 damage to each creature your opponents control.
  - has: card_ability, card_color, card_effect, card_type
- **Cinder Strike** — dropped `('effect', 'deal_damage')`
  - oracle: As an additional cost to cast this spell, you may blight 1. (You may put a -1/-1 counter on a creature you control.) / Cinder Strike deals 2 damage to target creature. It deals 4 damage to that creature instead if this spell's additional cost was paid.
  - has: card_ability, card_color, card_effect, card_type
- **Electro, Assaulting Battery** — dropped `('effect', 'deal_damage')`
  - oracle: Flying / You don't lose unspent red mana as steps and phases end. / Whenever you cast an instant or sorcery spell, add {R}. / When Electro leaves the battlefield, you may pay {X}. When you do, he deals X damage to target player.
  - has: ability_trigger, card_ability, card_color, card_effect, card_keyword, card_power, card_subtype, card_toughness, card_type, has_supertype, trigger_effect
- **Heartfire Immolator** — dropped `('effect', 'deal_damage')`
  - oracle: Prowess (Whenever you cast a noncreature spell, this creature gets +1/+1 until end of turn.) / {R}, Sacrifice this creature: It deals damage equal to its power to target creature or planeswalker.
  - has: card_ability, card_color, card_effect, card_keyword, card_power, card_subtype, card_toughness, card_type
- **Improvisation Capstone** — dropped `('effect', 'cast')`
  - oracle: Exile cards from the top of your library until you exile cards with total mana value 4 or greater. You may cast any number of spells from among them without paying their mana costs. / Paradigm (Then exile this spell. After you first resolve a spell with this name, you may cast a copy of it from exile without paying its mana cost at the beginning of each of your first main phases.)
  - has: card_ability, card_color, card_effect, card_keyword, card_subtype, card_type
- **Mizzix's Mastery** — dropped `('effect', 'cast')`
  - oracle: Exile target card that's an instant or sorcery from your graveyard. For each card exiled this way, copy it, and you may cast the copy without paying its mana cost. Exile Mizzix's Mastery. / Overload {5}{R}{R}{R} (You may cast this spell for its overload cost. If you do, change "target" in its text to "each.")
  - has: card_ability, card_color, card_effect, card_keyword, card_type, keyword_param, spell_effect
- **Rotisserie Elemental** — dropped `('effect', 'play')`
  - oracle: Menace / Whenever this creature deals combat damage to a player, put a skewer counter on this creature. Then you may sacrifice it. If you do, exile the top X cards of your library, where X is the number of skewer counters on this creature. You may play those cards this turn.
  - has: ability_trigger, card_ability, card_color, card_effect, card_keyword, card_power, card_subtype, card_toughness, card_type, trigger_effect
- **The Fire Crystal** — dropped `('effect', 'sacrifice')`
  - oracle: Red spells you cast cost {1} less to cast. / Creatures you control have haste. / {4}{R}{R}, {T}: Create a token that's a copy of target creature you control. Sacrifice it at the beginning of the next end step.
  - has: activated_ability, card_ability, card_color, card_effect, card_type, cost_reducer, has_supertype

## dropped kind: `event` — Trigger condition not recognized (the trigger never fires)  (6)

- **Byway Barterer** — dropped `('event', 'you_expend_4')`
  - oracle: Menace / Whenever you expend 4, you may discard your hand. If you do, draw two cards. (You expend 4 as you spend your fourth total mana to cast spells during a turn.)
  - has: ability_trigger, card_ability, card_color, card_effect, card_keyword, card_power, card_subtype, card_toughness, card_type
- **Doublecast** — dropped `('event', 'you_next_cast_an_instant_or_sorcery_spell_this_turn')`
  - oracle: When you next cast an instant or sorcery spell this turn, copy that spell. You may choose new targets for the copy.
  - has: ability_trigger, card_ability, card_color, card_effect, card_type
- **Dual Strike** — dropped `('event', 'you_next_cast_an_instant_or_sorcery_spell_with_mana_value_4_or_less_this_turn')`
  - oracle: When you next cast an instant or sorcery spell with mana value 4 or less this turn, copy that spell. You may choose new targets for the copy. / Foretell {R} (During your turn, you may pay {2} and exile this card from your hand face down. Cast it on a later turn for its foretell cost.)
  - has: ability_trigger, card_ability, card_color, card_effect, card_keyword, card_type, keyword_param
- **Howl of the Horde** — dropped `('event', 'you_next_cast_an_instant_or_sorcery_spell_this_turn')`
  - oracle: When you next cast an instant or sorcery spell this turn, copy that spell. You may choose new targets for the copy. / Raid — If you attacked this turn, when you next cast an instant or sorcery spell this turn, copy that spell an additional time. You may choose new targets for the copy.
  - has: ability_trigger, card_ability, card_color, card_effect, card_type
- **Teapot Slinger** — dropped `('event', 'you_expend_4')`
  - oracle: Menace / Whenever you expend 4, this creature deals 2 damage to each opponent. (You expend 4 as you spend your fourth total mana to cast spells during a turn.)
  - has: ability_trigger, card_ability, card_color, card_effect, card_keyword, card_power, card_subtype, card_toughness, card_type
- **Weftstalker Ardent** — dropped `('event', 'another_creature_or_artifact_you_control_enters')`
  - oracle: Whenever another creature or artifact you control enters, this creature deals 1 damage to each opponent. / Warp {R} (You may cast this card from your hand for its warp cost. Exile this creature at the beginning of the next end step, then you may cast it from exile on a later turn.)
  - has: ability_trigger, card_ability, card_color, card_effect, card_keyword, card_power, card_subtype, card_toughness, card_type, keyword_param

## dropped kind: `cost_modifier` — Cost reduction/increase not applied  (2)

- **Diary of Dreams** — dropped `('cost_modifier', ('less', '1', 'activated_ability'))`
  - oracle: Whenever you cast an instant or sorcery spell, put a page counter on this artifact. / {5}, {T}: Draw a card. This ability costs {1} less to activate for each page counter on this artifact.
  - has: ability_trigger, activated_ability, card_ability, card_effect, card_subtype, card_type, trigger_effect
- **Hazoret's Monument** — dropped `('cost_modifier', ('less', '1', 'red_creature_spells_you_cast'))`
  - oracle: Red creature spells you cast cost {1} less to cast. / Whenever you cast a creature spell, you may discard a card. If you do, draw a card.
  - has: ability_trigger, card_ability, card_effect, card_type, has_supertype

## dropped kind: `static` — Static ability not applied  (1)

- **Electro, Assaulting Battery** — dropped `('static', 'retain_mana')`
  - oracle: Flying / You don't lose unspent red mana as steps and phases end. / Whenever you cast an instant or sorcery spell, add {R}. / When Electro leaves the battlefield, you may pay {X}. When you do, he deals X damage to target player.
  - has: ability_trigger, card_ability, card_color, card_effect, card_keyword, card_power, card_subtype, card_toughness, card_type, has_supertype, trigger_effect

## dropped kind: `static_scope` — Static buff scope not resolved  (1)

- **Heraldic Banner** — dropped `('static_scope', 'creatures_you_control_of_the_chosen_color')`
  - oracle: As this artifact enters, choose a color. / Creatures you control of the chosen color get +1/+0. / {T}: Add one mana of the chosen color.
  - has: card_ability, card_effect, card_type, mana_source

## dropped kind: `scope` — Target/selection scope not resolved  (2)

- **Improvisation Capstone** — dropped `('scope', 'cards_from_the_top_of_your_library_until_you_exile_cards_with_total_mana_value_4_or_greater')`
  - oracle: Exile cards from the top of your library until you exile cards with total mana value 4 or greater. You may cast any number of spells from among them without paying their mana costs. / Paradigm (Then exile this spell. After you first resolve a spell with this name, you may cast a copy of it from exile without paying its mana cost at the beginning of each of your first main phases.)
  - has: card_ability, card_color, card_effect, card_keyword, card_subtype, card_type
- **Mizzix's Mastery** — dropped `('scope', 'target_card_that_s_an_instant_or_sorcery_from_your_graveyard')`
  - oracle: Exile target card that's an instant or sorcery from your graveyard. For each card exiled this way, copy it, and you may cast the copy without paying its mana cost. Exile Mizzix's Mastery. / Overload {5}{R}{R}{R} (You may cast this spell for its overload cost. If you do, change "target" in its text to "each.")
  - has: card_ability, card_color, card_effect, card_keyword, card_type, keyword_param, spell_effect

## dropped kind: `enters_with_counters` — Enters-with-counters not applied  (1)

- **Axiom Engraver** — dropped `('enters_with_counters', ('oil', 'two'))`
  - oracle: This creature enters with two oil counters on it. / {T}, Remove an oil counter from this creature, Discard a card: Draw a card.
  - has: activated_ability, card_ability, card_color, card_effect, card_power, card_subtype, card_toughness, card_type

## dropped kind: `modify_pt_amt` — P/T modification not applied  (1)

- **Ancestral Anger** — dropped `('modify_pt_amt', '+X/+0')`
  - oracle: Target creature gains trample and gets +X/+0 until end of turn, where X is 1 plus the number of cards named Ancestral Anger in your graveyard. / Draw a card.
  - has: card_ability, card_color, card_effect, card_type
