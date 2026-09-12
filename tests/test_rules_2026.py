"""Integration regressions for the August 2026 Comprehensive Rules.

Run: OMP_NUM_THREADS=1 MTG_NO_SPACY=1 python3 tests/test_rules_2026.py
"""
from pathlib import Path
import os
import sys
import unittest
import tempfile
import contextlib
import io
import re

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'packages')]
os.environ.setdefault('MTG_NO_SPACY', '1')

from interpreter.card_corpus import units_of
from interpreter.transpile_card import transpile_unit
from interpreter import build_ability_kinds, build_sba_extra
from mtg import driver as D, bridge_to_engine as bridge, rules_2026, _paths, sim
import effect_handlers


def state():
    return {'is_player': {('alice',), ('bob',)}, 'active_player': {('alice',)},
            'has_priority': {('alice',)}, 'current_step': {('precombat_main',)},
            'life': {('alice', 20), ('bob', 20)}, 'on_battlefield': set(),
            'printed_type': set(), 'printed_control': set(), 'printed_subtype': set(),
            'in_hand': set(), 'in_library': set(), 'counter': set(), 'tapped': set(),
            'spell_type': set(), 'printed_power': set(), 'printed_toughness': set()}


def creature(s, name, player='alice'):
    s['on_battlefield'].add((name,))
    s['printed_control'].add((player, name))
    s['printed_type'].add((name, 'creature'))
    s['printed_power'].add((name, 2))
    s['printed_toughness'].add((name, 3))


def parsed_card(text, mana='{2}{G}'):
    card = {'name': 'Probe', 'types': ['Creature'], 'text': text, 'manaCost': mana}
    f = {'abilities': {}}
    for i, unit in enumerate(units_of(card)):
        out = transpile_unit(unit, {'id': 'probe', 'card': card, 'seq': i})
        if out is None:
            raise AssertionError(f'Unparsed {unit.raw}')
        for fact in out.facts:
            rel = fact.split('(', 1)[0]
            args = sim._args(fact.split("(", 1)[1][:-1])
            if rel == 'card_ability':
                f['abilities'].setdefault(args[1], {}).update(kind=args[2], effects=[])
            elif rel == 'ability_cost':
                f['abilities'][args[1]]['cost'] = args[2]
            elif rel == 'ability_modifier':
                f['abilities'][args[1]].setdefault('modifiers', set()).add(args[2])
            elif rel == 'card_effect':
                f['abilities'][args[1]]['effects'].append((int(args[2]), *args[3:]))
            elif rel == 'static':
                f.setdefault('statics', []).append(args[1])
            elif rel == 'printed_keyword':
                f.setdefault('keywords', set()).add(args[1])
            elif rel == 'teamwork':
                f['teamwork'] = int(args[1])
            elif rel == 'keyword_param':
                f.setdefault('keyword_param', set()).add((args[1], args[2]))
    rows, dropped = bridge.card_facts('Probe', 'alice', 'probe', {'probe': f}, {'Probe': card})
    return rows, dropped


class Rules2026(unittest.TestCase):
    def test_hybrid_power_up_payment_and_entry_reduction(self):
        for colors, cost, printed in [(('green', 'blue'), '{4}{G/U}', '{1}{G/U}'),
                                      (('green', 'white'), '{6}{G/W}', '{2}{G/W}')]:
            for color in colors:
                for just_entered in (False, True):
                    with self.subTest(cost=cost, color=color, entered=just_entered):
                        rows, dropped = parsed_card('Power-up — ' + cost + ': Put a +1/+1 counter on ~.', printed)
                        self.assertFalse(dropped)
                        s = state(); creature(s, 'probe')
                        for key, values in rows.items():
                            s.setdefault(key, set()).update(values)
                        if just_entered:
                            rules_2026.entered(s, 'probe')
                        expected = (3 if cost == '{4}{G/U}' else 4) if just_entered else (5 if cost == '{4}{G/U}' else 7)
                        for n in range(expected):
                            land = f'land{n}'
                            s['on_battlefield'].add((land,))
                            s['printed_type'].add((land, 'land'))
                            s['printed_control'].add(('alice', land))
                            s.setdefault('land_produces', set()).add((land, color))
                        D._refresh_mana_pool(s, 'alice')
                        legal = D._activatable(s, 'alice')
                        self.assertEqual(len(legal), 1)
                        rules_2026.pay_activation(D, s, 'alice', legal[0])
                        self.assertEqual(len(s['tapped']), expected)
                        self.assertFalse(D._activatable(s, 'alice'))

    def test_hybrid_power_up_rejects_unmatched_mana(self):
        rows, _ = parsed_card('Power-up — {4}{G/U}: Put a +1/+1 counter on ~.', '{1}{G/U}')
        s = state(); creature(s, 'probe')
        for key, values in rows.items():
            s.setdefault(key, set()).update(values)
        D._set_floating(s, 'alice', {'red': 5})
        D._refresh_mana_pool(s, 'alice')
        self.assertFalse(D._activatable(s, 'alice'))

    def test_combat_draw_does_not_fire_phantom_death(self):
        s = state(); creature(s, 'a'); creature(s, 'b', 'bob')
        s.update(current_step={('combat_damage',)}, attacks={('a', 'bob')}, blocks={('b', 'a')},
                 has_trigger={('death', 'a', 'dies_self'), ('hit', 'a', 'combat_damage_to_creature')},
                 trigger_effect={('death', 'lose_life', 3, 'each_opponent'), ('hit', 'draw', 1, 'controller')},
                 in_library={('alice', 'card')}, _lib_order={'alice': ['card']})
        D._apply_outputs(s, D.run(s, D.OUTPUTS), 'alice')
        self.assertIn(('alice', 'card'), s['in_hand'])
        self.assertIn(('bob', 20), s['life'])
        self.assertEqual(s['on_battlefield'], {('a',), ('b',)})
        self.assertEqual(s['marked_damage'], {('a', 2), ('b', 2)})
        self.assertFalse(D.run(s, ['zone_change'])['zone_change'])
        # A later combat must still add new damage to what remains marked.
        s['current_step'] = {('end_of_combat',)}
        D._apply_outputs(s, D.run(s, D.OUTPUTS), 'alice')
        s['current_step'] = {('combat_damage',)}
        self.assertEqual(len(D.run(s, ['zone_change'])['zone_change']), 2)

    def _vibranium_x_state(self, artifact=False):
        s = state(); D._create_token(s, 'vibranium', 'alice', 3)
        s['on_battlefield'].add(('mountain',))
        s['printed_type'].add(('mountain', 'land'))
        s['printed_control'].add(('alice', 'mountain'))
        s['land_produces'] = {('mountain', 'red')}
        s['in_hand'] = {('alice', 'spell')}
        s['spell_type'] = {('spell', 'artifact' if artifact else 'sorcery')}
        s['mana_generic'] = {('spell', 0)}
        s['mana_pip'] = {('spell', 'red', 1)}
        s['x_count'] = {('spell', 1)}
        D._refresh_mana_pool(s, 'alice')
        return s

    def test_vibranium_x_uses_only_spendable_mana(self):
        for artifact in (False, True):
            s = self._vibranium_x_state(artifact)
            D._spend_mana(s, 'alice', 'spell')
            self.assertEqual(s['_spell_x']['spell'], 3 if artifact else 0)
            self.assertEqual(len(s['tapped']), 4 if artifact else 1)

    def test_x_payment_preserves_spend_as_any_color_permission(self):
        s = self._vibranium_x_state()
        s['land_produces'] = {('mountain', 'green')}
        s['_spend_any_color'] = {('alice',)}
        D._refresh_mana_pool(s, 'alice')
        D._spend_mana(s, 'alice', 'spell')
        self.assertEqual(s['_spell_x']['spell'], 0)
        self.assertEqual(s['tapped'], {('mountain',)})

    def test_vibranium_rejects_unpayable_chosen_x_before_payment(self):
        from unittest.mock import patch
        s = self._vibranium_x_state()
        with patch.object(D, '_choose', return_value=3):
            with self.assertRaisesRegex(ValueError, 'Cannot pay chosen X'):
                D._spend_mana(s, 'alice', 'spell')
        self.assertFalse(s['tapped'])
        self.assertNotIn('_spell_x', s)

    def test_current_data_selected(self):
        self.assertEqual(_paths.datalog_dir().resolve(), (ROOT / 'datalog').resolve())

    def test_extraction(self):
        self.assertIn(('605.1a', 'activated', 'no_library_movement'), build_ability_kinds.mana_ability_criteria())
        rows = build_sba_extra.extract()
        self.assertTrue(any(r[1:3] == ('siege_battle', 'defense_zero_without_pending_trigger') for r in rows))
        self.assertTrue(any(r[1:3] == ('non_siege_battle', 'defense_zero') for r in rows))

    def test_teamwork_survives_grounding(self):
        rows, _ = parsed_card('Teamwork 3')
        self.assertIn(('probe', 3), rows['teamwork_cost'])

    def test_power_up_cost_limit_and_blink(self):
        rows, _ = parsed_card('Power-up — {3}{G}: Put two +1/+1 counters on ~.')
        s = state(); creature(s, 'probe')
        for k, v in rows.items():
            s.setdefault(k, set()).update(v)
        s['mana_available'] = {('alice', 1)}
        self.assertFalse(D._activatable(s, 'alice'))
        rules_2026.entered(s, 'probe')
        row = D._activatable(s, 'alice')[0]
        self.assertEqual(rules_2026.power_up_cost(s, row[0]), (1, {}))
        rules_2026.pay_activation(D, s, 'alice', row)
        self.assertFalse(D._activatable(s, 'alice'))
        # New turn does not reset "activate only once".
        s['entered_this_turn'] = set(); s['mana_available'] = {('alice', 10)}
        self.assertFalse(D._activatable(s, 'alice'))
        # A new object after a zone change gets a fresh activation.
        rules_2026.entered(s, 'probe')
        self.assertTrue(D._activatable(s, 'alice'))

    def test_power_up_reduction_preserves_unmatched_colored_pips(self):
        rows, _ = parsed_card('Power-up — {1}{U}: Draw a card.', '{2}{G}')
        s = state(); creature(s, 'probe')
        for k, v in rows.items():
            s.setdefault(k, set()).update(v)
        rules_2026.entered(s, 'probe')
        aid = next(iter(s['ability_power_up']))[0]
        self.assertEqual(rules_2026.power_up_cost(s, aid), (0, {'blue': 1}))
        s['on_battlefield'].add(('forest',)); s['printed_type'].add(('forest', 'land'))
        s['printed_control'].add(('alice', 'forest')); s['land_produces'] = {('forest', 'green')}
        D._refresh_mana_pool(s, 'alice')
        self.assertFalse(D._activatable(s, 'alice'))

    def test_library_cost_is_stack_ability(self):
        rows, _ = parsed_card('{T}, Mill a card: Add {G}.')
        self.assertNotIn(('probe',), rows.get('mana_source', set()))
        self.assertTrue(rows['activated_ability'])
        self.assertEqual(rows['ability_mill_cost'], {('probe_a0', 1)})
        s = state(); creature(s, 'probe')
        for k, v in rows.items():
            s.setdefault(k, set()).update(v)
        self.assertFalse(D._activatable(s, 'alice'))
        s['in_library'] = {('alice', 'top')}; s['_lib_order'] = {'alice': ['top']}
        row = D._activatable(s, 'alice')[0]
        rules_2026.pay_activation(D, s, 'alice', row)
        self.assertIn(('top',), s['graveyard'])
        self.assertFalse(D._floating(s, 'alice'))  # effect has not resolved yet

    def test_library_effects_resolve_once_in_printed_order(self):
        from unittest.mock import patch
        rows, dropped = parsed_card('{T}: Draw a card. Add {G}.')
        self.assertFalse(dropped)
        s = state(); creature(s, 'probe')
        for k, v in rows.items():
            s.setdefault(k, set()).update(v)
        legal = D._activatable(s, 'alice')
        self.assertEqual(len(legal), 1)
        a, src, cost, taps, eff, n, tgt = legal[0]
        self.assertEqual(eff, 'draw')
        D._stack_push(s, a, 'alice')
        s['_ability_effect'] = {a: (eff, n, tgt, src, 'alice')}
        with patch.object(D, '_resolve_activation_effect') as apply:
            D._resolve_top(s)
        self.assertEqual([call.args[2] for call in apply.call_args_list], ['draw', 'add_mana'])

    def test_public_state_distinguishes_activation_and_damage(self):
        from mtg.engine.observe import observe
        s = state(); creature(s, 'probe')
        old = D._facts_key(s)
        s['power_up_used'] = {('probe_a0',)}
        s['entered_this_turn'] = {('probe',)}
        s['marked_damage'] = {('probe', 2)}
        self.assertNotEqual(D._facts_key(s), old)
        visible = observe(s, 'alice')
        for key in ('power_up_used', 'entered_this_turn', 'marked_damage'):
            self.assertEqual(visible[key], s[key])

    def test_regeneration_and_zone_entry_clear_damage(self):
        s = state(); creature(s, 'probe')
        s['marked_damage'] = {('probe', 2)}
        s['_regen_shield'] = {('probe',)}
        self.assertTrue(D._consume_regen_shield(s, 'probe'))
        self.assertFalse(s['marked_damage'])
        s['marked_damage'] = {('probe', 2)}
        rules_2026.entered(s, 'probe')
        self.assertFalse(s['marked_damage'])

    def test_combat_retains_damage_for_heal(self):
        s = state(); creature(s, 'a'); creature(s, 'b', 'bob')
        s['current_step'] = {('combat_damage',)}
        s['attacks'] = {('a', 'bob')}; s['blocks'] = {('b', 'a')}
        out = D.run(s, D.OUTPUTS)
        D._apply_outputs(s, out, 'alice')
        s['current_step'] = {('postcombat_main',)}
        self.assertEqual(dict(s['marked_damage']), {'a': 2, 'b': 2})
        effect_handlers.load()
        effect_handlers.APPLY['heal'](D, s, 'heal', 0, 'self', 'a', 'alice')
        self.assertEqual(dict(s['marked_damage']), {'b': 2})

    def test_heal_and_recruit_encode_as_activated_effects(self):
        for text, effect in [('{T}: Heal target creature.', 'heal'), ('{T}: Recruit.', 'recruit')]:
            rows, dropped = parsed_card(text)
            self.assertFalse(dropped)
            self.assertEqual(next(iter(rows['activated_ability']))[4], effect)

    def test_recruit_land_and_nonland(self):
        effect_handlers.load()
        for typ, tokens in [('land', 0), ('instant', 1)]:
            s = state(); s['in_library'] = {('alice', 'card')}; s['_lib_order'] = {'alice': ['card']}
            s['spell_type'] = {('card', typ)}
            effect_handlers.APPLY['recruit'](D, s, 'a', 1, 'controller', 'src', 'alice')
            self.assertEqual(len(s.get('is_token', set())), tokens)
            self.assertIn(('card',), s['graveyard'])
            if tokens:
                tok = next(iter(s['is_token']))[0]
                self.assertIn((tok, 'human'), s['printed_subtype'])
                self.assertIn((tok, 'soldier'), s['printed_subtype'])

    def test_heal_between_damage_events(self):
        effect_handlers.load()
        s = state(); creature(s, 'c', 'bob')
        D._apply_damage(s, 'first', 2, 'creature_fixed:c', 'alice')
        self.assertEqual(dict(s['marked_damage'])['c'], 2)
        effect_handlers.APPLY['heal'](D, s, 'heal', 0, 'self', 'c', 'bob')
        D._apply_damage(s, 'second', 2, 'creature_fixed:c', 'alice')
        self.assertIn(('c',), s['on_battlefield'])
        D._apply_damage(s, 'third', 1, 'creature_fixed:c', 'alice')
        self.assertNotIn(('c',), s['on_battlefield'])

    def test_hone_changes_power_only_while_equipped(self):
        s = state(); creature(s, 'c')
        s['on_battlefield'].add(('equipment',)); s['printed_type'].add(('equipment', 'artifact'))
        s['printed_subtype'].add(('equipment', 'equipment'))
        s['counter'] = {('equipment', 'hone', 2)}; s['attached_to'] = {('equipment', 'c')}
        self.assertIn(('c', '4'), D.run(s, ['power'])['power'])
        self.assertIn(('c', '3'), D.run(s, ['eff_toughness'])['eff_toughness'])
        s['attached_to'] = set()
        self.assertIn(('c', '2'), D.run(s, ['power'])['power'])

    def test_story_counts_distinct_objects_and_persists(self):
        s = state(); creature(s, 'story')
        rows, dropped = parsed_card('Storied')
        self.assertFalse(dropped)
        # Use the real card-keyword translation, not a hand-injected printed keyword.
        s['instance_of'] = {('story', 'probe')}
        s['card_keyword'] = rows['card_keyword']
        s['has_supertype'] = {('story', 'legendary')}
        s['printed_type'].add(('story', 'artifact')); s['printed_subtype'].add(('story', 'saga'))
        self.assertFalse(D.run(s, ['has_enduring_story'])['has_enduring_story'])
        for c in ['one', 'two']:
            s['on_battlefield'].add((c,)); s['printed_control'].add(('alice', c)); s['printed_type'].add((c, 'artifact'))
        D._pending_both(s)
        self.assertIn(('alice',), s['enduring_story'])
        s['on_battlefield'] = set()
        self.assertIn(('alice',), D.run(s, ['has_enduring_story'])['has_enduring_story'])

    def test_worthy_excludes_villains(self):
        s = state(); creature(s, 'hero'); s['has_supertype'] = {('hero', 'legendary')}
        s['printed_color'] = {('hero', 'red')}
        self.assertIn(('hero',), D.run(s, ['worthy'])['worthy'])
        s['printed_subtype'].add(('hero', 'villain'))
        self.assertFalse(D.run(s, ['worthy'])['worthy'])

    def test_face_down_transform_is_a_noop(self):
        import copy
        s = state(); creature(s, 'c')
        s['face_down'] = {('c',)}; s['transform_target'] = {('c', 'back')}
        old = copy.deepcopy(s)
        D._transform(s, 'c', 'alice')
        self.assertEqual(s, old)

    def test_connive_zero_and_departed_source(self):
        effect_handlers.load()
        s = state(); s['in_hand'] = {('alice', 'discard')}
        effect_handlers.APPLY['connive'](D, s, 'a', 0, 'self', 'gone', 'alice')
        self.assertEqual(s['in_hand'], {('alice', 'discard')})
        s['in_library'] = {('alice', 'draw')}; s['_lib_order'] = {'alice': ['draw']}
        effect_handlers.APPLY['connive'](D, s, 'a', 1, 'self', 'gone', 'alice')
        self.assertFalse(s['counter'])
        self.assertEqual(len(s['in_hand']), 1)

    def test_equip_worthy_requires_a_worthy_creature(self):
        card = {'name': 'Hammer', 'types': ['Artifact'], 'subtypes': ['Equipment'],
                'text': 'Equip worthy {1}'}
        rows, _ = bridge.card_facts('Hammer', 'alice', 'hammer', {}, {'Hammer': card})
        s = state(); creature(s, 'hero')
        s['on_battlefield'].add(('hammer',)); s['mana_available'] = {('alice', 1)}
        for k, v in rows.items():
            s.setdefault(k, set()).update(v)
        self.assertFalse(D._activatable(s, 'alice'))
        s['has_supertype'] = {('hero', 'legendary')}; s['printed_color'] = {('hero', 'white')}
        self.assertEqual(len(D._activatable(s, 'alice')), 1)
        D._equip(s, 'hammer', 'alice', 'worthy')
        self.assertIn(('hammer', 'hero'), s['attached_to'])

    def test_vibranium_cannot_pay_nonartifact_colorless_pip(self):
        s = state(); D._create_token(s, 'vibranium', 'alice', 1)
        s['on_battlefield'].add(('forest',)); s['printed_type'].add(('forest', 'land'))
        s['printed_control'].add(('alice', 'forest')); s['land_produces'] = {('forest', 'green')}
        s['in_hand'] = {('alice', 'spell')}; s['spell_type'] = {('spell', 'instant')}
        s['mana_cost'] = {('spell', 1)}; s['mana_generic'] = {('spell', 0)}
        s['mana_pip'] = {('spell', 'colorless', 1)}
        D._refresh_mana_pool(s, 'alice')
        self.assertNotIn(('alice', 'spell'), D.run(s, ['can_cast'])['can_cast'])

    def test_vibranium_restricted_mana(self):
        s = state(); D._create_token(s, 'vibranium', 'alice', 1)
        tok = next(iter(s['is_token']))[0]
        self.assertIn((tok, 'indestructible'), D.run(s, ['has_keyword'])['has_keyword'])
        for c, typ in [('artifact', 'artifact'), ('instant', 'instant')]:
            s['in_hand'].add(('alice', c)); s['spell_type'].add((c, typ))
        s['mana_cost'] = {('artifact', 1), ('instant', 1)}
        D._refresh_mana_pool(s, 'alice')
        can = D.run(s, ['can_cast'])['can_cast']
        self.assertIn(('alice', 'artifact'), can)
        self.assertNotIn(('alice', 'instant'), can)
        D._tap_all_for_mana(s, 'alice')
        self.assertNotIn((tok,), s['tapped'])
        D._spend_mana(s, 'alice', 'artifact')
        self.assertIn((tok,), s['tapped'])


if __name__ == '__main__':
    unittest.main()
