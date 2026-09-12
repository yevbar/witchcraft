"""Regression coverage for the rules-update audit. Run directly with Python."""
import unittest
from unittest.mock import patch
from test_rules_2026 import state, creature, parsed_card, D, rules_2026, effect_handlers

class RulesGaps(unittest.TestCase):
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
