"""Team source bounds refine OR without inventing hits or clearing real buffs."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from src.data.battle_highlight import BattleHighlightState
from src.data.highlight_conditions import HighlightAtom, HighlightExpression, infer_highlight, parse_highlight
from src.data.highlight_producers import SNAPSHOT, TeamProducerClosure, read_producers
from src.data.skill_timing import load_skill_timings


def term(identity, *, target=''):
    return HighlightExpression('atom', atom=HighlightAtom('tag', identity, target))


def either(*children):
    return HighlightExpression('or', children)


def both(*children):
    return HighlightExpression('and', children)


class TestHighlightInference(unittest.TestCase):
    def test_ternary_or_only_confirms_last_possible_arm(self):
        expression = either(term('a'), term('b'), term('c'))
        inference = infer_highlight(expression, True, possible=lambda a: False if a.identity in ('a', 'b') else None)
        self.assertTrue(inference.confirms('tag', 'c'))
        self.assertFalse(inference.confirms('tag', 'a'))
        self.assertEqual(inference.alternatives, 1)

    def test_nested_or_confirms_common_terms_but_not_an_arbitrary_branch(self):
        expression = either(both(term('a'), term('shared')), both(term('b'), term('shared')),
                            both(term('c'), term('extra')))
        inference = infer_highlight(expression, True, possible=lambda a: False if a.identity == 'c' else None)
        self.assertTrue(inference.confirms('tag', 'shared'))
        self.assertFalse(inference.confirms('tag', 'a'))
        self.assertFalse(inference.confirms('tag', 'b'))
        self.assertFalse(inference.confirms('tag', 'extra'))

    def test_native_multi_tag_or_and_nested_sequences_are_parsed(self):
        def query(tags, mode=0):
            return {'$type': 'Beyond.Gameplay.CheckTagMatch+Data, Gameplay',
                    '$value': {'query': {'queryType': mode, 'tags': [{'raw': tag} for tag in tags]},
                               'checkTarget': {'targetSource': 2, 'targetGroupKey': 'highlight_smart_target'}}}
        condition = {'actionData': [{'$type': 'Beyond.Gameplay.OrConditionAction+Data', '$value': {
            'conditionList': [{'actionData': [query(['01000000', '02000000', '03000000'])]},
                              {'actionData': [query(['04000000']), query(['05000000'])]}]}}]}
        expression = parse_highlight(json.dumps(condition))
        result = infer_highlight(expression, True, possible=lambda atom: False if atom.identity in
                                 ('01000000', '02000000', '04000000') else None)
        self.assertTrue(result.confirms('tag', '03000000'))
        self.assertEqual(result.alternatives, 1)
        condition['actionData'] = [query(['01000000', '02000000'], 1)]
        result = infer_highlight(parse_highlight(json.dumps(condition)), True)
        self.assertTrue(result.confirms('tag', '01000000'))
        self.assertTrue(result.confirms('tag', '02000000'))

    def test_possible_producer_is_not_a_current_positive_fact(self):
        result = infer_highlight(either(term('a'), term('b')), True, possible=lambda _: True)
        self.assertFalse(result.facts)
        self.assertEqual(result.alternatives, 2)
        for ready in (False, None, 1, 'true'):
            with self.subTest(ready=ready):
                self.assertFalse(infer_highlight(term('a'), ready, possible=lambda _: True).facts)

    def test_observed_conflict_drops_narrowing_without_changing_ready(self):
        state = BattleHighlightState(load_skill_timings(), ['陈千语', '余烬', '黎风', '管理员'])
        observation = state.observe('1', state.store.profiles('狼卫', 'battle'), True, 0)
        self.assertTrue(observation.ready)
        self.assertTrue(observation.model_inference.conflicted)
        self.assertFalse(observation.model_inference.facts)
        self.assertFalse(observation.inference.confirms('tag', '9648d5bd'))

    def test_native_wolf_or_refines_for_fire_team_and_stays_open_for_two_elements(self):
        store = load_skill_timings()
        profiles = store.profiles('狼卫', 'battle')
        fire = BattleHighlightState(store, ['狼卫', '卡缪', '余烬', '管理员'])
        observation = fire.observe('1', profiles, True, 0)
        result = observation.model_inference
        self.assertTrue(result.confirms('tag', '9648d5bd'))
        self.assertFalse(result.confirms('tag', 'bf9d6e57'))
        # External/prior enemy sources are not independently bounded by roster
        # recognition. Keep this refinement separate from actual UI facts.
        self.assertFalse(observation.inference.confirms('tag', '9648d5bd'))
        mixed = BattleHighlightState(store, ['狼卫', '佩丽卡', '余烬', '骏卫'])
        result = mixed.observe('1', profiles, True, 0).model_inference
        self.assertFalse(result.confirms('tag', '9648d5bd'))
        self.assertFalse(result.confirms('tag', 'bf9d6e57'))

    def test_unknown_roster_and_missing_child_prevent_negative_proof(self):
        store = load_skill_timings()
        for team in (['狼卫', '?'], ['狼卫', '洛茜'], ['狼卫', '艾维文娜'], ['狼卫', '莱万汀']):
            with self.subTest(team=team):
                state = BattleHighlightState(store, team)
                self.assertFalse(state.closure.closed)
                result = state.observe('1', store.profiles('狼卫', 'battle'), True, 0).model_inference
                self.assertFalse(result.confirms('tag', '9648d5bd'))

    def test_forced_burning_and_cross_reaction_are_distinct_producers(self):
        fire = TeamProducerClosure.for_team(['狼卫'])
        self.assertIn('9648d5bd', fire.tags)  # Native ultimate's forced Burning.
        self.assertNotIn('bf9d6e57', fire.tags)
        cold = TeamProducerClosure.for_team(['赛希'])
        self.assertNotIn('55af885b', cold.tags)  # Cold attachment alone is not Frozen.
        cross = TeamProducerClosure.for_team(['狼卫', '赛希'])
        self.assertIn('55af885b', cross.tags)

    def test_owner_scope_is_not_pruned_by_enemy_producer_closure(self):
        closure = TeamProducerClosure.for_team(['狼卫'])
        atom = HighlightAtom('tag', 'bf9d6e57', json.dumps({'targetSource': 1}))
        self.assertIsNone(closure.possible(atom))
        state = BattleHighlightState(load_skill_timings(), ['狼卫', '陈千语'])
        observation = state.observe('2', state.store.profiles('庄方宜', 'battle'), True, 0)
        self.assertTrue(observation.model_inference.confirms('buff', 'buff_chr_0030_zhuangfy_ult_skill_free', target_source=1))
        self.assertFalse(observation.inference.confirms('buff', 'buff_chr_0030_zhuangfy_ult_skill_free', target_source=1))
        self.assertFalse(observation.inference.confirms('buff', 'buff_chr_0030_zhuangfy_have_sword'))

    def test_latest_unknown_discards_inferred_counts_without_exact_stack_guess(self):
        state = BattleHighlightState(load_skill_timings(), ['莱万汀'])
        profiles = state.store.profiles('莱万汀', 'battle')
        result = state.observe('1', profiles, True, 0).inference
        self.assertTrue(result.confirms('buff', 'buff_chr_0016_laevat_energy', minimum=4, target_source=4))
        self.assertFalse(result.confirms('buff', 'buff_chr_0016_laevat_energy', minimum=5))
        self.assertFalse(state.observe('1', profiles, None, 1).inference.facts)
        self.assertFalse(state.observe('1', profiles, False, 2).inference.facts)

    def test_duplicate_and_contradictory_literals_have_logical_meaning(self):
        negated = HighlightExpression('not', (term('a'),))
        expression = either(both(term('a'), negated), both(term('b'), term('b')))
        result = infer_highlight(expression, True)
        self.assertTrue(result.confirms('tag', 'b'))
        self.assertFalse(result.confirms('tag', 'a'))

    def test_complexity_limit_preserves_unknown(self):
        expression = both(*(either(term(str(i)), term(str(i + 100))) for i in range(9)))
        result = infer_highlight(expression, True)
        self.assertTrue(result.limited)
        self.assertFalse(result.facts)

    def test_changed_snapshot_fails_provenance_check(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'index.json').write_bytes((SNAPSHOT / 'index.json').read_bytes())
            (path / 'snapshot.json').write_bytes((SNAPSHOT / 'snapshot.json').read_bytes() + b' ')
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                read_producers(path)

    def test_nonfinite_or_nonpositive_damage_binding_is_rejected(self):
        for key, value in (('extra_parameter', float('inf')), ('base_projectiles', 0), ('potential', -1)):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as directory:
                path = Path(directory)
                data = json.loads((SNAPSHOT / 'snapshot.json').read_text(encoding='utf8'))
                data['damage_bindings']['wulfgard'][key] = value
                raw = json.dumps(data).encode('utf8')
                manifest = json.loads((SNAPSHOT / 'index.json').read_text(encoding='utf8'))
                manifest['snapshot_sha256'] = hashlib.sha256(raw).hexdigest()
                (path / 'snapshot.json').write_bytes(raw)
                (path / 'index.json').write_text(json.dumps(manifest), encoding='utf8')
                with self.assertRaisesRegex(ValueError, 'Invalid highlight damage binding'):
                    read_producers(path)

    def test_source_evidence_and_exporter_hash_are_current(self):
        manifest = json.loads((SNAPSHOT / 'index.json').read_text(encoding='utf8'))
        exporter = Path(__file__).resolve().parents[1] / 'scripts/skill-data/export_highlight_producers.py'
        self.assertEqual(hashlib.sha256(exporter.read_bytes()).hexdigest(), manifest['exporter_sha256'])
        for name, key in (('reviewed_highlight_damage.py', 'damage_reviewer_sha256'),
                          ('reviewed_condition_transitions.py', 'condition_reviewer_sha256')):
            self.assertEqual(hashlib.sha256((exporter.parent / name).read_bytes()).hexdigest(), manifest[key])
        data = read_producers()
        self.assertEqual(len(data['actors']), 32)
        self.assertTrue(all(row['evidence'] or not row['tags'] for row in data['actors'].values()))


if __name__ == '__main__':
    unittest.main()
