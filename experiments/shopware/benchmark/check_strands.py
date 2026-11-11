"""Compare admin readouts and replay the fitted Strands programs through the actual VM."""
from argparse import ArgumentParser
import json
from pathlib import Path
from statistics import median, quantiles
from tempfile import TemporaryDirectory
from time import perf_counter

from adaptation import restore
from admin_projection import AdminProjection
from check_adaptation import check
from check_admin_projection import CASES
from composition import TextFeatures
from learn_policy import json_lines, require_offline
from strands_programs import StrandsPrograms
from strands_readout import CHECKPOINT, StrandsReadout, training_rows


def evaluate(choose):
    rows = []
    timings = []
    choose('Show the order total.')
    for text, expected in CASES:
        start = perf_counter()
        observed = choose(text)
        timings.append((perf_counter() - start) * 1000)
        rows.append({'text': text, 'expected': sorted(expected), 'observed': sorted(observed),
                     'exact': observed == set(expected)})
    return {'exact': sum(row['exact'] for row in rows), 'total': len(rows),
            'median_ms': round(median(timings), 1), 'p95_ms': round(quantiles(timings, n=20, method='inclusive')[18], 1),
            'rows': rows}


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True, type=Path)
    parser.add_argument('--evidence', required=True, type=Path)
    parser.add_argument('--model', default=CHECKPOINT)
    parser.add_argument('--view-head', required=True, type=Path)
    parser.add_argument('--program-head', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--teacher-port', type=int, default=55867)
    args = parser.parse_args()
    require_offline(args.teacher_port)
    if any(text in training_rows() for text, _ in CASES):
        raise ValueError('An evaluation request is in the training rows')
    readout = StrandsReadout(args.model)
    original = evaluate(lambda text: readout.choose(text, include_service=False))
    import torch
    readout.head.load_state_dict(torch.load(args.view_head, map_location='cpu', weights_only=True))
    fitted = evaluate(lambda text: readout.choose(text, include_service=False))
    previous = AdminProjection(TextFeatures(args.base, (), [(('admin',), '')]),
                               args.evidence / 'language/admin.npz', None)
    existing = evaluate(previous.choose)
    selector = StrandsPrograms(readout, args.program_head, args.evidence / 'specialist')
    model = restore(args.evidence / 'checkpoint', args.evidence / 'specialist')
    rows = json_lines(args.evidence / 'traces/service-holdout.jsonl')
    with TemporaryDirectory() as directory:
        prior = check('sparse', model, rows, Path(directory))
        trial = check('strands', selector.attach(model), rows, Path(directory))
    require_offline(args.teacher_port)
    report = {'readouts': {'existing': existing, 'upstream_strands': original, 'fitted_strands': fitted},
              'program_replay': {'sparse': prior, 'strands': trial}}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({group: {name: {k: v for k, v in row.items() if k not in ('details', 'rows')}
                              for name, row in data.items()} for group, data in report.items()}))


if __name__ == '__main__':
    main()
