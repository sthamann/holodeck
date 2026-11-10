"""Measure exact requested facets with the same labels and two feature sets."""
from argparse import ArgumentParser
import json
from pathlib import Path

from admin_projection import AdminProjection, examples
from composition import TextFeatures
from learn_policy import require_offline


# Keep this population out of the fitting data. Do not tune on these results.
CASES = (
    ('Welche Umsatzsteuer ist bei den einzelnen Sätzen angefallen?', ('order_taxes',)),
    ('Ich will den Lieferstand und den Gesamtwert jeder Bestellung sehen', ('delivery', 'gross')),
    ('Den Versand keinesfalls bestätigen. Zeig den Lieferstatus.', ('delivery',)),
    ('Ich brauche eine Suche nach Bestellreferenz. Mach die Zeilen sortierbar.', ('search', 'sort')),
    ('Ich möchte nur offene oder versandte Lieferungen auswählen können', ('delivery_filter',)),
    ('Zahlung als eingegangen verbuchen. Ich will den Bezahlstatus wieder öffnen.', ('pay', 'reopen')),
    ('Stell die Sendung auf verschickt', ('ship',)),
    ('Die Lieferung soll nur sichtbar sein. Versand darf man hier nicht auslösen.', ('delivery',)),
    ('Was hat der Kunde gekauft? Zeig die Steueranteile nach Prozent. Ich möchte die Ablaufmarkierung sehen.',
     ('details', 'order_taxes', 'workflow')),
    ('Ich will nur Auftragsnummern.', ()),
    ('Kann ich den Geldeingang hier erfassen?', ('pay',)),
    ('Zeig Zahlungsstatus und Versandstatus ohne Zahlungen anzustoßen', ('payment', 'delivery')),
    ('Bitte Filter für offene und bezahlte Rechnungen', ('payment_filter',)),
    ('Wie verteilen sich die Steuern auf die einzelnen Prozentsätze?', ('order_taxes',)),
)


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--weights', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--teacher-port', type=int, default=55697)
    args = parser.parse_args()
    require_offline(args.teacher_port)
    source = Path(__file__).resolve().parent.parent / 'workspace/ui.jsonl'
    training = examples(source)
    features = TextFeatures(args.base, (), [(('admin',), '')])
    model = AdminProjection(features, args.weights, source)
    rows = []
    for text, expected in CASES:
        if text.strip('.!?') in training:
            raise ValueError('Evaluation prompt duplicates a training example')
        learned = model.choose(text)
        text_only = model.choose(text, lexical_only=True)
        rows.append({'request': text, 'expected': sorted(expected), 'learned': sorted(learned),
                     'text_only': sorted(text_only), 'learned_exact': learned == set(expected),
                     'text_only_exact': text_only == set(expected)})
    report = {'teacher_port_closed': args.teacher_port, 'training_examples': len(training),
              'evaluation_cases': len(rows), 'learned_exact': sum(row['learned_exact'] for row in rows),
              'text_only_exact': sum(row['text_only_exact'] for row in rows), 'rows': rows}
    unsupported = 'Gib mir den Nettobetrag je Artikel'
    report['unsupported_probe'] = {'request': unsupported, 'learned': sorted(model.choose(unsupported)),
                                   'required_field': 'net_per_item'}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k not in ('rows', 'unsupported_probe')}))


if __name__ == '__main__':
    main()
