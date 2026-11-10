"""Train the small language readout for requested after-sales operations."""
from argparse import ArgumentParser
from pathlib import Path

from admin_projection import AdminProjection
from composition import TextFeatures


EXTRA = {
    'returns': ('Retouren bearbeiten', 'Rücksendungen im Händlerbereich erfassen',
                'Teilretoure und vollständige Rückgabe bearbeiten', 'Ware als zurückgesendet verbuchen',
                'Ich möchte die Rücksendung abwickeln', 'Retourenwerkstatt öffnen',
                'Aktionen für die zurückgegebene Lieferung', 'Process a return',
                'Retouren und Erstattungen bearbeiten'),
    'refunds': ('Erstattungen bearbeiten', 'Rückzahlungen als Händler verwalten',
                'Teilerstattung und vollständige Erstattung erfassen', 'Geld zurückerstatten',
                'Ich möchte den Rückzahlungsstatus ändern', 'Erstattungsaktionen anbieten',
                'Zahlung ganz oder teilweise erstatten', 'Process a refund',
                'Retouren und Erstattungen bearbeiten'),
}


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('base', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    features = TextFeatures(args.base, (), [(('admin',), '')])
    source = Path(__file__).resolve().parents[1] / 'workspace/ui.jsonl'
    AdminProjection(features, args.output, source, fit=True, extra=EXTRA)
