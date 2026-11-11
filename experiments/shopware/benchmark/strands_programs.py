"""Learn typed after-sales program selection from native Shopware outcomes."""
from argparse import ArgumentParser
from hashlib import sha256
import json
from pathlib import Path

from adaptation import ACTIONS, ENTITY_FIELDS, target
from collection_vm import literal
from learn_policy import json_lines
from program_deck import ProgramDeck
from strands_readout import CHECKPOINT, StrandsReadout


REJECT = 'reject'
NO_CHANGE = 'accept_without_write'


def field_options(specialist):
    grammar = json.loads((specialist / 'grammar.json').read_text())
    return {field: tuple(atom[1] for atom in grammar['write:order.' + field]
                         if atom and atom[0] == 'literal' and isinstance(atom[1], str))
            for field in sorted(set(ENTITY_FIELDS.values()))}


def make_question(engine, vocabulary, event, before):
    field = ENTITY_FIELDS[event.split('.')[0]]
    criteria = {
        REJECT: 'Reject the action. Keep the current state.',
        NO_CHANGE: 'Accept the action. Keep the current state.',
        **{value: f'Accept the action. Set {field} to {value}.' for value in vocabulary[field]},
    }
    return engine.request(state={'action': event, 'current ' + field: before}, questions={
        'program': {'type': 'choice', 'instructions': 'Choose the resulting state transition.',
                    'criteria': criteria},
    })


def examples(path):
    for row in json_lines(path):
        for step in row['events']:
            event = step['command']
            if event not in ACTIONS:
                continue
            field, before, cells = target(step)
            accepted = cells['write:result.accepted'][1]
            change = cells.get('write:order.' + field)
            yield event, before, change[1] if change else NO_CHANGE if accepted else REJECT


class StrandsPrograms:
    def __init__(self, readout, checkpoint, specialist):
        import copy
        import torch

        self.readout = readout
        self.vocabulary = field_options(specialist)
        self.head = copy.deepcopy(readout.head)
        self.head.load_state_dict(torch.load(checkpoint, map_location='cpu', weights_only=True))
        self.checksum = sha256(checkpoint.read_bytes()).hexdigest()
        self.cache = {}

    def choose(self, event, before):
        key = (event, before)
        if key not in self.cache:
            request = make_question(self.readout, self.vocabulary, event, before)
            self.cache[key] = self.readout.evaluate(request, head=self.head).answers['program'].choice
        return self.cache[key]

    def attach(self, deck):
        programs = self
        original = deck.predict

        class Select:
            tags = original.tags

            def __call__(self, context):
                tags, _ = context
                event = next(tag[6:] for tag in tags if tag.startswith('event='))
                if event not in ACTIONS:
                    return original(context)
                field = ENTITY_FIELDS[event.split('.')[0]]
                prefix = f'state.order.{field}='
                values = [tag.removeprefix(prefix) for tag in tags if tag.startswith(prefix)]
                # The specialist's recorded domain identifies supported input categories.
                if len(values) != 1 or prefix + values[0] not in original.routes[event]:
                    return [('write:result.accepted', literal(False))]
                selection = programs.choose(event, values[0])
                cells = [('write:result.accepted', literal(selection != REJECT))]
                if selection in programs.vocabulary[field]:
                    cells.append(('write:order.' + field, literal(selection)))
                return cells

        identity = sha256(f'{deck.checkpoint}:strands:{self.readout.identity}:{self.checksum}'.encode()).hexdigest()
        return ProgramDeck(Select(), identity)


def fit(readout, vocabulary, train, output):
    import torch

    rows = list(json_lines(train))
    if not rows or any(row['split'] != 'train' for row in rows):
        raise ValueError('Training requires native training traces')
    unique = {}
    for event, before, outcome in examples(train):
        if unique.setdefault((event, before), outcome) != outcome:
            raise ValueError('Conflicting targets for the same state and command')
    captures, labels = [], []

    def collect(_, inputs):
        captures.append(tuple(value.detach().clone() for value in inputs))

    hook = readout.head.register_forward_pre_hook(collect)
    try:
        for (event, before), outcome in unique.items():
            question = make_question(readout, vocabulary, event, before)
            labels.append(list(question.questions['program'].criteria).index(outcome))
            readout.evaluate(question)
    finally:
        hook.remove()

    queries, choices = (torch.cat(tensors).clone() for tensors in zip(*captures, strict=True))
    targets = torch.tensor(labels)
    torch.set_num_threads(4)
    optimizer = torch.optim.AdamW(readout.head.parameters(), lr=0.00025)
    readout.head.train()
    for _ in range(70):
        loss = torch.nn.functional.cross_entropy(readout.head(queries, choices), targets)
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
    readout.head.eval()
    torch.save(readout.head.state_dict(), output)
    return len(unique)


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', default=CHECKPOINT)
    parser.add_argument('--train', required=True, type=Path)
    parser.add_argument('--holdout', required=True, type=Path)
    parser.add_argument('--specialist', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    readout = StrandsReadout(args.checkpoint)
    vocabulary = field_options(args.specialist)
    cases = list(examples(args.holdout))
    baseline = [readout.evaluate(make_question(readout, vocabulary, event, before)).answers['program'].choice
                for event, before, _ in cases]
    count = fit(readout, vocabulary, args.train, args.output)
    selector = StrandsPrograms(readout, args.output, args.specialist)
    misses = [(event, before, target, actual) for event, before, target in cases
              if (actual := selector.choose(event, before)) != target]
    print({'training_signatures': count, 'heldout_commands': len(cases),
           'upstream_correct': sum(expected == actual for (_, _, expected), actual in zip(cases, baseline)),
           'fitted_correct': len(cases) - len(misses), 'misses': sorted(set(misses))})
