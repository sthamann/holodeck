"""Read-only float32 execution of the pinned TinyLlama checkpoint with NumPy."""

import json
from pathlib import Path
import struct

import numpy as np


class Tokenizer:
    def __init__(self, path):
        document = json.loads(Path(path).read_text())
        model = document["model"]
        if model["type"] != "BPE" or not model["byte_fallback"] or document["pre_tokenizer"]:
            raise ValueError("expected the pinned Llama BPE convention")
        self.vocabulary = model["vocab"]
        self.merges = {tuple(pair.split(" ", 1)): rank for rank, pair in enumerate(model["merges"])}

    def encode(self, text, *, bos=True):
        symbols = []
        for character in "▁" + text.replace(" ", "▁"):
            if character in self.vocabulary:
                symbols.append(character)
            else:
                symbols.extend(f"<0x{byte:02X}>" for byte in character.encode())
        while len(symbols) > 1:
            pairs = zip(symbols, symbols[1:])
            rank, position = min((self.merges.get(pair, len(self.merges)), index)
                                 for index, pair in enumerate(pairs))
            if rank == len(self.merges):
                break
            symbols[position:position + 2] = [symbols[position] + symbols[position + 1]]
        return ([1] if bos else []) + [self.vocabulary[symbol] for symbol in symbols]


class SafeTensors:
    def __init__(self, path):
        with Path(path).open("rb") as file:
            header_size, = struct.unpack("<Q", file.read(8))
            self.header = json.loads(file.read(header_size))
        self.bytes = np.memmap(path, mode="r", offset=8 + header_size, dtype=np.uint8)

    def weight(self, name):
        entry = self.header[name]
        begin, end = entry["data_offsets"]
        if entry["dtype"] != "BF16" or (end - begin) % 2:
            raise ValueError(f"unexpected type in {name}")
        data = self.bytes[begin:end].view("<u2").astype(np.uint32)
        value = (data << 16).view(np.float32).reshape(entry["shape"])
        value.flags.writeable = False
        return value


def rms(x, weight, epsilon):
    return x * (1 / np.sqrt(np.mean(x * x, axis=-1, keepdims=True) + epsilon)) * weight


def linear(x, weight):
    return (x.reshape(-1, x.shape[-1]) @ weight.T).reshape(*x.shape[:-1], weight.shape[0])


class TinyLlama:
    def __init__(self, path):
        path = Path(path)
        config = json.loads((path / "config.json").read_text())
        if (config["model_type"], config["hidden_act"], config.get("rope_scaling")) != ("llama", "silu", None):
            raise ValueError("unsupported checkpoint architecture")
        self.tokenizer = Tokenizer(path / "tokenizer.json")
        file = SafeTensors(path / "model.safetensors")
        self.embed = file.weight("model.embed_tokens.weight")
        self.final_norm = file.weight("model.norm.weight")
        self.head = file.weight("lm_head.weight")
        self.epsilon = np.float32(config["rms_norm_eps"])
        self.heads = config["num_attention_heads"]
        self.kv_heads = config["num_key_value_heads"]
        self.width = config["hidden_size"] // self.heads
        self.groups = self.heads // self.kv_heads
        self.inv_freq = np.float32(config["rope_theta"]) ** (
            -np.arange(0, self.width, 2, dtype=np.float32) / self.width
        )
        names = {
            "in_norm": "input_layernorm", "mlp_norm": "post_attention_layernorm",
            "q": "self_attn.q_proj", "k": "self_attn.k_proj", "v": "self_attn.v_proj",
            "o": "self_attn.o_proj", "gate": "mlp.gate_proj", "up": "mlp.up_proj", "down": "mlp.down_proj",
        }
        self.layers = [
            {key: file.weight(f"model.layers.{index}.{name}.weight") for key, name in names.items()}
            for index in range(config["num_hidden_layers"])
        ]

    def rotate(self, x, angles):
        cosine = np.cos(angles)[None, :, None, :]
        sine = np.sin(angles)[None, :, None, :]
        first, second = np.split(x, 2, axis=-1)
        return np.concatenate((first * cosine - second * sine, second * cosine + first * sine), axis=-1)

    def forward(self, ids, targets, *, replacement=None, edit=None, cache=None, keep_cache=False, read=None):
        """Operate on a same-length batch, optionally replacing actual residual slots."""
        ids = np.asarray(ids, dtype=np.int32)
        x = self.embed[ids].copy()
        if replacement is not None:
            replacement(x)
        batch, length, _ = x.shape
        past = 0 if cache is None else cache[0][0].shape[-2]
        positions = np.arange(past, past + length)
        angles = positions.astype(np.float32)[:, None] * self.inv_freq
        mask = np.arange(past + length)[None, :] <= positions[:, None]
        saved = []
        for index, weights in enumerate(self.layers):
            features = rms(x, weights["in_norm"], self.epsilon)
            q = linear(features, weights["q"]).reshape(batch, length, self.heads, self.width)
            k = linear(features, weights["k"]).reshape(batch, length, self.kv_heads, self.width)
            v = linear(features, weights["v"]).reshape(batch, length, self.kv_heads, self.width)
            q = self.rotate(q, angles).reshape(batch, length, self.kv_heads, self.groups, self.width)
            q = q.transpose(0, 2, 3, 1, 4)
            k = self.rotate(k, angles).transpose(0, 2, 1, 3)
            v = v.transpose(0, 2, 1, 3)
            if cache is not None:
                k = np.concatenate((cache[index][0], k), axis=-2)
                v = np.concatenate((cache[index][1], v), axis=-2)
            if keep_cache:
                saved.append((k, v))
            scores = (q @ np.swapaxes(k[:, :, None], -1, -2)) * np.float32(self.width ** -0.5)
            scores = np.where(mask, scores, -np.inf)
            scores -= np.max(scores, axis=-1, keepdims=True)
            np.exp(scores, out=scores)
            scores /= np.sum(scores, axis=-1, keepdims=True)
            context = (scores @ v[:, :, None]).transpose(0, 3, 1, 2, 4).reshape(batch, length, -1)
            x = x + linear(context, weights["o"])
            features = rms(x, weights["mlp_norm"], self.epsilon)
            gate = linear(features, weights["gate"])
            gate /= 1 + np.exp(-np.clip(gate, -80, 80))
            x = x + linear(gate * linear(features, weights["up"]), weights["down"])
            if edit is not None:
                edit(index + 1, x)
        selected = x[:, -1] if read is None else x[np.arange(batch), read]
        scores = linear(rms(selected, self.final_norm, self.epsilon), self.head[targets])
        return (scores, saved) if keep_cache else scores
