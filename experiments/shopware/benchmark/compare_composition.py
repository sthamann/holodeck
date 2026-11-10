"""Compare factorized decoders with the same-data retrieval and fixed-choice controls."""
from argparse import ArgumentParser
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path

from composition import Atoms, Composed, TextFeatures, encode, train
from composition_lessons import ViewLessons, quantity_program, reserved_cases
from dataflow import GraphAtoms, ScopedAtoms, include_native_transitions
from graph_ir import GRAPH, expand
from lessons import training
from models import Learned, Retrieval, Vocabulary, fit
from run import measure, ports_closed, read_rows


def summarize(result, known_programs, known_fields):
    novelty = Counter()
    for step in result['steps']:
        program = step['program']
        if program and GRAPH in program:
            program = {**{key: expr for key, expr in program.items() if key != GRAPH},
                       **expand(program[GRAPH])}
            novelty['dataflow executions'] += 1
        if step['correct'] and program and encode(program) not in known_programs:
            novelty[step['event']] += 1
        if step['correct'] and program and any(encode(expr) not in known_fields[field]
                                               for field, expr in program.items()):
            novelty['new expression for a destination'] += 1
    return dict(novelty)


def main():
    cli = ArgumentParser(description=__doc__)
    cli.add_argument('original', type=Path)
    cli.add_argument('new', type=Path)
    cli.add_argument('base', type=Path)
    cli.add_argument('output', type=Path)
    cli.add_argument('--teacher-port', type=int, action='append', required=True)
    cli.add_argument('--steps', type=int, default=650)
    cli.add_argument('--confirmation', type=Path)
    cli.add_argument('--weights-from', type=Path)
    cli.add_argument('--train-only', action='store_true')
    cli.add_argument('--chrome', type=Path)
    cli.add_argument('--dataflow-capture', type=Path)
    cli.add_argument('--dataflow-confirmation', type=Path)
    cli.add_argument('--coupled', action='store_true')
    args = cli.parse_args()
    if not args.train_only:
        ports_closed(args.teacher_port)
    args.output.mkdir(parents=True, exist_ok=False)
    original, reserved_original = read_rows(args.original)
    fresh, held_out = read_rows(args.new)
    if {row['channel'] for row in original + reserved_original} & {row['channel'] for row in fresh + held_out}:
        raise ValueError('Original and new channels must be disjoint')
    rows = original + fresh
    if args.confirmation:
        confirm = [json.loads(line) for line in args.confirmation.read_text().splitlines()]
        confirm_ids = {row['channel'] for row in confirm}
        previous_ids = {row['channel'] for row in rows + reserved_original + held_out}
        if len(confirm_ids) < 2 or confirm_ids & previous_ids or any(r['split'] != 'holdout' for r in confirm):
            raise ValueError('The confirmation population must consist of new reserved channels')
        held_out = confirm
    if args.dataflow_capture:
        new_train, new_holdout = read_rows(args.dataflow_capture)
        prior_ids = {row['channel'] for row in rows + reserved_original + held_out}
        if prior_ids & {row['channel'] for row in new_train + new_holdout}:
            raise ValueError('Dataflow channels must be new')
        rows += new_train
        held_out = new_holdout
    if args.dataflow_confirmation:
        confirmation = [json.loads(line) for line in args.dataflow_confirmation.read_text().splitlines()]
        channel_ids = {row['channel'] for row in confirmation}
        if len(channel_ids) < 2 or channel_ids & {r['channel'] for r in rows + held_out + reserved_original}:
            raise ValueError('Confirmation requires fresh independent channels')
        if any(row['split'] != 'holdout' for row in confirmation):
            raise ValueError('Confirmation includes training data')
        held_out = confirmation
    quantity, caps = quantity_program(rows)
    lessons, diagnostics = training(rows, quantity=quantity, ui_samples=ViewLessons())
    if diagnostics['label_errors']:
        raise ValueError(f"Invalid teaching programs: {diagnostics['label_errors'][:2]}")
    extra = include_native_transitions(lessons) if args.dataflow_capture else []
    lessons += extra
    unique = {(ctx, encode(plan)): plan for ctx, plan in lessons}
    samples = [(ctx, plan) for (ctx, _), plan in unique.items()]
    scopes = reserved_cases()
    known_programs = {encode(plan) for _, plan in samples}
    if any(encode({'holo.view': ['const', target]}) in known_programs for _, target in scopes.values()):
        raise ValueError('Reserved UI is present in supervision')
    known_fields = {}
    for _, program in samples:
        for field, expr in program.items():
            known_fields.setdefault(field, set()).add(encode(expr))
    features = TextFeatures(args.base, [ctx for ctx, _ in samples], [ctx for ctx, _ in samples])
    print(f'{len(rows)} train journeys, {len(held_out)} holdout journeys, {caps} capped training requests, '
          f'{len(samples)} unique training programs and input pairs', flush=True)
    cache = dict(features.cache)
    vocabulary, atoms = Vocabulary(samples), Atoms(samples)
    sources = [args.original, args.new] + ([args.dataflow_capture] if args.dataflow_capture else [])
    training_sources = {p.name: sha256(p.read_bytes()).hexdigest() for p in sources}
    weights = args.weights_from or args.output
    legacy = weights / 'fixed.safetensors'
    joint = weights / 'composed-joint.safetensors'
    experts = weights / 'composed-experts.safetensors'
    additional = {}
    if args.dataflow_capture:
        scoped = ScopedAtoms(samples)
        graph_atoms = GraphAtoms(samples)
        additional = {
            'B-scoped-tree': (scoped, weights / 'scoped-tree.safetensors', False),
            'B-shared-graph': (graph_atoms, weights / 'shared-graph.safetensors', False),
            'C-routed-graph': (graph_atoms, weights / 'routed-graph.safetensors', True),
        }
    if args.weights_from:
        prior = json.loads((weights / 'report.json').read_text())
        if prior['source_sha256'] != training_sources:
            raise ValueError('Checkpoint requires the same training sources')
    else:
        fit(samples, features, vocabulary, legacy, False, args.steps, seed=71)
        train(samples, features, atoms, joint, False, args.steps)
        if not additional:
            train(samples, features, atoms, experts, True, args.steps)
        for atom_set, file, routed in additional.values():
            train(samples, features, atom_set, file, routed, args.steps)
    arms = {
        'A-retrieval': Retrieval(samples, features),
        'B-fixed': Learned(features, vocabulary, legacy),
        'B-composed': Composed(features, atoms, joint),
    }
    weight_paths = [legacy, joint]
    if additional:
        for label, (atom_set, file, routed) in additional.items():
            arms[label] = Composed(features, atom_set, file, routed)
            weight_paths.append(file)
        atoms_for_ablation, path_for_ablation, _ = additional['C-routed-graph']
    else:
        arms['C-composed'] = Composed(features, atoms, experts, routed=True)
        weight_paths.append(experts)
        atoms_for_ablation, path_for_ablation = atoms, experts
    arms['C-swapped'] = Composed(features, atoms_for_ablation, path_for_ablation, routed=True, reroute=True)
    joint_metadata = None
    if args.coupled:
        from joint_effects import Coupled, JointEffects
        if 'B-scoped-tree' not in arms:
            raise ValueError('Coupled graph needs the dataflow population')
        coupled_path = args.output / 'joint-effects.safetensors'
        joint_effect = JointEffects(samples, coupled_path)
        joint_metadata = joint_effect.metadata
        weight_paths.append(coupled_path)
        arms['B-coupled'] = Coupled(arms['B-scoped-tree'], joint_effect)
        arms['B-coupled-rotated'] = Coupled(arms['B-scoped-tree'], joint_effect, rotate=True)
    report = {'base_revision': args.base.name, 'teacher_ports_closed': [] if args.train_only else args.teacher_port,
              'source_sha256': training_sources,
              'confirmation_sha256': sha256(args.confirmation.read_bytes()).hexdigest() if args.confirmation else None,
              'dataflow_confirmation_sha256': sha256(args.dataflow_confirmation.read_bytes()).hexdigest()
              if args.dataflow_confirmation else None,
              'weights_sha256': {p.name: sha256(p.read_bytes()).hexdigest() for p in weight_paths},
              'native_transition_supplements': len(extra),
              'numeric_regions': graph_atoms.regions[1:] if additional else [],
              'joint_effect_training': joint_metadata,
              'training_journeys': len(rows), 'heldout_journeys': len(held_out),
              'train_shops': sorted({row['shop'] for row in rows}),
              'held_out_shops': sorted({row['shop'] for row in held_out}),
              'training_pairs': len(samples), 'capped_training_requests': caps,
              'quantity_expression': quantity, 'training_steps': None if args.weights_from else args.steps,
              'reserved_ui': scopes, 'arms': {}}
    target = args.output / 'report.json'
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    if args.train_only:
        return
    for name, arm in arms.items():
        features.cache = dict(cache)
        ports_closed(args.teacher_port)
        result = measure(arm, name, held_out, args.output, ui_cases=scopes)
        result['novel_correct_programs'] = summarize(result, known_programs, known_fields)
        report['arms'][name] = result
        print(name, sum(j['correct'] for j in result['journeys']), '/', len(held_out),
              'steps', result['by_family'], 'UI', result['by_ui'],
              'novel', result['novel_correct_programs'], flush=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    if args.chrome:
        from browser_probe import verify
        choices = ('B-shared-graph', 'C-routed-graph') if additional else ('B-composed', 'C-composed')
        if args.coupled:
            choices = (*choices, 'B-coupled')
        def merit(arm):
            trial = report['arms'][arm]
            return sum(j['correct'] for j in trial['journeys']), sum(x[0] for x in trial['by_ui'].values())
        name = max(choices, key=merit)
        proof = verify(args.chrome, arms[name], name, held_out, report['arms'][name],
                       scopes, args.output / 'browser')
        print('Browser proof', name, proof, flush=True)
    ports_closed(args.teacher_port)


if __name__ == '__main__':
    main()
