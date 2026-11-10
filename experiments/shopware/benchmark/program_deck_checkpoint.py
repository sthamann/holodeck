"""Export a serving checkpoint that contains no exemplar programs or outcomes."""
from argparse import ArgumentParser
import json
from pathlib import Path
from shutil import copyfile

from learn_policy import fingerprint
from models import Features
from program_deck import Grammar, Learned, ProgramDeck


def export(source, target):
    info = json.loads((source / 'fit.json').read_text())
    samples = json.loads((source / 'retrieval.json').read_text())
    tags = sorted({tag for (context, _), _ in samples for tag in context})
    target.mkdir(parents=True, exist_ok=False)
    for name in ('grammar.json', 'shared.safetensors', 'routed.safetensors'):
        copyfile(source / name, target / name)
    manifest = {'base_revision': info['base_revision'], 'input_tags': tags,
                'files': {name.name: fingerprint(name) for name in sorted(target.iterdir())}}
    (target / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')


class Checkpoint:
    def __init__(self, directory, base):
        manifest = json.loads((directory / 'manifest.json').read_text())
        if manifest['base_revision'] != base.name or any(
                fingerprint(directory / name) != expected for name, expected in manifest['files'].items()):
            raise ValueError('Deck checkpoint fingerprint mismatch')
        self.directory = directory
        self.grammar = Grammar(json.loads((directory / 'grammar.json').read_text()))
        self.features = Features(base, (), [(tuple(manifest['input_tags']), '')])

    def deck(self, variant):
        if variant not in ('shared', 'routed'):
            raise ValueError('Unknown learned architecture')
        weights = self.directory / f'{variant}.safetensors'
        predict = Learned(self.features, self.grammar, weights, routed=variant == 'routed')
        return ProgramDeck(predict, fingerprint(weights))


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('frozen', type=Path)
    parser.add_argument('runtime', type=Path)
    args = parser.parse_args()
    export(args.frozen, args.runtime)
