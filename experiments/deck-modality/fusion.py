"""Deck-native backbone: Deck tokens, exact executor blocks inside the forward, masked heads, scene decoder."""

import math
import random

import torch
from torch import nn
import torch.nn.functional as F
from transformers import AutoModelForCausalLM, AutoTokenizer

import holo
from worlds import COMPARATORS, MAX_CANDIDATES, MAX_FIELDS, describe, letter

TYPES = ["pad", "guard", "write", "effect", "not", "add", "sub", "le", "eq", "and", "or",
         "const", "field", "arg", "role", "policy", "revision"]
KIND = {name: index for index, name in enumerate(TYPES)}
BLOCKS = (12, 16, 20, 24)  # backbone layers after which an executor block runs
NS = len(holo.STRUCT)
WIDTH = 256


def serialize(world, action, actor):
    """Prefix form of one action's guard, writes and effects as (type, field slot, value) triples."""
    rule = world.spec["actions"][action]
    slots = {name: index + 1 for index, name in enumerate(world.names)}
    constants = {"role": world.spec["actors"][actor], "policy": world.spec["revisions"]["1"], "revision": 1}
    out = []

    def emit(expression):
        if type(expression) is int:
            out.append((KIND["const"], 0, max(0, min(9, expression))))
        elif isinstance(expression, str):
            if expression in slots:
                out.append((KIND["field"], slots[expression], 0))
            elif expression in constants:
                out.append((KIND[expression], 0, constants[expression]))
            else:
                out.append((KIND["arg"], 0, 0))
        else:
            out.append((KIND[expression[0]], 0, 0))
            for operand in expression[1:]:
                emit(operand)

    out.append((KIND["guard"], 0, 0))
    emit(rule["guard"])
    for name, expression in rule["writes"].items():
        out.append((KIND["write"], slots[name], 0))
        emit(expression)
    for _, expression in rule["effects"]:
        out.append((KIND["effect"], 0, 0))
        emit(expression)
    return out[:120]


class LoRA(nn.Module):
    def __init__(self, base, rank=16, alpha=32):
        super().__init__()
        self.base, self.scale = base, alpha / rank
        self.down = nn.Parameter(torch.randn(rank, base.in_features) * 0.01)
        self.up = nn.Parameter(torch.zeros(base.out_features, rank))

    def forward(self, x):
        return self.base(x) + (x @ self.down.T @ self.up.T) * self.scale


def load_backbone(path, device):
    tokenizer = AutoTokenizer.from_pretrained(path)
    lm = AutoModelForCausalLM.from_pretrained(path, dtype=torch.float32).to(device)
    for parameter in lm.parameters():
        parameter.requires_grad_(False)
    for layer in lm.model.layers:
        layer.self_attn.q_proj = LoRA(layer.self_attn.q_proj).to(device)
        layer.self_attn.v_proj = LoRA(layer.self_attn.v_proj).to(device)
    return tokenizer, lm


def set_nll(logits, members):
    """Negative log of the probability mass on any correct member."""
    mask = torch.full_like(logits, float("-inf"))
    mask[members] = 0
    return torch.logsumexp(logits, -1) - torch.logsumexp(logits + mask, -1)


class TextArm(nn.Module):
    """Matched language baseline: the contract as text, the answer as a letter."""

    def __init__(self, tokenizer, lm, previews):
        super().__init__()
        self.tokenizer, self.lm, self.previews = tokenizer, lm, previews
        self.letters = [tokenizer.encode(" " + letter(i), add_special_tokens=False) for i in range(MAX_CANDIDATES)]
        self.letters.append(tokenizer.encode(" Z", add_special_tokens=False))
        assert all(len(ids) == 1 for ids in self.letters), "answer letters must be single tokens"
        self.letters = [ids[0] for ids in self.letters]

    def forward(self, batch, **_):
        device = next(self.lm.parameters()).device
        texts = [describe(world, task, self.previews) for world, task in batch]
        self.tokenizer.padding_side = "left"
        encoded = self.tokenizer(texts, return_tensors="pt", padding=True).to(device)
        logits = self.lm(**encoded).logits[:, -1]
        loss, answers = 0, []
        for row, (world, task) in enumerate(batch):
            count = len(world.candidates)
            scores = logits[row, self.letters[:count] + self.letters[-1:]]
            loss = loss + set_nll(scores, task["optimal"] or [count])
            answers.append(scores.detach().cpu())
        return {"loss": loss / len(batch), "answers": answers,
                "tokens": int(encoded["attention_mask"].sum()) / len(batch)}


class DeckArm(nn.Module):
    def __init__(self, tokenizer, lm, executor):
        super().__init__()
        self.tokenizer, self.lm, self.executor = tokenizer, lm, executor
        size, d = lm.config.hidden_size, WIDTH
        self.slot = nn.Embedding(MAX_FIELDS + 1, d)
        self.value = nn.Embedding(10, d)
        self.kind = nn.Embedding(len(TYPES), d)
        self.position = nn.Embedding(128, d)
        layer = nn.TransformerEncoderLayer(d, 4, 4 * d, dropout=0.0, batch_first=True, norm_first=True)
        self.expression = nn.TransformerEncoder(layer, 2, enable_nested_tensor=False)
        self.field_out = nn.Linear(3 * d, size)
        self.action_out = nn.Linear(d, size)
        self.candidate_index = nn.Embedding(MAX_CANDIDATES, d)
        self.candidate_out = nn.Linear(2 * d, size)
        self.special = nn.Parameter(torch.randn(2, size) * 0.02)
        # typed goal read from the request
        self.goal_count = nn.Linear(size, 2)
        self.goal_query = nn.Linear(size, 2 * d)
        self.goal_key = nn.Linear(size, d)
        self.goal_comparator = nn.Linear(size, 2 * len(COMPARATORS))
        self.goal_value = nn.Linear(size, 2 * 10)
        # executor write-back and continuation choice
        self.tape_value = nn.Embedding(11, 32)
        self.tape = nn.Sequential(nn.Linear(MAX_FIELDS * 32 + 2 + len(BLOCKS), 512), nn.GELU(), nn.Linear(512, size))
        nn.init.zeros_(self.tape[-1].weight)
        nn.init.zeros_(self.tape[-1].bias)
        self.choose_query = nn.ModuleList(nn.Linear(size, d) for _ in BLOCKS[1:])
        self.choose_key = nn.ModuleList(nn.Linear(size, d) for _ in BLOCKS[1:])
        self.choose_stop = nn.ModuleList(nn.Linear(size, 1) for _ in BLOCKS[1:])
        # answer and scene heads
        self.answer_query, self.answer_key, self.refuse = nn.Linear(size, d), nn.Linear(size, d), nn.Linear(size, 1)
        self.scene_in = nn.Embedding(NS + 2, size)
        self.scene_struct = nn.Linear(size, NS)
        self.scene_query = nn.Linear(size, 2 * d)
        self.scene_fact, self.scene_control = nn.Linear(size, d), nn.Linear(size, d)
        self.names = {}
        # Small heads run on the CPU. Only the padded sequence and the write-backs cross to the backbone device.
        self.table = lm.get_input_embeddings().weight.detach().to("cpu")
        self.backbone_device = lm.get_input_embeddings().weight.device

    def name(self, word):
        if word not in self.names:
            ids = self.tokenizer.encode(" " + word, add_special_tokens=False)
            self.names[word] = self.table[ids].mean(0)
        return self.names[word]

    def embed(self, batch, scenes):
        """Per-sample sequence: request, fields, GOAL, actions, candidates, QUERY, scene so far."""
        device = self.special.device
        long = lambda values: torch.tensor(values, dtype=torch.long, device=device)
        programs, owners = [], []
        for row, (world, task) in enumerate(batch):
            for action in world.spec["actions"]:
                programs.append(serialize(world, action, task["actor"]))
                owners.append(row)
        length = max(map(len, programs))
        grid = torch.zeros(len(programs), length, 3, dtype=torch.long, device=device)
        for index, program in enumerate(programs):
            grid[index, :len(program)] = long(program)
        padding = grid[..., 0] == 0
        hidden = self.kind(grid[..., 0]) + self.slot(grid[..., 1]) + self.value(grid[..., 2]) + \
            self.position(torch.arange(length, device=device))
        hidden = self.expression(hidden, src_key_padding_mask=padding)
        pooled = (hidden * ~padding[..., None]).sum(1) / (~padding).sum(1, keepdim=True)
        encoded = self.action_out(pooled)

        embedding = lambda ids: self.table[ids]
        sequences, layout, cursor = [], [], 0
        for row, (world, task) in enumerate(batch):
            request = embedding(long(self.tokenizer.encode(task["request"], add_special_tokens=False)))
            count = len(world.names)
            structure = torch.cat([self.slot(long(range(1, count + 1))), self.value(long(task["state"])),
                                   self.value(long([high for _, high, _ in world.bounds]))], -1)
            fields = self.field_out(structure) + torch.stack([self.name(name) for name in world.names])
            labels = list(world.spec["actions"])
            actions = encoded[cursor:cursor + len(labels)] + torch.stack([self.name(name) for name in labels])
            cursor += len(labels)
            owner = long([labels.index(action) for action, _ in world.candidates])
            argument = long([next(iter(args.values()), 0) for _, args in world.candidates])
            candidates = actions[owner] + self.candidate_out(torch.cat(
                [self.value(argument), self.candidate_index(long(range(len(owner))))], -1))
            scene = []
            for kind, value in scenes[row]:
                if kind == "s":
                    scene.append(self.scene_in.weight[value])
                elif kind == "f":
                    scene.append(self.scene_in.weight[NS] + fields[value])
                else:
                    scene.append(self.scene_in.weight[NS + 1] + candidates[value])
            parts = [request, fields, self.special[:1], actions, candidates, self.special[1:]]
            if scene:
                parts.append(torch.stack(scene))
            start = len(request)
            goal = start + count
            first = goal + 1 + len(labels)
            layout.append({"field_at": list(range(start, goal)), "goal": goal,
                           "candidate_at": list(range(first, first + len(owner))),
                           "query": first + len(owner), "F": count, "C": len(owner)})
            sequences.append(torch.cat(parts))
        longest = max(map(len, sequences))
        inputs = torch.zeros(len(batch), longest, sequences[0].shape[-1], device=device)
        mask = torch.zeros(len(batch), longest, dtype=torch.long, device=device)
        for row, sequence in enumerate(sequences):
            inputs[row, :len(sequence)] = sequence
            mask[row, :len(sequence)] = 1
        return inputs.to(self.backbone_device), mask.to(self.backbone_device), layout

    def read_goal(self, hidden, layout, batch):
        """Typed goal from the GOAL position: atom count, field pointer, comparator and value per atom."""
        loss, goals, correct = 0, [], []
        for row, (world, task) in enumerate(batch):
            place = layout[row]
            state = hidden[row, place["goal"]]
            keys = self.goal_key(hidden[row, place["field_at"]])
            pointer = self.goal_query(state).view(2, -1) @ keys.T / math.sqrt(WIDTH)
            comparator = self.goal_comparator(state).view(2, -1)
            value = self.goal_value(state).view(2, -1)
            count = self.goal_count(state)
            truth = task.get("goal", [])  # absent for live requests
            if truth:
                loss = loss + F.cross_entropy(count[None], torch.tensor([len(truth) - 1], device=state.device))
            for atom, (field, name, target) in enumerate(truth):
                at = lambda index: torch.tensor([index], device=state.device)
                loss = loss + F.cross_entropy(pointer[atom][None], at(field)) + \
                    F.cross_entropy(comparator[atom][None], at(COMPARATORS.index(name))) + \
                    F.cross_entropy(value[atom][None], at(target))
            read = [(int(pointer[atom].argmax()), COMPARATORS[int(comparator[atom].argmax())],
                     int(value[atom].argmax())) for atom in range(int(count.argmax()) + 1)]
            goals.append(read)
            correct.append(sorted(read) == sorted(map(tuple, truth)))
        return loss / len(batch), goals, correct

    def forward(self, batch, scenes=None, teacher=0.0, sever=False, oracle_goal=False):
        scenes = scenes if scenes is not None else [holo.reference(world, task)[:-1] for world, task in batch]
        inputs, mask, layout = self.embed(batch, scenes)
        device = self.special.device
        run = {"loss": 0, "goals": None, "steps": 0, "rollouts": None}

        def write_back(hidden, round_index):
            delta = torch.zeros_like(hidden)
            for row, (world, _) in enumerate(batch):
                place, tape = layout[row], run["rollouts"][row]
                values = torch.full((place["C"], MAX_FIELDS), 10, dtype=torch.long, device=device)
                flags = torch.zeros(place["C"], 2 + len(BLOCKS), device=device)
                for candidate, (state, reached) in enumerate(tape):
                    if state >= 0:
                        values[candidate, :place["F"]] = torch.tensor(world.states[state], device=device)
                        flags[candidate, 0] = 1
                    flags[candidate, 1] = float(reached)
                flags[:, 2 + round_index] = 1
                features = torch.cat([self.tape_value(values).flatten(1), flags], -1)
                delta[row, place["candidate_at"]] = self.tape(features)
            return hidden if sever else hidden + delta

        def block(round_index):
            def hook(_, __, output):
                hidden = (output[0] if isinstance(output, tuple) else output).to(device)
                if round_index == 0:
                    loss, goals, correct = self.read_goal(hidden, layout, batch)
                    run["loss"] += loss
                    run["goals"], run["goal_correct"] = goals, correct
                    if not self.executor:
                        return output
                    run["used"], run["rollouts"] = [], []
                    for row, (world, task) in enumerate(batch):
                        truth = [tuple(atom) for atom in task.get("goal", [])]
                        goal = truth if truth and (oracle_goal or random.random() < teacher) else goals[row]
                        table = world.table(task["actor"])[world.index[tuple(task["state"])]]
                        run["used"].append(goal)
                        run["rollouts"].append([(after, after >= 0 and world.satisfied(world.states[after], goal))
                                                for after in table])
                        run["steps"] += len(table)
                else:
                    self.continue_rollouts(hidden, layout, batch, run, round_index, teacher)
                hidden = write_back(hidden, round_index).to(self.backbone_device)
                return (hidden, *output[1:]) if isinstance(output, tuple) else hidden
            return hook

        handles = [self.lm.model.layers[layer - 1].register_forward_hook(block(index))
                   for index, layer in enumerate(BLOCKS) if self.executor or index == 0]
        try:
            final = self.lm.model(inputs_embeds=inputs, attention_mask=mask).last_hidden_state.to(device)
        finally:
            for handle in handles:
                handle.remove()

        answers, scene_logits, loss = [], [], run["loss"]
        for row, (world, task) in enumerate(batch):
            place = layout[row]
            query = final[row, place["query"]]
            candidates = final[row, place["candidate_at"]]
            scores = torch.cat([self.answer_key(candidates) @ self.answer_query(query) / math.sqrt(WIDTH),
                                self.refuse(query)])
            if "optimal" in task:
                loss = loss + set_nll(scores, task["optimal"] or [place["C"]]) / len(batch)
            answers.append(scores.detach().cpu())
            states = final[row, place["query"]:place["query"] + len(scenes[row]) + 1]
            fact_query, control_query = self.scene_query(states).chunk(2, -1)
            fields = self.scene_fact(final[row, place["field_at"]])
            logits = torch.cat([self.scene_struct(states), fact_query @ fields.T / math.sqrt(WIDTH),
                                control_query @ self.scene_control(candidates).T / math.sqrt(WIDTH)], -1)
            scene_logits.append(logits)
        return {"loss": loss, "answers": answers, "scene_logits": scene_logits, "layout": layout,
                "goals": run["goals"], "goal_correct": run["goal_correct"], "steps": run["steps"],
                "tokens": float(mask.sum()) / len(batch)}

    def continue_rollouts(self, hidden, layout, batch, run, round_index, teacher):
        """For each candidate's rollout the model picks a continuation and the executor applies it exactly."""
        for row, (world, task) in enumerate(batch):
            place, tape = layout[row], run["rollouts"][row]
            states = hidden[row, place["candidate_at"]]
            logits = self.choose_query[round_index - 1](states) @ self.choose_key[round_index - 1](states).T
            logits = torch.cat([logits / math.sqrt(WIDTH), self.choose_stop[round_index - 1](states)], -1)
            table = world.table(task["actor"])
            distance = world.distance(task["actor"], task["goal"]) if "goal" in task else None
            follow_teacher = distance is not None and random.random() < teacher
            for candidate, (state, reached) in enumerate(tape):
                if state < 0 or reached:
                    continue
                enabled = [after >= 0 for after in table[state]] + [True]
                row_logits = logits[candidate].masked_fill(
                    ~torch.tensor(enabled, device=hidden.device), float("-inf"))
                choice = int(row_logits.argmax())
                if distance is not None:
                    here = distance[state]
                    best = [index for index, after in enumerate(table[state])
                            if after >= 0 and here and distance[after] == here - 1] or [place["C"]]
                    run["loss"] += set_nll(row_logits, best) / (len(batch) * place["C"])
                    choice = best[0] if follow_teacher else choice
                if choice < place["C"]:
                    after = table[state][choice]
                    tape[candidate] = (after, world.satisfied(world.states[after], run["used"][row]))
                    run["steps"] += 1

    def scene_loss(self, output, batch):
        loss = 0
        for row, (world, task) in enumerate(batch):
            place = output["layout"][row]
            target = [value if kind == "s" else NS + value if kind == "f" else NS + place["F"] + value
                      for kind, value in holo.reference(world, task)]
            loss = loss + F.cross_entropy(output["scene_logits"][row],
                                          torch.tensor(target, device=output["scene_logits"][row].device))
        return loss / len(batch)

    @torch.no_grad()
    def decode(self, batch, constrained=True, **options):
        """Stream scene tokens greedily. With the grammar, only authorized controls are ever decodable."""
        scenes = [[] for _ in batch]
        active = list(range(len(batch)))
        first = None
        while active:
            output = self.forward([batch[row] for row in active], [scenes[row] for row in active], **options)
            first = first or output
            for position, row in enumerate(active):
                world, task = batch[row]
                place = output["layout"][position]
                logits = output["scene_logits"][position][-1].cpu()
                tokens = [("s", index) for index in range(NS)] + [("f", index) for index in range(place["F"])] + \
                    [("c", index) for index in range(place["C"])]
                if constrained:
                    legal = [index for index, ok in enumerate(task["legal"]) if ok]
                    options_now = set(holo.allowed(scenes[row], range(place["F"]), legal))
                    keep = torch.tensor([token in options_now for token in tokens])
                    logits = logits.masked_fill(~keep, float("-inf"))
                scenes[row].append(tokens[int(logits.argmax())])
            active = [row for row in active if scenes[row][-1] != holo.END and len(scenes[row]) < holo.MAX_TOKENS]
        return scenes, first


class HybridArm(DeckArm):
    """The contract stays text, where the pretrained reader is strong. Executor blocks write exact
    rollouts onto the option lines and the answer is a pointer to one of them."""

    def embed(self, batch, scenes):
        sequences, layout = [], []
        for world, task in batch:
            text = describe(world, task, request_first=True)
            encoded = self.tokenizer(text, return_offsets_mapping=True, add_special_tokens=False)
            ends, cursor = [], 0
            for line in text.split("\n"):
                cursor += len(line)
                ends.append(cursor - 1)
                cursor += 1
            token_at = lambda char: next(index for index, (start, stop) in enumerate(encoded["offset_mapping"])
                                         if start <= char < stop)
            fields, actions, options = len(world.names), len(world.spec["actions"]), len(world.candidates)
            first = 1 + fields + 2 + actions + 2  # state header, fields, policy, Rules:, rules, request, Options:
            layout.append({"field_at": [token_at(ends[1 + index]) for index in range(fields)],
                           "candidate_at": [token_at(ends[first + index]) for index in range(options)],
                           "goal": len(encoded["input_ids"]) - 1, "query": len(encoded["input_ids"]) - 1,
                           "F": fields, "C": options})
            sequences.append(self.table[encoded["input_ids"]])
        longest = max(map(len, sequences))
        inputs = torch.zeros(len(batch), longest, sequences[0].shape[-1])
        mask = torch.zeros(len(batch), longest, dtype=torch.long)
        for row, sequence in enumerate(sequences):
            inputs[row, :len(sequence)] = sequence
            mask[row, :len(sequence)] = 1
        return inputs.to(self.backbone_device), mask.to(self.backbone_device), layout
