"""Float32 replay of the pinned LFM2 hybrid backbone for the ASCII goal probes."""

import json
from pathlib import Path
import re

import numpy as np

from llama import SafeTensors, TinyLlama, linear, rms


WORD = re.compile(
    r"(?i:'s|'t|'re|'ve|'m|'ll|'d)|[^\r\nA-Za-z0-9]?[A-Za-z]+|[0-9]{1,3}"
    r"| ?[^\sA-Za-z0-9]+[\r\n]*|\s*[\r\n]+|\s+(?!\S)|\s+"
)


class ByteTokenizer:
    def __init__(self, path):
        model = json.loads(Path(path).read_text())["model"]
        if model["type"] != "BPE" or model["ignore_merges"]:
            raise ValueError("unsupported LFM tokenizer settings")
        self.vocabulary = model["vocab"]
        self.merges = {tuple(pair): index for index, pair in enumerate(model["merges"])}
        direct = [*range(33, 127), *range(161, 173), *range(174, 256)]
        extra = [byte for byte in range(256) if byte not in direct]
        self.byte_map = {value: chr(value) for value in direct}
        self.byte_map.update({value: chr(256 + index) for index, value in enumerate(extra)})

    def encode(self, text, *, bos=True):
        if not text.isascii():
            raise ValueError("the fixture tokenizer supports ASCII")
        pieces = WORD.findall(text)
        if "".join(pieces) != text:
            raise ValueError("tokenizer left an input span")
        ids = [1] if bos else []
        for piece in pieces:
            symbols = [self.byte_map[byte] for byte in piece.encode()]
            while len(symbols) > 1:
                rank, index = min((self.merges.get(pair, len(self.merges)), index)
                                  for index, pair in enumerate(zip(symbols, symbols[1:])))
                if rank == len(self.merges):
                    break
                symbols[index:index + 2] = [symbols[index] + symbols[index + 1]]
            ids.extend(self.vocabulary[token] for token in symbols)
        return ids


class LFM2:
    rotate = TinyLlama.rotate

    def __init__(self, path):
        path = Path(path)
        config = json.loads((path / "config.json").read_text())
        if config["model_type"] != "lfm2" or config["conv_bias"] or not config["tie_embedding"]:
            raise ValueError("expected LFM2 with bias-free convolutions and tied embeddings")
        self.tokenizer = ByteTokenizer(path / "tokenizer.json")
        weights = SafeTensors(path / "model.safetensors")
        self.embed = weights.weight("model.embed_tokens.weight")
        self.final_norm = weights.weight("model.embedding_norm.weight")
        self.epsilon = np.float32(config["norm_eps"])
        self.heads = config["num_attention_heads"]
        self.kv_heads = config["num_key_value_heads"]
        self.width = config["hidden_size"] // self.heads
        self.groups = self.heads // self.kv_heads
        self.inv_freq = np.float32(config["rope_theta"]) ** (
            -np.arange(0, self.width, 2, dtype=np.float32) / self.width
        )
        shared = {"norm": "operator_norm", "ffn": "ffn_norm",
                  "gate": "feed_forward.w1", "down": "feed_forward.w2", "up": "feed_forward.w3"}
        names = {
            "conv": {"kernel": "conv.conv", "enter": "conv.in_proj", "exit": "conv.out_proj"},
            "full_attention": {
                "q": "self_attn.q_proj", "k": "self_attn.k_proj", "v": "self_attn.v_proj",
                "q_norm": "self_attn.q_layernorm", "k_norm": "self_attn.k_layernorm", "o": "self_attn.out_proj",
            },
        }
        self.layers = [
            {key: weights.weight(f"model.layers.{index}.{name}.weight")
             for key, name in {**shared, **names[kind]}.items()}
            for index, kind in enumerate(config["layer_types"])
        ]

    def convolution(self, x, weight):
        b, c, data = np.split(linear(x, weight["enter"]), 3, axis=-1)
        source = b * data
        out = np.zeros_like(source)
        taps = weight["kernel"][:, 0, :]
        for index in range(taps.shape[-1]):
            delay = taps.shape[-1] - 1 - index
            if delay < x.shape[1]:
                out[:, delay:] += source[:, :x.shape[1] - delay] * taps[:, index]
        return linear(c * out, weight["exit"])

    def attention(self, x, weight, angles, mask):
        batch, length, _ = x.shape
        q = linear(x, weight["q"]).reshape(batch, length, self.heads, self.width)
        k = linear(x, weight["k"]).reshape(batch, length, self.kv_heads, self.width)
        v = linear(x, weight["v"]).reshape(batch, length, self.kv_heads, self.width)
        q = self.rotate(rms(q, weight["q_norm"], self.epsilon), angles)
        k = self.rotate(rms(k, weight["k_norm"], self.epsilon), angles)
        q = q.reshape(batch, length, self.kv_heads, self.groups, self.width).transpose(0, 2, 3, 1, 4)
        k = k.transpose(0, 2, 1, 3)
        v = v.transpose(0, 2, 1, 3)
        scores = (q @ np.swapaxes(k[:, :, None], -1, -2)) * np.float32(self.width ** -0.5)
        scores = np.where(mask, scores, -np.inf)
        scores -= np.max(scores, axis=-1, keepdims=True)
        np.exp(scores, out=scores)
        scores /= np.sum(scores, axis=-1, keepdims=True)
        context = (scores @ v[:, :, None]).transpose(0, 3, 1, 2, 4).reshape(batch, length, -1)
        return linear(context, weight["o"])

    def forward(self, ids, targets, *, edit=None, read=None):
        x = self.embed[np.asarray(ids, dtype=np.int32)].copy()
        batch, length, _ = x.shape
        positions = np.arange(length)
        angles = positions.astype(np.float32)[:, None] * self.inv_freq
        mask = positions[None, :] <= positions[:, None]
        for index, weight in enumerate(self.layers):
            features = rms(x, weight["norm"], self.epsilon)
            if "kernel" in weight:
                x = x + self.convolution(features, weight)
            else:
                x = x + self.attention(features, weight, angles, mask)
            features = rms(x, weight["ffn"], self.epsilon)
            gate = linear(features, weight["gate"])
            gate /= 1 + np.exp(-np.clip(gate, -80, 80))
            x = x + linear(gate * linear(features, weight["up"]), weight["down"])
            if edit is not None:
                edit(index + 1, x)
        selected = x[:, -1] if read is None else x[np.arange(batch), read]
        return linear(rms(selected, self.final_norm, self.epsilon), self.embed[targets])
