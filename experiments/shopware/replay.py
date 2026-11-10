"""Replay the published experiments in a disposable directory."""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def run(script, *args):
    result = subprocess.run(
        [sys.executable, str(script), *args], capture_output=True, text=True
    )
    if result.returncode:
        sys.stderr.write(result.stdout + result.stderr)
        raise SystemExit(result.returncode)
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", action="store_true", help="Refit the small checkpoints with PyTorch.")
    args = parser.parse_args()
    source = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix="holodeck-replay-") as directory:
        root = Path(directory) / "shopware"
        shutil.copytree(
            source, root,
            ignore=shutil.ignore_patterns("state", "results", "training", "__pycache__"),
        )
        core = root / "core"
        native = root / "native"
        training_flags = [] if args.train else ["--skip-training"]
        run(core / "experiment.py", *training_flags)
        run(core / "test_vm.py")
        run(native / "test_native.py")
        export = json.loads(run(native / "export_training.py"))
        report = json.loads((core / "results/report.json").read_text())
        assert report["host_unchanged"] and report["previous_heads_preserved"]
        assert report["before_new_rule"]["exact_trajectories"] == 0
        for name in ("native_shadow", "after_new_rule", "restart_and_checkpoint_swap"):
            result = report[name]
            assert result["exact_trajectories"] == result["trajectories"] == 12, name
        assert report["restart_and_checkpoint_swap"]["processes"] == 48
        assert report["selected_rule_equivalence"]["equivalent_on_domain"]
        assert export["native_exact"] == export["native_total"] == 168
        assert export["ui_records"] == 4
        print(json.dumps({
            "checkpoint_source": "refitted" if args.train else "published",
            "merchant_transfer": "12/12",
            "restart_steps": 48,
            "native_reference_transitions": "168/168",
            "numeric_comparison": report["new_rule_generalization"],
            "bounded_rule_equivalence": True,
            "unit_suites": ["core", "native"],
            "bound_view_examples": 4,
        }, indent=2))


if __name__ == "__main__":
    main()
