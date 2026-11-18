"""Score the judgement cases with the frozen backbone. Writes the archive that the replay consumes."""

import json
from pathlib import Path
import sys

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

ROOT = Path(__file__).resolve().parent


def main(path):
    tokenizer = AutoTokenizer.from_pretrained(path)
    lm = AutoModelForCausalLM.from_pretrained(path, dtype=torch.float32).eval()
    yes, no = (tokenizer.encode(word, add_special_tokens=False) for word in ("yes", "no"))
    assert len(yes) == len(no) == 1
    data = json.loads((ROOT / "judgement_cases.json").read_text())
    scores = {}
    with torch.no_grad():
        for name, question in data["criteria"].items():
            scores[name] = []
            for message, *_ in data["cases"]:
                prompt = tokenizer.apply_chat_template(
                    [{"role": "user", "content": f'Customer message: "{message}"\n{question} '
                                                 "Answer with yes or no only."}],
                    tokenize=False, add_generation_prompt=True, enable_thinking=False)
                logits = lm(**tokenizer(prompt, return_tensors="pt")).logits[0, -1]
                scores[name].append(round(float(logits[yes[0]] - logits[no[0]]), 4))
    print(json.dumps({"model": Path(path).parent.parent.name.replace("models--", "").replace("--", "/"),
                      "revision": Path(path).name, "scores": scores}, indent=1))


if __name__ == "__main__":
    main(sys.argv[1])
