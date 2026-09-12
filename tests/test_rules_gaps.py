"""Regression coverage for the rules-update audit. Run directly with Python."""
import unittest
from test_rules_2026 import state, creature, parsed_card, D, rules_2026, effect_handlers

class RulesGaps(unittest.TestCase):
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
