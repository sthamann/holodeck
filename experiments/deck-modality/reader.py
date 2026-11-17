"""Phase 2 arm: the backbone reads the application as text and writes three things in one stream.

    Goal: reels >= 3            the request restated over the contract's own field names
    Answer: F                   the option that starts a shortest plan
    Screen: card primary F ...  the Holo, decoded under the scene grammar

Nothing is added to the model except low-rank adapters. The exact Deck stays outside the weights:
it supplies the previews, the legal-action mask, the scene grammar and the check of the answer
against the goal the model stated.
"""

import torch
from torch import nn
import torch.nn.functional as F

import holo
from fusion import set_nll
from worlds import MAX_CANDIDATES, MAX_FIELDS, describe, goal_text, letter, parse_goal, verdict

WORDS = [" end", " card", " row", " close", " primary", " notice"]  # same order as holo.STRUCT
GOAL_TOKENS = 20


class ReaderArm(nn.Module):
    def __init__(self, tokenizer, lm, previews=True):
        super().__init__()
        self.tokenizer, self.lm, self.previews = tokenizer, lm, previews
        single = lambda text: self.ids(text) if len(self.ids(text)) == 1 else None
        self.letters = [single(" " + letter(i))[0] for i in range(MAX_CANDIDATES)] + [single(" Z")[0]]
        self.struct = [single(word)[0] for word in WORDS]
        self.facts = [single(" " + "abcdefgh"[i])[0] for i in range(MAX_FIELDS)]
        self.answer, self.screen = self.ids("Answer:"), self.ids("\nScreen:")
        self.pad = tokenizer.pad_token_id

    def ids(self, text):
        return self.tokenizer.encode(text, add_special_tokens=False)

    def prompt(self, world, task):
        text = describe(world, task, self.previews).rsplit("\n", 1)[0]
        return self.ids(text + "\nState the goal, the option that starts the shortest way to it, and the screen.\nGoal:")

    def scene_ids(self, scene):
        return [self.struct[value] if kind == "s" else self.facts[value] if kind == "f" else self.letters[value]
                for kind, value in scene]

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

    def forward(self, batch, **_):
        """Teacher-forced training on the whole stream."""
        rows, marks = [], []
        for world, task in batch:
            prompt = self.prompt(world, task)
            goal = self.ids(" " + goal_text(world, task["goal"]) + "\n")
            best = task["optimal"][0] if task["optimal"] else MAX_CANDIDATES
            scene = self.scene_ids(holo.reference(world, task))
            rows.append(prompt + goal + self.answer + [self.letters[best]] + self.screen + scene)
            marks.append((len(prompt), len(goal), len(scene)))
        ids, mask = self.batch(rows, left=False)
        logits = self.lm(input_ids=ids, attention_mask=mask).logits
        loss = 0
        for row, ((world, task), (start, goal, scene)) in enumerate(zip(batch, marks)):
            count = len(world.candidates)
            at = start + goal + len(self.answer)
            loss = loss + F.cross_entropy(logits[row, start - 1:start + goal - 1], ids[row, start:start + goal])
            loss = loss + set_nll(logits[row, at - 1, self.letters[:count] + self.letters[-1:]],
                                  task["optimal"] or [count])
            begin = at + 1 + len(self.screen)
            loss = loss + F.cross_entropy(logits[row, begin - 1:begin + scene - 1], ids[row, begin:begin + scene])
        return {"loss": loss / len(batch), "tokens": float(mask.sum()) / len(batch)}

    def step(self, ids, mask, past=None):
        output = self.lm(input_ids=ids, attention_mask=mask, past_key_values=past, use_cache=True)
        return output.logits[:, -1], output.past_key_values

    def extend(self, mask, width=1):
        return torch.cat([mask, torch.ones(len(mask), width, dtype=mask.dtype, device=mask.device)], 1)

    @torch.no_grad()
    def run(self, batch, scenes=False, constrained=True, oracle_goal=False):
        """Generate the goal, score the options, and optionally stream the scene under the grammar."""
        prompts = [self.prompt(world, task) for world, task in batch]
        size = len(batch)
        if oracle_goal:
            texts = [goal_text(world, task["goal"]) for world, task in batch]
        else:
            ids, mask = self.batch(prompts, left=True)
            logits, past = self.step(ids, mask)
            written, open_rows = [[] for _ in batch], set(range(size))
            for _ in range(GOAL_TOKENS):
                token = logits.argmax(-1)
                for row in list(open_rows):
                    written[row].append(int(token[row]))
                    if "\n" in self.tokenizer.decode(written[row]):
                        open_rows.discard(row)
                if not open_rows:
                    break
                mask = self.extend(mask)
                logits, past = self.step(token[:, None], mask, past)
            texts = [self.tokenizer.decode(tokens).split("\n")[0].strip() for tokens in written]
        goals = [parse_goal(world, text) for (world, _), text in zip(batch, texts)]

        stated = [prompt + self.ids(" " + text + "\n") + self.answer for prompt, text in zip(prompts, texts)]
        ids, mask = self.batch(stated, left=True)
        logits, past = self.step(ids, mask)
        answers, choices = [], []
        for row, (world, task) in enumerate(batch):
            count = len(world.candidates)
            scores = logits[row, self.letters[:count] + self.letters[-1:]].float().cpu()
            answers.append(scores)
            masked = scores.clone()
            masked[:count][~torch.tensor(task["legal"])] = float("-inf")
            choices.append(int(masked.argmax()))
        result = {"goals": goals, "goal_texts": texts, "answers": answers, "choices": choices,
                  "agree": [verdict(world, task, goal, choice)
                            for (world, task), goal, choice in zip(batch, goals, choices)],
                  "tokens": sum(map(len, stated)) / size}
        if not scenes:
            return result

        device = ids.device
        said = [[self.letters[choice if choice < len(world.candidates) else MAX_CANDIDATES]] + self.screen
                for (world, _), choice in zip(batch, choices)]
        mask = self.extend(mask, len(said[0]))
        logits, past = self.step(torch.tensor(said, device=device), mask, past)
        streams, open_rows = [[] for _ in batch], set(range(size))
        while open_rows:
            feed = []
            for row, (world, task) in enumerate(batch):
                if row not in open_rows:
                    feed.append(self.struct[0])
                    continue
                fields, count = len(world.names), len(world.candidates)
                tokens = [("s", index) for index in range(len(WORDS))] + [("f", index) for index in range(fields)] + \
                    [("c", index) for index in range(count)]
                if constrained:
                    legal = [index for index, ok in enumerate(task["legal"]) if ok]
                    tokens = holo.allowed(streams[row], range(fields), legal)
                options = self.scene_ids(tokens)
                token = tokens[int(logits[row, options].argmax())]
                streams[row].append(tuple(token))
                feed.append(self.scene_ids([token])[0])
                if tuple(token) == holo.END or len(streams[row]) >= holo.MAX_TOKENS:
                    open_rows.discard(row)
            if open_rows:
                mask = self.extend(mask)
                logits, past = self.step(torch.tensor(feed, device=device)[:, None], mask, past)
        result["scenes"] = streams
        return result

    def decode(self, batch, constrained=True):
        result = self.run(batch, scenes=True, constrained=constrained)
        return result["scenes"], result
