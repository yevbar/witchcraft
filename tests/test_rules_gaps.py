"""Regression coverage for the rules-update audit. Run directly with Python."""
import unittest
from test_rules_2026 import state, creature, parsed_card, D, rules_2026, effect_handlers

class RulesGaps(unittest.TestCase):
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
