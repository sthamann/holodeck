"""The application adapter: low-rank weights that make the backbone write intent programs for one Deck.

The prompt is the screen a person sees and the request. The rules of the application are not in the
prompt. They reach the weights through supervision that the Deck generates, and they stay enforced
by the Deck, which compiles and checks every program the model writes.
"""

from pathlib import Path
import sys

import torch
from torch import nn
import torch.nn.functional as F

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "deck-modality"))

from fusion import load_backbone  # noqa: E402,F401

from intent import render_actions, screen  # noqa: E402

MAX_NEW = 64


class Adapter(nn.Module):
    def __init__(self, tokenizer, lm, deck, actions=False, prefix=""):
        super().__init__()
        self.tokenizer, self.lm, self.deck = tokenizer, lm, deck
        self.actions, self.prefix = actions, prefix  # control arms: raw actions, instructions in the prompt
        self.backward = False  # the explaining expert: screen and program in, request out
        self.pad = tokenizer.pad_token_id

    def ids(self, text):
        return self.tokenizer.encode(text, add_special_tokens=False)

    def prompt(self, task):
        view = screen(self.deck, task["state"], self.actions)
        if self.backward:
            return self.ids(f"{view}\nProgram: {task['program']}\nRequest:")
        return self.ids(f"{self.prefix}{view}\nRequest: {task['request']}\nProgram:")

    def target(self, task):
        if self.backward:
            return task["request"]
        return render_actions(task["actions"]) if self.actions else task["program"]

    def batch(self, rows, left):
        device = next(self.lm.parameters()).device
        longest = max(map(len, rows))
        ids = torch.full((len(rows), longest), self.pad, dtype=torch.long)
        mask = torch.zeros(len(rows), longest, dtype=torch.long)
        for index, row in enumerate(rows):
            span = slice(longest - len(row), longest) if left else slice(0, len(row))
            ids[index, span] = torch.tensor(row)
            mask[index, span] = 1
        return ids.to(device), mask.to(device)

    def forward(self, tasks):
        rows, marks = [], []
        for task in tasks:
            prompt, program = self.prompt(task), self.ids(" " + self.target(task) + "\n")
            rows.append(prompt + program)
            marks.append((len(prompt), len(program)))
        ids, mask = self.batch(rows, left=False)
        logits = self.lm(input_ids=ids, attention_mask=mask).logits
        loss = sum(F.cross_entropy(logits[row, start - 1:start + size - 1], ids[row, start:start + size])
                   for row, (start, size) in enumerate(marks))
        return loss / len(tasks)

    @torch.no_grad()
    def write(self, tasks):
        """Greedy programs for a batch, with the number of prompt tokens."""
        prompts = [self.prompt(task) for task in tasks]
        ids, mask = self.batch(prompts, left=True)
        output = self.lm(input_ids=ids, attention_mask=mask, use_cache=True)
        written, open_rows = [[] for _ in tasks], set(range(len(tasks)))
        for _ in range(MAX_NEW):
            token = output.logits[:, -1].argmax(-1)
            for row in list(open_rows):
                written[row].append(int(token[row]))
                if "\n" in self.tokenizer.decode(written[row]):
                    open_rows.discard(row)
            if not open_rows:
                break
            mask = torch.cat([mask, torch.ones(len(tasks), 1, dtype=mask.dtype, device=mask.device)], 1)
            output = self.lm(input_ids=token[:, None], attention_mask=mask,
                             past_key_values=output.past_key_values, use_cache=True)
        return [self.tokenizer.decode(tokens).split("\n")[0].strip() for tokens in written], \
            sum(map(len, prompts)) / len(tasks)

    @torch.no_grad()
    def generate(self, tasks, sample=False):
        """Programs with their exact log probability under this model: greedy, or one sample each at temperature 1."""
        prompts = [self.prompt(task) for task in tasks]
        ids, mask = self.batch(prompts, left=True)
        output = self.lm(input_ids=ids, attention_mask=mask, use_cache=True)
        written, score = [[] for _ in tasks], [0.0] * len(tasks)
        open_rows = set(range(len(tasks)))
        for _ in range(MAX_NEW):
            logp = torch.log_softmax(output.logits[:, -1].float(), -1)
            token = torch.multinomial(logp.exp(), 1)[:, 0] if sample else logp.argmax(-1)
            picked = logp.gather(1, token[:, None])[:, 0].cpu()
            for row in list(open_rows):
                written[row].append(int(token[row]))
                score[row] += float(picked[row])
                if "\n" in self.tokenizer.decode(written[row]):
                    open_rows.discard(row)
            if not open_rows:
                break
            mask = torch.cat([mask, torch.ones(len(tasks), 1, dtype=mask.dtype, device=mask.device)], 1)
            output = self.lm(input_ids=token[:, None], attention_mask=mask,
                             past_key_values=output.past_key_values, use_cache=True)
        return [(self.tokenizer.decode(tokens).split("\n")[0].strip(), value)
                for tokens, value in zip(written, score)]

    @torch.no_grad()
    def loglik(self, tasks):
        """Log probability this model gives to each task's target after its prompt."""
        rows, marks = [], []
        for task in tasks:
            prompt, target = self.prompt(task), self.ids(" " + self.target(task) + "\n")
            rows.append(prompt + target)
            marks.append((len(prompt), len(target)))
        ids, mask = self.batch(rows, left=False)
        logp = torch.log_softmax(self.lm(input_ids=ids, attention_mask=mask).logits.float(), -1)
        return [float(logp[row, start - 1:start + size - 1].gather(1, ids[row, start:start + size, None]).sum())
                for row, (start, size) in enumerate(marks)]
