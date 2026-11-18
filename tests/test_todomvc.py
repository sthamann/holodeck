"""Exact properties of the TodoMVC Deck and its intent programs. No model is loaded."""
import json
import math
from pathlib import Path
import random
import sys
import unittest

HERE = Path(__file__).resolve().parents[1] / 'experiments' / 'todomvc'
sys.path.insert(0, str(HERE))

from compose import decide, key, posterior  # noqa: E402
from deck import Deck, trim  # noqa: E402
from describe import describe, enumerate_programs  # noqa: E402
from intent import compile_program, outcome, parse  # noqa: E402
import tasks  # noqa: E402


class TodoMVC(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.deck = Deck()
        cls.cohort = tasks.build(3, 300, 'test')

    def state(self, *rows, **scalars):
        todos = [{'id': number, 'title': title, 'completed': done} for number, (title, done) in enumerate(rows, 1)]
        return {'filter': 'all', 'all_box': False, 'todos': todos, 'next': len(rows) + 1, **scalars}

    def test_blank_titles_are_refused_and_an_empty_edit_deletes(self):
        state = self.deck.initial()
        self.assertIsNone(self.deck.step(state, 'add', {'text': ' \t  '}))
        state = self.deck.step(state, 'add', {'text': '  buy milk '})
        self.assertEqual(state['todos'][0]['title'], 'buy milk')
        self.assertEqual(self.deck.step(state, 'edit', {'row': 0, 'text': '  '})['todos'], [])
        self.assertEqual(trim('﻿ a  '), 'a')

    def test_rows_are_positions_in_the_visible_list(self):
        state = self.state(('a', True), ('b', False), ('c', True), filter='completed')
        self.assertEqual([row['title'] for row in self.deck.view(state)], ['a', 'c'])
        self.assertEqual([row['title'] for row in self.deck.step(state, 'destroy', {'row': 1})['todos']], ['a', 'b'])
        self.assertIsNone(self.deck.step(state, 'toggle', {'row': 2}))

    def test_program_syntax(self):
        self.assertEqual(parse('complete #3 #12; delete completed; add "x y"'),
                         [('complete', [3, 12]), ('delete completed',), ('add', 'x y')])
        self.assertEqual(parse('unclear'), [])
        for text in ('', 'complete 3', 'destroy #3', 'add x', 'complete #3;delete #4', 'show done'):
            self.assertIsNone(parse(text), text)

    def test_complete_all_absorbs_the_flip_flop_of_the_original(self):
        for box in (False, True):
            state = self.state(('a', True), ('b', False), all_box=box)
            actions, after = compile_program(self.deck, state, parse('complete all'))
            self.assertTrue(all(row['completed'] for row in after['todos']))
            self.assertEqual(len(actions), 2 if box else 1)

    def test_reference_programs_reach_the_recorded_outcome_and_never_apply_twice(self):
        for task in self.cohort:
            statements = parse(task['program'])
            actions, after = compile_program(self.deck, task['state'], statements)
            self.assertEqual(outcome(after), task['outcome'])
            self.assertEqual(actions, task['actions'])
            if len(statements) == 1 and statements[0][0] not in ('add', 'delete'):
                again = compile_program(self.deck, after, statements)
                # A second run changes nothing. It is refused when the row has left the visible list.
                self.assertTrue(again is None or again[0] == [])

    def test_a_program_about_a_hidden_or_missing_row_is_refused(self):
        state = self.state(('a', True), ('b', False), filter='active')
        self.assertIsNone(compile_program(self.deck, state, parse('delete #1')))
        self.assertIsNone(compile_program(self.deck, state, parse('rename #9 "x"')))
        self.assertIsNone(compile_program(self.deck, state, parse('add " "')))

    def test_relabelling_rows_keeps_the_outcome(self):
        rng = random.Random(4)
        for task in self.cohort[:100]:
            other = tasks.relabel(rng, task)
            mapping = {old['id']: new['id'] for old, new in zip(task['state']['todos'], other['state']['todos'])}
            program = ' '.join(f'#{mapping[int(word[1:])]}' if word.lstrip('#').isdigit() and word.startswith('#')
                               else word for word in task['program'].replace(';', ' ;').split(' ')).replace(' ;', ';')
            self.assertEqual(outcome(compile_program(self.deck, other['state'], parse(program))[1]), task['outcome'])

    def test_scored_tasks_share_no_title_or_wording_with_training(self):
        titles = lambda split: {title for group in tasks.TITLES[split].values() for title in group}
        wording = lambda split: {text for group in tasks.WORDING[split].values() for text in group}
        self.assertFalse(titles('train') & titles('test'))
        self.assertFalse(wording('train') & wording('test'))

    def test_recorded_evidence_names_the_pinned_original(self):
        evidence = json.loads((HERE / 'evidence.json').read_text())
        self.assertEqual(len(evidence['deck_equals_original_todomvc']['commit']), 40)

    def test_programs_with_the_same_outcome_pool_their_mass(self):
        state = self.state(('a', False), ('b', False))
        view = {'state': state}
        self.assertEqual(key(self.deck, state, 'complete #1 #2'), key(self.deck, state, 'complete all'))
        self.assertIsNone(key(self.deck, state, 'complete #9'))
        self.assertIsNone(key(self.deck, state, 'toggle 1'))
        # Two weaker programs with one outcome outweigh the single most likely program.
        candidates = [[['delete #1', math.log(0.4), None], ['complete #1 #2', math.log(0.3), None],
                       ['complete all', math.log(0.3), None], ['complete #9', math.log(0.9), None]]]
        scores = posterior(self.deck, [view], candidates)
        self.assertEqual(max(scores, key=scores.get), key(self.deck, state, 'complete all'))
        self.assertAlmostEqual(sum(scores.values()), 1.0)
        self.assertEqual(posterior(self.deck, [view], [[['complete #9', 0.0, None]]]), {})

    def test_every_enumerated_statement_compiles_and_is_described(self):
        for task in self.cohort[:40]:
            for program in enumerate_programs(self.deck, task['state'], task['request']):
                self.assertIsNotNone(key(self.deck, task['state'], program), program)
                self.assertTrue(describe(self.deck, task['state'], program))


if __name__ == '__main__':
    unittest.main()
