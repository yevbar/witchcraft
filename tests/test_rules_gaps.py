"""Regression coverage for the rules-update audit. Run directly with Python."""
import unittest
from unittest.mock import patch
from test_rules_2026 import state, creature, parsed_card, D, rules_2026, effect_handlers

class RulesGaps(unittest.TestCase):
    def test_leading_duration_team_buff_is_a_spell(self):
        rows, drops = parsed_card('Until end of turn, creatures you control get +1/+1 and gain trample and infect.', types=['Sorcery'])
        self.assertFalse(drops)
        self.assertTrue(all(kind == 'spell' for _, _, kind in rows['card_ability']))
        effects = D.run(rows, ['spell_scope'])['spell_scope']
        self.assertIn(('probe', 'modify_pt', '1/1', 'creatures_you_control'), effects)
        self.assertIn(('probe', 'grant', 'infect', 'creatures_you_control'), effects)
        self.assertIn(('probe', 'grant', 'trample', 'creatures_you_control'), effects)

    def test_triggered_permanent_scope_selects_noncreature_artifacts(self):
        s = state(); creature(s, 'source'); creature(s, 'survivor', 'bob')
        for obj, typ in [('rock', 'artifact'), ('land', 'land')]:
            s['on_battlefield'].add((obj,)); s['printed_type'].add((obj, typ))
            s['printed_control'].add(('bob', obj))
        s['current_step'] = {('upkeep',)}
        s['has_trigger'] = {('wipe', 'source', 'upkeep')}
        s['trigger_effect_destroy'] = {('wipe', 'all_artifacts')}
        pending = D.run(s, ['pending_destroy'])['pending_destroy']
        self.assertEqual(pending, {('wipe', 'rock', 'alice')})
        D._apply_creature_effects(s)
        self.assertNotIn(('rock',), s['on_battlefield'])
        self.assertIn(('land',), s['on_battlefield'])
        self.assertIn(('survivor',), s['on_battlefield'])

    def test_temporary_board_ability_loss_expires(self):
        s = state(); creature(s, 'source')
        s['printed_keyword'] = {('source', 'flying')}
        s['instance_of'] = {('spell', 'strip')}
        s['card_ability'] = {('strip', 'a', 'spell')}
        s['card_effect'] = {('strip', 'a', 0, 'lose_abilities', '-', 'all_creatures', '-', 'until_end_of_turn')}
        effects = D.run(s, ['spell_effect'])['spell_effect']
        self.assertIn(('spell', 'lose_abilities_scope', '0', 'all_creatures|eot'), effects)
        effect_handlers.APPLY['lose_abilities_scope'](D, s, 'spell', 0, 'all_creatures|eot', 'spell', 'alice')
        self.assertNotIn(('source', 'flying'), D.run(s, ['has_keyword'])['has_keyword'])
        D._end_of_turn(s)
        self.assertIn(('source', 'flying'), D.run(s, ['has_keyword'])['has_keyword'])

    def test_power_up_x_choices_and_independent_stack_values(self):
        from mtg.engine.env import _activate_choices
        s = state(); creature(s, 'probe')
        rows, drops = parsed_card('Power-up — {X}: Put X +1/+1 counters on ~.', mana='{1}')
        self.assertFalse(drops)
        for k, v in rows.items(): s.setdefault(k, set()).update(v)
        s['power_up_extra_activation'] = {('probe',)}
        D._set_floating(s, 'alice', {'blue': 5}); D._refresh_mana_pool(s, 'alice')
        row = D._activatable(s, 'alice')[0]
        self.assertEqual(_activate_choices(s, row), [{'power_up_x': n} for n in range(6)])
        for x in (2, 3):
            s['_forced'] = {'power_up_x': x}
            rules_2026.pay_activation(D, s, 'alice', row)
            D.put_activation(s, row[0], row[4], row[5], row[6], row[1], 'alice')
        self.assertEqual(len(s['on_stack']), 2)
        D._resolve_top(s); D._resolve_top(s)
        self.assertIn(('probe', 'p1p1', 5), s['counter'])

    def test_teamwork_declined_does_not_require_rider_target(self):
        from mtg.engine.env import _cast_choices
        s = state()
        rows, drops = parsed_card('Teamwork 2\nDraw a card. If this spell was cast using teamwork, put a +1/+1 counter on target creature.', types=['Sorcery'])
        self.assertFalse(drops)
        for k, v in rows.items(): s.setdefault(k, set()).update(v)
        self.assertEqual(_cast_choices(s, 'probe'), [{'teamwork': False}])
        creature(s, 'hero')
        choices = _cast_choices(s, 'probe')
        self.assertIn({'teamwork': False}, choices)
        self.assertIn({'teamwork': True, 'target': 'hero'}, choices)

    def test_card_copies_choose_cast_independently(self):
        from mtg.card_copies import cast_copies
        s = state(); s['instance_of'] = {('original', 'card')}; s['card_type'] = {('card', 'sorcery')}
        decisions = iter([True, False, True])
        # Use the common decision seam while leaving cast-trigger choices at their defaults.
        with patch.object(D, '_choose', side_effect=lambda st, key, opts, default: next(decisions) if key == 'cast_card_copy' else default):
            made = cast_copies(D, s, ['original'] * 3, 'alice')
        self.assertEqual(len(made), 2)
        self.assertEqual(len(s['on_stack']), 2)
        self.assertEqual(s['_cast_count'], 2)
        self.assertFalse(s['exile'])

    def test_departed_players_last_turn_expires_at_their_scheduled_turn(self):
        from mtg import turn_history as history
        s = state(); s['is_player'].add(('carol',)); s['_turn_order'] = ['alice', 'bob', 'carol']
        history.record(s, 'bob', 'cast', 'spell'); history.finish(s, 'bob')
        history.leave(s, 'bob')
        self.assertTrue(history.last_turn(s, 'bob'))
        self.assertEqual(D._next_active_player(s, 'carol', ['alice', 'carol']), 'alice')
        self.assertTrue(history.last_turn(s, 'bob'))
        self.assertEqual(D._next_active_player(s, 'alice', ['alice', 'carol']), 'carol')
        self.assertFalse(history.last_turn(s, 'bob'))
        self.assertIn(('bob', 'cast', 'spell'), s['_game_actions'])

    def test_departed_damage_source_uses_last_keywords_and_controller(self):
        for keyword in ('deathtouch', 'lifelink', 'wither', 'infect'):
            s = state(); creature(s, 'source'); creature(s, 'target', 'bob')
            s['eff_grant_keyword'] = {('grant', 'source', keyword)}
            D._sacrifice(s, 'source')
            s['eff_grant_keyword'] = set()
            D._apply_damage(s, 'ability', 1, 'creature_fixed:target', 'alice', 'source')
            if keyword == 'deathtouch':
                self.assertNotIn(('target',), s['on_battlefield'])
            elif keyword == 'lifelink':
                self.assertIn(('alice', 21), s['life'])
            else:
                self.assertIn(('target', 'm1m1', 1), s['counter'])
                self.assertNotIn(('target', 1), s.get('marked_damage', set()))
        D._apply_damage(s, 'ability', 2, 'face', 'alice', 'source')
        self.assertIn(('bob', 2), s['poison'])
        self.assertIn(('bob', 20), s['life'])

    def test_multiple_optional_payments_trigger_reflexive_once(self):
        s = state(); creature(s, 'probe')
        rows, drops = parsed_card('At the beginning of your upkeep, you may pay {1} any number of times. When you do, you gain 1 life.')
        self.assertFalse(drops)
        for k, v in rows.items(): s.setdefault(k, set()).update(v)
        s['current_step'] = {('upkeep',)}
        D._set_floating(s, 'alice', {'blue': 3}); D._refresh_mana_pool(s, 'alice')
        s['_forced'] = {'you_do_pay_count': 3}
        D._fire_you_do_costs(s)
        self.assertIn(('alice', 21), s['life'])
        self.assertFalse(D._floating(s, 'alice'))

    def test_conditional_flash_survives_permission_lost_during_payment(self):
        s = state(); creature(s, 'legend'); s['has_supertype'] = {('legend', 'legendary')}
        rows, drops = parsed_card('You may cast creature spells as though they had flash if you control a legendary creature.')
        self.assertFalse(drops)
        for k, v in rows.items(): s.setdefault(k, set()).update(v)
        s['on_battlefield'].add(('probe',)); s['active_player'] = {('bob',)}
        s['in_hand'] = {('alice', 'spell')}; s['spell_type'] = {('spell', 'creature')}
        s['mana_cost'] = {('spell', 0)}; s['mana_available'] = {('alice', 0)}
        self.assertIn(('alice', 'spell'), D.run(s, ['can_cast'])['can_cast'])
        def pay(state, player, spell):
            D._sacrifice(state, 'legend')
        with patch.object(D, '_spend_mana', side_effect=pay):
            D._cast_spell(s, 'alice', 'spell', ['alice', 'bob'])
        self.assertIn(('spell',), s['on_battlefield'])
        self.assertNotIn(('legend',), s['on_battlefield'])

    def test_crew_intervening_if_only_checks_this_activation(self):
        from mtg import crew
        s = state(); creature(s, 'dwarf'); creature(s, 'elf'); creature(s, 'probe')
        s['printed_subtype'] = {('dwarf', 'dwarf'), ('elf', 'elf')}
        rows, drops = parsed_card('Whenever ~ becomes crewed, if it was crewed by a Dwarf, you gain 1 life.')
        self.assertFalse(drops)
        for k, v in rows.items(): s.setdefault(k, set()).update(v)
        for c in ('dwarf', 'elf'):
            s['_forced'] = {'crew': frozenset({c})}
            crew.pay(D, s, 'activation', 'alice', 2)
            crew.resolve(D, s, 'activation', 'probe', 'alice')
        self.assertIn(('alice', 21), s['life'])

    def test_crew_activations_pay_and_keep_separate_attribution(self):
        from mtg import crew
        s = state(); creature(s, 'one'); creature(s, 'two')
        rows, drops = parsed_card('Crew 2', types=['Artifact'])
        self.assertFalse(drops)
        for k, v in rows.items(): s.setdefault(k, set()).update(v)
        s['on_battlefield'].add(('probe',))
        s['printed_type'] = {r for r in s['printed_type'] if r[0] != 'probe'} | {('probe', 'artifact')}
        s['printed_power'].add(('probe', 4)); s['printed_toughness'].add(('probe', 4))
        self.assertTrue(D._activatable(s, 'alice'))
        for chosen in ('one', 'two'):
            row = D._activatable(s, 'alice')[0]
            s['_forced'] = {'crew': frozenset({chosen})}
            rules_2026.pay_activation(D, s, 'alice', row)
            D.put_activation(s, row[0], row[4], row[5], row[6], row[1], 'alice')
        self.assertEqual(len(s['on_stack']), 2)
        self.assertEqual({next(iter(v['creatures'])) for v in s['_crew_payment'].values()}, {'one', 'two'})
        self.assertFalse(D._activatable(s, 'alice'))
        D._resolve_top(s)
        self.assertIn(('probe',), D.run(s, ['creature'])['creature'])
        self.assertEqual(len(s['_crew_payment']), 1)
        D._resolve_top(s)
        self.assertFalse(s['_crew_payment'])
        self.assertFalse(s['crewed_by'])

    def test_battle_zero_defense_sba_and_protector_combat(self):
        from mtg import battles
        s = state(); creature(s, 'attacker'); creature(s, 'blocker', 'bob')
        s['on_battlefield'].add(('battle',)); s['printed_type'].add(('battle', 'battle'))
        s['printed_control'].add(('alice', 'battle'))
        self.assertIn(('battle', 'battlefield', 'graveyard'), D.run(s, ['zone_change'])['zone_change'])
        s['printed_subtype'].add(('battle', 'siege')); s['counter'] = {('battle', 'defense', 3)}
        s['battle_protector'] = {('battle', 'bob')}
        s['current_step'] = {('combat_damage',)}; s['attacks'] = {('attacker', 'battle')}
        out = D.run(s, ['battle_damage'])
        self.assertEqual({(b, int(n)) for b, n in out['battle_damage']}, {('battle', 2)})
        battles.damage(D, s, 'battle', 3)
        self.assertFalse(D.run(s, ['zone_change'])['zone_change'])
        self.assertIn(('battle',), s['battle_trigger_pending'])
        battles.set_protector(s, 'battle', 'alice')
        self.assertEqual(s['attacks'], {('attacker', '__removed_from_combat__')})
        self.assertFalse(D.run(s, ['battle_damage'])['battle_damage'])
        D._resolve_top(s)
        self.assertIn(('battle',), s['exile'])

    def test_siege_back_cast_and_countered_defeat(self):
        from mtg import battles
        for countered in (False, True):
            s = state(); s['on_battlefield'].add(('battle',))
            s['printed_type'].add(('battle', 'battle')); s['printed_subtype'].add(('battle', 'siege'))
            s['printed_control'].add(('alice', 'battle')); s['counter'] = {('battle', 'defense', 1)}
            s['instance_of'] = {('battle', 'front')}; s['transform_target'] = {('battle', 'back')}
            s['card_type'] = {('back', 'creature')}; s['card_power'] = {('back', 4)}; s['card_toughness'] = {('back', 4)}
            battles.damage(D, s, 'battle', 1)
            if countered:
                D._stack_remove(s, 'battle__siege_defeat')
                self.assertIn(('battle', 'battlefield', 'graveyard'), D.run(s, ['zone_change'])['zone_change'])
            else:
                D._resolve_top(s)
                self.assertTrue(any(o == 'battle' for o, _ in s['on_stack']))
                self.assertEqual(s['_cast_count'], 1)
                D._resolve_top(s)
                self.assertIn(('battle',), s['on_battlefield'])
                self.assertIn(('battle', 'back'), s['instance_of'])

    def test_battle_entry_defense_and_protector(self):
        from mtg import battles
        s = state(); s['on_battlefield'].add(('battle',))
        s['printed_type'].add(('battle', 'battle')); s['printed_subtype'].add(('battle', 'siege'))
        s['printed_control'].add(('alice', 'battle'))
        s['instance_of'] = {('battle', 'front')}; s['card_defense'] = {('front', 5)}
        battles.enter(D, s, 'battle')
        self.assertIn(('battle', 'defense', 5), s['counter'])
        self.assertIn(('battle', 'bob'), s['battle_protector'])
        creature(s, 'attacker'); creature(s, 'wrong_blocker')
        s['attacks'] = {('attacker', 'battle')}; s['blocks'] = {('wrong_blocker', 'attacker')}
        self.assertIn(('wrong_blocker', 'attacker'), D.run(s, ['illegal_block'])['illegal_block'])

    def test_illegal_face_down_preserves_characteristics_including_merged_parts(self):
        import copy
        for relation, rows in [('cannot_turn_face_down', {('hero',)}),
                               ('transform_target', {('hero', 'back')})]:
            s = state(); creature(s, 'hero'); s[relation] = rows
            before = copy.deepcopy(s)
            self.assertFalse(D.turn_face_down(s, 'hero'))
            self.assertEqual(s, before)
        s = state(); creature(s, 'hero')
        s['merged_component'] = {('hero', 'dfc')}; s['cannot_turn_face_down'] = {('dfc',)}
        before = copy.deepcopy(s)
        self.assertFalse(D.turn_face_down(s, 'hero'))
        self.assertEqual(s, before)
        s['merged_component'] = set()
        self.assertTrue(D.turn_face_down(s, 'hero'))
        self.assertEqual({(c, int(n)) for c, n in D.run(s, ['eff_toughness'])['eff_toughness']}, {('hero', 2)})

    def test_granted_storied_persists_before_next_effect(self):
        s = state(); creature(s, 'hero')
        s['static_grant'] = {('hero', 'storied', 'self')}
        for c in ('a', 'b', 'c'):
            s['on_battlefield'].add((c,)); s['printed_control'].add(('alice', c))
            s['printed_type'].add((c, 'artifact'))
        D._pending_both(s)
        s['on_battlefield'] = set(); s['static_grant'] = set()
        self.assertIn(('alice',), D.run(s, ['has_enduring_story'])['has_enduring_story'])

    def test_connive_positive_event_after_impossible_actions_and_zero(self):
        s = state(); creature(s, 'probe')
        rows, drops = parsed_card('Whenever ~ connives, you gain 1 life.')
        self.assertFalse(drops)
        for k, v in rows.items(): s.setdefault(k, set()).update(v)
        effect_handlers.load()
        with patch.object(D, '_draw'):
            effect_handlers.APPLY['connive'](D, s, 'a', 0, 'self', 'probe', 'alice')
            self.assertIn(('alice', 20), s['life'])
            effect_handlers.APPLY['connive'](D, s, 'a', 1, 'self', 'probe', 'alice')
        self.assertIn(('alice', 21), s['life'])
        self.assertFalse(s['just_connived'])

    def test_connive_apnap_uses_captured_controller_for_departed_source(self):
        from effect_handlers.keyword_actions import connive_many
        s = state(); s['active_player'] = {('bob',)}
        s['_turn_order'] = ['alice', 'bob']
        calls = []
        with patch.object(D, '_draw', side_effect=lambda state, p: calls.append(p)):
            connive_many(D, s, 'a', 1, [('gone', 'alice'), ('also_gone', 'bob')])
        self.assertEqual(calls, ['bob', 'alice'])
        self.assertFalse(s['counter'])

    def test_unparsed_card_units_are_reported(self):
        from interpreter.build_cards import _process_chunk
        from mtg import bridge_to_engine as bridge
        card = {'name': 'Probe', 'types': ['Creature'], 'text': 'This is deliberately unsupported oracle prose.'}
        result = _process_chunk([card])[0]
        self.assertFalse(result[-1])
        self.assertTrue(any(row.startswith('card_unparsed(') for row in result[2]))
        _, drops = bridge.card_facts('Probe', 'alice', 'probe',
                                    {'probe': {'unparsed': [(0, card['text'])]}}, {'Probe': card})
        self.assertIn(('unparsed_unit', card['text']), drops)

    def test_power_up_x_selects_pays_and_resolves(self):
        for entered, expected in [(False, 3), (True, 5)]:
            s = state(); creature(s, 'probe')
            rows, drops = parsed_card('Power-up — {X}{U}{U}: Put X +1/+1 counters on ~.', '{1}{U}')
            self.assertFalse(drops)
            for k, v in rows.items(): s.setdefault(k, set()).update(v)
            if entered: rules_2026.entered(s, 'probe')
            D._set_floating(s, 'alice', {'blue': 5}); D._refresh_mana_pool(s, 'alice')
            D._activate_phase(s, 'alice', ['alice', 'bob'])
            if s.get('on_stack'): D._resolve_top(s)
            self.assertIn(('probe', 'p1p1', expected), s['counter'])
            self.assertFalse(D._floating(s, 'alice'))

    def test_power_up_modifiers_and_mana_ability(self):
        s = state(); creature(s, 'probe')
        for text, obj in [('Power-up — {3}{G}: Put two +1/+1 counters on ~.', 'probe'),
                          ('Each power-up ability of permanents you control can be activated an additional time.', 'wonder'),
                          ('Power-up abilities of other creatures you control cost {3} less to activate.', 'hulk')]:
            rows, drops = parsed_card(text)
            self.assertFalse(drops)
            for k, vals in rows.items():
                s.setdefault(k, set()).update(tuple(obj if v == 'probe' else v for v in row) for row in vals)
            s['on_battlefield'].add((obj,))
        aid = 'probe_a0'
        self.assertEqual(rules_2026.power_up_cost(s, aid), (0, {'green': 1}))
        for _ in range(2):
            D._set_floating(s, 'alice', {'green': 1}); D._refresh_mana_pool(s, 'alice')
            row = D._activatable(s, 'alice')[0]
            rules_2026.pay_activation(D, s, 'alice', row)
        self.assertFalse(D._activatable(s, 'alice'))
        s = state(); creature(s, 'probe')
        rows, drops = parsed_card('Power-up — {1}: Add {G}.')
        for k, v in rows.items(): s.setdefault(k, set()).update(v)
        self.assertFalse(rows.get('mana_source'))
        s['mana_available'] = {('alice', 1)}
        D._activate_phase(s, 'alice', ['alice', 'bob'])
        self.assertFalse(s.get('on_stack'))
        self.assertEqual(D._floating(s, 'alice'), {'green': 1})
        self.assertFalse(D._activatable(s, 'alice'))

    def test_hand_to_library_is_one_stack_activation(self):
        rows, drops = parsed_card('{T}: Add {G}. Put a card from your hand on top of your library.')
        self.assertFalse(drops)
        self.assertFalse(rows.get('mana_source'))
        s = state(); creature(s, 'probe')
        for k, v in rows.items(): s.setdefault(k, set()).update(v)
        s['in_hand'] = {('alice', 'card')}
        row = D._activatable(s, 'alice')[0]
        self.assertEqual(len(D._activatable(s, 'alice')), 1)
        a, src, cost, taps, eff, n, tgt = row
        D._stack_push(s, a, 'alice')
        s['_ability_effect'] = {a: (eff, n, tgt, src, 'alice')}
        D._resolve_top(s)
        self.assertEqual(D._floating(s, 'alice'), {'green': 1})
        self.assertIn(('alice', 'card'), s['in_library'])

    def test_heal_natural_wording_and_hexproof(self):
        rows, drops = parsed_card('{T}: Heal all damage on target creature.')
        self.assertFalse(drops)
        self.assertEqual(next(iter(rows['activated_ability']))[4], 'heal')
        s = state(); creature(s, 'hero')
        s['printed_keyword'] = {('hero', 'hexproof')}
        s['marked_damage'] = {('hero', 2)}
        effect_handlers.load()
        effect_handlers.APPLY['heal'](D, s, 'heal', 0, 'target_creature', 'spell', 'bob')
        self.assertEqual(s['marked_damage'], {('hero', 2)})
        effect_handlers.APPLY['heal'](D, s, 'heal', 0, 'target_creature', 'spell', 'alice')
        self.assertFalse(s['marked_damage'])

    def test_healing_replacement_removes_only_previous_damage(self):
        rows, drops = parsed_card('If damage would be dealt to ~, instead that damage is dealt, but all other damage already dealt to it is healed.')
        self.assertFalse(drops)
        s = state(); creature(s, 'probe'); creature(s, 'other')
        for k, v in rows.items(): s.setdefault(k, set()).update(v)
        s['marked_damage'] = {('probe', 2), ('other', 1)}
        D._apply_damage(s, 'hit', 2, 'creature_fixed:probe', 'bob')
        self.assertEqual(s['marked_damage'], {('probe', 2), ('other', 1)})
        self.assertIn(('probe',), s['on_battlefield'])
        D._apply_damage(s, 'lethal', 3, 'creature_fixed:probe', 'bob')
        self.assertNotIn(('probe',), s['on_battlefield'])

    def test_combat_heals_previous_damage(self):
        s = state(); creature(s, 'a'); creature(s, 'b', 'bob')
        s.update(current_step={('combat_damage',)}, attacks={('a', 'bob')}, blocks={('b', 'a')},
                 marked_damage={('a', 2)}, heal_previous_damage={('a',)})
        out = D.run(s, D.OUTPUTS)
        self.assertNotIn(('a', 'battlefield', 'graveyard'), out['zone_change'])
        D._apply_outputs(s, out, 'alice')
        self.assertIn(('a', 2), s['marked_damage'])

    def test_departed_explorer_gets_no_counter(self):
        s = state(); s['in_library'] = {('alice', 'top')}; s['_lib_order'] = {'alice': ['top']}
        s['spell_type'] = {('top', 'instant')}
        effect_handlers.load()
        effect_handlers.APPLY['explore'](D, s, 'explore', 1, 'self', 'gone', 'alice')
        self.assertFalse(s['counter'])
        self.assertEqual(s['_known_top']['alice'], ['top'])

if __name__ == '__main__': unittest.main()
