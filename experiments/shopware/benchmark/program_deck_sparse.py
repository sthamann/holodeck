"""Learn sparse gates for cell presence and independent value selection."""
from argparse import ArgumentParser
from collections import defaultdict
from itertools import combinations
import json
from pathlib import Path
from shutil import copyfile

import numpy as np

from learn_policy import fingerprint, require_offline
from program_deck import Grammar, ProgramDeck


def coverage(contexts, limit=3):
    tags = sorted({tag for ctx in contexts for tag in ctx})
    rows = [set(ctx) for ctx in contexts]
    columns = [sum(1 << i for i, row in enumerate(rows) if tag in row) for tag in tags]
    universe = (1 << len(rows)) - 1
    candidates = {universe: ()}
    # Constant inputs are irrelevant for separating the observed contexts.
    varying = [i for i, column in enumerate(columns) if column != universe]
    for length in range(1, limit + 1):
        for clause in combinations(varying, length):
            support = universe
            for feature in clause:
                support &= columns[feature]
            if support:
                candidates.setdefault(support, clause)
    return tags, candidates, universe


def cover(positive, negative, candidates):
    available = [(bits & positive, clause) for bits, clause in candidates.items()
                 if bits & positive and not bits & negative]
    remaining, result = positive, []
    while remaining:
        ranked = [(clause, (bits & remaining).bit_count(), bits) for bits, clause in available
                  if bits & remaining]
        if not ranked:
            raise ValueError('Observed cell cannot be explained by three input facts')
        clause, _, bits = min(ranked, key=lambda row: ((1 + len(row[0])) / row[1], len(row[0]), row[0]))
        remaining &= ~bits
        result.append(clause)
    return result


def decision_list(active, by_value, candidates):
    remaining = active
    result = []
    while remaining:
        options = []
        for support, clause in candidates.items():
            matched = support & remaining
            if not matched:
                continue
            for value, bits in by_value.items():
                if not matched & ~bits:
                    options.append((clause, value, matched))
        if not options:
            raise ValueError('No sparse decision list fits the observed cell')
        clause, value, matched = min(options, key=lambda item: (
            (1 + len(item[0])) / item[2].bit_count(), len(item[0]), item[0], item[1]))
        result.append((value, clause))
        remaining &= ~matched
    return result


class Sparse:
    def __init__(self, grammar, manifest, path):
        self.grammar = grammar
        self.tags = manifest['tags']
        self.ordered = manifest.get('ordered', False)
        with np.load(path, allow_pickle=False) as archive:
            self.weights = archive['weights'].astype(np.int32)
            self.cells = archive['cells']
            self.labels = archive['labels']
            if self.weights.shape != (len(self.cells), len(self.tags)) or self.labels.shape != self.cells.shape:
                raise ValueError('Invalid sparse checkpoint tensor dimensions')

    def __call__(self, context):
        values = set(context[0])
        missing = np.array([tag not in values for tag in self.tags], dtype=np.int32)
        enabled = (self.weights @ missing) == 0
        choices = defaultdict(list)
        for cell, label in zip(self.cells[enabled], self.labels[enabled], strict=True):
            choices[int(cell)].append(int(label))
        tokens = []
        for index in range(len(self.grammar.cells)):
            labels = choices[index]
            active = 0 in labels
            values = [label for label in labels if label]
            if active and (not values or (not self.ordered and len(set(values)) != 1)):
                raise ValueError(f'No unique learned choice for active cell {self.grammar.cells[index]}')
            tokens.append(values[0] if active else 0)
        return self.grammar.decode(tokens)


def fit(source, target, *, ordered=False):
    target.mkdir(parents=True, exist_ok=False)
    path = target / 'grammar.json'
    copyfile(source / 'grammar.json', path)
    grammar = Grammar(json.loads(path.read_text()))
    samples = json.loads((source / 'retrieval.json').read_text())
    labels = np.array([grammar.targets(cells) for _, cells in samples])
    tags, candidates, universe = coverage([context[0] for context, _ in samples])
    gates = []
    for index, column in enumerate(labels.T):
        active = sum(1 << i for i, value in enumerate(column) if value)
        for clause in cover(active, universe ^ active, candidates):
            gates.append((index, 0, clause))
        # Learn values inside the support of the cell. This prevents a
        # correlated condition from another cell selecting this cell's value.
        by_value = {value: sum(1 << i for i, label in enumerate(column) if label == value)
                    for value in sorted(set(column) - {0})}
        if ordered:
            gates.extend((index, value, clause) for value, clause in decision_list(active, by_value, candidates))
        else:
            for value, positive in by_value.items():
                gates.extend((index, value, clause) for clause in cover(positive, active ^ positive, candidates))
    weights = np.zeros((len(gates), len(tags)), dtype=np.uint8)
    for index, (_, _, clause) in enumerate(gates):
        weights[index, list(clause)] = 1
    checkpoint = target / 'sparse.npz'
    np.savez_compressed(checkpoint, weights=weights, cells=[cell for cell, _, _ in gates],
                        labels=[label for _, label, _ in gates])
    manifest = {'tags': tags, 'ordered': ordered, 'grammar_sha256': fingerprint(path), 'weights_sha256': fingerprint(checkpoint),
                'training_sha256': fingerprint(source / 'retrieval.json'), 'learned_gates': len(gates),
                'learned_edges': int(weights.sum()), 'signatures': len(samples)}
    model = Sparse(grammar, manifest, checkpoint)
    if any(dict(model((tuple(ctx[0]), ctx[1]))) != expected for ctx, expected in samples):
        raise ValueError('Sparse decoder does not reconstruct its training supervision')
    (target / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print({k: v for k, v in manifest.items() if k != 'tags'}, flush=True)


def load(directory, grammar_path=None):
    grammar_path = grammar_path or directory / 'grammar.json'
    manifest = json.loads((directory / 'manifest.json').read_text())
    checkpoint = directory / 'sparse.npz'
    if manifest['grammar_sha256'] != fingerprint(grammar_path) or manifest['weights_sha256'] != fingerprint(checkpoint):
        raise ValueError('Sparse decoder checkpoint fingerprint mismatch')
    identity = manifest['weights_sha256']
    if 'numeric_vm_sha256' in manifest:
        from numeric_vm import install
        numeric_source = fingerprint(Path(__file__).with_name('numeric_vm.py'))
        if numeric_source != manifest['numeric_vm_sha256']:
            raise ValueError('Numeric opcode fingerprint mismatch')
        install()
        identity += '-' + fingerprint(grammar_path) + '-' + numeric_source
    grammar = Grammar(json.loads(grammar_path.read_text()))
    return ProgramDeck(Sparse(grammar, manifest, checkpoint), identity)


def shipping_weight_control(directory, grammar_path):
    deck = load(directory, grammar_path)
    model = deck.predict
    cell = model.grammar.cells.index('shipping_bands')
    feature = model.tags.index('config.shipping_tax=fixed')
    rows = np.flatnonzero((model.cells == cell) & (model.labels > 0) & (model.weights[:, feature] == 1))
    if len(rows) != 1:
        raise ValueError('Expected one learned fixed shipping gate')
    row = int(rows[0])
    alternatives = model.labels[(model.cells == cell) & (model.labels > 0) & (model.weights[:, feature] == 0)]
    original = int(model.labels[row])
    replacement = int(alternatives[0])
    if replacement == original:
        raise ValueError('Shipping control does not change the selected alternative')
    model.labels[row] = replacement
    deck.checkpoint += '-shipping-output-intervention'
    return deck, {'cell': 'shipping_bands', 'changed_output_index': [original, replacement],
                  'unchanged_grammar': fingerprint(grammar_path)}


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--decision-list', action='store_true')
    parser.add_argument('--teacher-port', type=int, required=True)
    args = parser.parse_args()
    require_offline(args.teacher_port)
    fit(args.source, args.output, ordered=args.decision_list)
