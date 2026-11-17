"""Exact properties of the Deck modality experiment. No model is loaded."""
import json
from pathlib import Path
import random
import sys
from tempfile import TemporaryDirectory
import unittest

HERE = Path(__file__).resolve().parents[1] / 'experiments' / 'deck-modality'
sys.path.insert(0, str(HERE))

import holo  # noqa: E402
from neural_ops import Desk  # noqa: E402
from worlds import authored, build, goal_text, parse_goal, verdict  # noqa: E402


class DeckModality(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cohort = build(3, 12, 'test')

    def test_generator_is_deterministic(self):
        again = build(3, 12, 'test')
        self.assertEqual([(w.spec, t) for w, t in self.cohort], [(w.spec, t) for w, t in again])

    def test_training_and_test_vocabulary_are_disjoint(self):
        train = build(3, 12, 'train')
        names = lambda cohort: {name for world, _ in cohort for name in (*world.names, *world.spec['actions'])}
        self.assertFalse(names(train) & names(self.cohort))

    def test_labels_start_a_shortest_plan(self):
        for world, tasks in self.cohort:
            for task in tasks:
                distance = world.distance(task['actor'], task['goal'])
                here = distance[world.index[tuple(task['state'])]]
                self.assertEqual(here or 0, task['depth'])
                for candidate in task['optimal']:
                    after = world.step(tuple(task['state']), candidate, task['actor'])
                    self.assertEqual(distance[world.index[after]], here - 1)

    def test_no_decoder_choice_binds_a_foreign_control(self):
        rng = random.Random(5)
        for world, tasks in self.cohort + authored():
            for task in tasks:
                legal = {index for index, ok in enumerate(task['legal']) if ok}
                for _ in range(20):
                    scene = []
                    while not scene or scene[-1] != holo.END:
                        scene.append(rng.choice(holo.allowed(scene, range(len(world.names)), legal)))
                    self.assertLessEqual(len(scene), holo.MAX_TOKENS)
                    shown = holo.controls(scene)
                    self.assertTrue(set(shown) <= legal)
                    self.assertEqual(len(shown), len(set(shown)))

    def test_grammar_rejects_an_unauthorized_control(self):
        world, tasks = self.cohort[0]
        task = tasks[0]
        illegal = next(index for index, ok in enumerate(task['legal']) if not ok)
        legal = [index for index, ok in enumerate(task['legal']) if ok]
        scene = [holo.CARD, ('c', illegal), holo.CLOSE, holo.END]
        self.assertFalse(holo.wellformed(scene, range(len(world.names)), legal))

    def test_judgement_is_a_fact_that_replay_never_recomputes(self):
        with TemporaryDirectory() as root:
            path = Path(root) / 'desk.jsonl'
            desk = Desk(path, judge=lambda text, name: [1], model_digest='a')
            receipt = desk.resolve('refund me now', 'k1')
            self.assertEqual(receipt['effects'], [['page', 1], ['refund', 1]])
            offline = Desk(path)
            self.assertEqual(offline.state, desk.state)
            self.assertEqual(offline.resolve('refund me now', 'k1'), receipt)
            changed = Desk(path, judge=lambda text, name: [0], model_digest='b')
            self.assertEqual(changed.resolve('refund me now', 'k2')['effects'], [['page', 1], ['refund', 1]])
            self.assertEqual(changed.calls, 0)
            with self.assertRaises(LookupError):
                Desk(path).resolve('a message nobody judged', 'k3')

    def test_ambiguous_judgement_withholds_the_commit(self):
        with TemporaryDirectory() as root:
            desk = Desk(Path(root) / 'desk.jsonl', judge=lambda text, name: [0, 1] if name == 'urgent' else [0])
            self.assertEqual(desk.resolve('maybe soon', 'k'), {'status': 'withheld', 'ask': ['urgent']})
            self.assertEqual(desk.state['fields']['open'], 3)

    def test_stated_goal_round_trips_and_foreign_names_are_rejected(self):
        for world, tasks in self.cohort:
            for task in tasks:
                goal = [tuple(atom) for atom in task['goal']]
                self.assertEqual(parse_goal(world, goal_text(world, goal)), goal)
            self.assertIsNone(parse_goal(world, 'nonexistent >= 1'))
            self.assertIsNone(parse_goal(world, f'{world.names[0]} is 1'))

    def test_answer_is_confirmed_only_when_exact_search_agrees(self):
        for world, tasks in self.cohort:
            refusal = len(world.candidates)
            for task in tasks:
                goal = [tuple(atom) for atom in task['goal']]
                for choice in range(refusal + 1):
                    expected = choice in task['optimal'] if task['optimal'] else choice == refusal
                    self.assertEqual(verdict(world, task, goal, choice), expected)
                self.assertFalse(verdict(world, task, None, refusal))

    def test_recorded_evidence_is_valid_json(self):
        evidence = json.loads((HERE / 'evidence.json').read_text())
        self.assertEqual(evidence['scene_grammar']['unauthorized_or_repeated_controls'], 0)


if __name__ == '__main__':
    unittest.main()
