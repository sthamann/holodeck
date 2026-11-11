"""Fit and serve a Strands pointer head over the deck's typed projection vocabulary."""
from argparse import ArgumentParser
from hashlib import sha256
from pathlib import Path
from threading import Lock

from admin_projection import EXAMPLES, COMBINED, FACETS
from service_intent import EXTRA


CHECKPOINT = 'amazon/strands-decider-2B-qwen3.5-v1-2610'
ANCHOR = 'omit'
OPTIONS = dict(zip(FACETS, (
    'Show order total', 'Show payment status', 'Show delivery status',
    'Show order status', 'Sort the order table', 'Search order numbers',
    'Filter by payment status', 'Filter by delivery status', 'Show line items',
    'Show workflow marker', 'Show tax breakdown', 'Offer marking payment paid',
    'Offer reopening the payment', 'Offer marking delivery shipped',
), strict=True))
OPTIONS.update(returns='Offer recording a return', refunds='Offer recording a refund')
DESCRIPTION = 'Which controls or information does the merchant explicitly request?'


def training_rows():
    rows = {text: {label} for label, phrases in EXAMPLES.items() for text in phrases}
    rows.update({text: set(labels) for text, labels in COMBINED.items()})
    for label, phrases in EXTRA.items():
        for text in phrases:
            rows.setdefault(text, set()).add(label)
    rows.update({
        'Show payment and delivery status. Show line items.': {'payment', 'delivery', 'details'},
        'Show delivery and let me mark the shipment as sent.': {'delivery', 'ship'},
        'Record the payment as received. Let me reopen it.': {'pay', 'reopen'},
        'Show order items, delivery status, and return controls.': {'details', 'delivery', 'returns'},
        'Show the payment status and offer refund actions.': {'payment', 'refunds'},
        'Show payment and delivery. Offer return and refund actions.': {'payment', 'delivery', 'returns', 'refunds'},
        'Show delivery, but do not offer shipment or returns.': {'delivery'},
        'Show payment and delivery only. No actions.': {'payment', 'delivery'},
        'Show line items and let me record a return.': {'details', 'returns'},
        'Show line items with the order total.': {'details', 'gross'},
        'Leave payments and shipment unchanged.': set(),
    })
    return rows


class StrandsReadout:
    def __init__(self, checkpoint=CHECKPOINT, head=None):
        import torch
        from strands_decider.mlx_engine import load_mlx_engine
        from strands_decider.modeling import checkpoint_dir, config_path
        from strands_decider.schema import SystemOneRequest

        path = Path(checkpoint_dir(str(checkpoint)))
        manifest = path / 'MANIFEST.sha256'
        identity = sha256()
        for file in (Path(config_path(str(path))), path / 'head.safetensors',
                     path / 'lora/adapter_model.safetensors', path / 'tokenizer.json', manifest):
            if file.is_file():
                identity.update(sha256(file.read_bytes()).digest())
        self.identity = identity.hexdigest()
        self.engine = load_mlx_engine(str(path))
        self.request = SystemOneRequest
        self.head = self.engine.model.head
        self.lock = Lock()
        if head:
            self.head.load_state_dict(torch.load(head, map_location='cpu', weights_only=True))

    def score(self, intent, *, include_service=True):
        criteria = {key: description for key, description in OPTIONS.items()
                    if include_service or key not in EXTRA}
        criteria[ANCHOR] = 'Omit the capability if it was not requested'
        request = self.request(state={'merchant request': intent}, questions={'view': {
            'type': 'choice', 'instructions': DESCRIPTION, 'criteria': criteria,
        }})
        return self.evaluate(request).answers['view'].probabilities

    def evaluate(self, request, *, head=None):
        with self.lock:
            self.engine.model.head = head if head is not None else self.head
            try:
                return self.engine.evaluate(request)
            finally:
                self.engine.model.head = self.head

    def choose(self, intent, *, include_service=True):
        scores = self.score(intent, include_service=include_service)
        return {key for key, value in scores.items() if key != ANCHOR and value > scores[ANCHOR]}


def fit(readout, output):
    import torch
    from torch.nn.functional import binary_cross_entropy_with_logits

    rows = training_rows()
    captures = []

    def collect(_, inputs):
        captures.append(tuple(value.detach().clone() for value in inputs))

    hook = readout.head.register_forward_pre_hook(collect)
    try:
        for text in rows:
            readout.score(text)
    finally:
        hook.remove()

    questions, choices = (torch.cat(tensors).clone() for tensors in zip(*captures, strict=True))
    targets = torch.tensor([[float(option in labels) for option in OPTIONS] for labels in rows.values()])
    torch.set_num_threads(4)
    torch.manual_seed(11)
    optimizer = torch.optim.AdamW(readout.head.parameters(), lr=0.00018)
    readout.head.train()
    for epoch in range(40):
        for batch in torch.randperm(len(rows)).split(16):
            scores = readout.head(questions[batch], choices[batch])
            logits = scores[:, :-1] - scores[:, -1:]
            loss = binary_cross_entropy_with_logits(logits, targets[batch], pos_weight=torch.tensor(3.0))
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
    readout.head.eval()
    output.parent.mkdir(parents=True, exist_ok=True)
    torch.save(readout.head.state_dict(), output)
    print(f'Fitted {len(rows)} annotated requests; saved {output}', flush=True)


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', default=CHECKPOINT)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    fit(StrandsReadout(args.checkpoint), args.output)
