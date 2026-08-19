"""Issue #14 — failure analysis over a completed eval run.

The two failure directions need different breakdowns:
  - False accepts (gold UNSUPPORTED/INSUFFICIENT, predicted SUPPORTED): these
    are claims where an error was deliberately injected (#12), so they break
    down cleanly by #5's error_type taxonomy -- "which injected error type
    does the Verifier fail to catch?"
  - False rejects (gold SUPPORTED, predicted UNSUPPORTED/INSUFFICIENT): these
    claims have error_type=None (nothing was injected, they're faithful
    restatements of evidence), so error_type can't categorize them. Broken
    down by reasoning_type instead, to see whether over-rejection clusters
    around a particular kind of reasoning (e.g. disjunctive/any_of
    conditions) rather than being random noise.

Schema-invalid predictions (predicted_verdict is None) are reported
separately -- they're not a verdict error, Schema Valid Rate already covers
them (see metrics.py's docstring for why classification metrics exclude
them).

Usage:
    python -m src.eval.failure_analysis <qwen|kanana> [--split smoke] [--prompt-version N]
"""

import argparse
import json
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CLAIM_DATASET_PATH = REPO_ROOT / "data" / "smoke" / "claim_dataset.json"
RESULTS_DIR = REPO_ROOT / "results" / "eval"

NEGATIVE_LABELS = ("UNSUPPORTED", "INSUFFICIENT")


def find_results_path(model_key: str, split: str, prompt_version: int | None) -> Path:
    if prompt_version is not None:
        path = RESULTS_DIR / f"{split}_{model_key}_prompt-v{prompt_version}.json"
        if not path.exists():
            raise FileNotFoundError(path)
        return path

    candidates = sorted(
        RESULTS_DIR.glob(f"{split}_{model_key}_prompt-v*.json"),
        key=lambda p: int(p.stem.rsplit("v", 1)[1]),
    )
    if not candidates:
        raise FileNotFoundError(f"no results for {model_key}/{split} in {RESULTS_DIR}")
    return candidates[-1]


def analyze(model_key: str, split: str, prompt_version: int | None = None) -> dict:
    results_path = find_results_path(model_key, split, prompt_version)
    run = json.loads(results_path.read_text(encoding="utf-8"))
    predictions = run["predictions"]

    reasoning_by_claim = {
        c["claim_id"]: c["reasoning_type"]
        for c in json.loads(CLAIM_DATASET_PATH.read_text(encoding="utf-8"))
    }

    schema_invalid = [p for p in predictions if not p["schema_valid"]]
    valid = [p for p in predictions if p["schema_valid"]]

    false_accepts = [p for p in valid if p["gold_label"] in NEGATIVE_LABELS and p["predicted_verdict"] == "SUPPORTED"]
    false_rejects = [p for p in valid if p["gold_label"] == "SUPPORTED" and p["predicted_verdict"] != "SUPPORTED"]
    label_reasoning_mismatch = [
        p for p in valid
        if p["gold_label"] == "INSUFFICIENT" and p["predicted_verdict"] == "UNSUPPORTED"
    ]

    false_accept_by_error_type = Counter(p["error_type"] for p in false_accepts)

    false_reject_by_reasoning_type = Counter()
    for p in false_rejects:
        types = reasoning_by_claim.get(p["claim_id"], [])
        if not types:
            false_reject_by_reasoning_type["(none)"] += 1
        for t in types:
            false_reject_by_reasoning_type[t] += 1

    return {
        "model": model_key,
        "split": split,
        "results_file": str(results_path.relative_to(REPO_ROOT)),
        "n": len(predictions),
        "schema_invalid": [p["claim_id"] for p in schema_invalid],
        "false_accepts": {
            "claim_ids": [p["claim_id"] for p in false_accepts],
            "by_error_type": dict(false_accept_by_error_type),
        },
        "false_rejects": {
            "claim_ids": [p["claim_id"] for p in false_rejects],
            "by_reasoning_type": dict(false_reject_by_reasoning_type),
        },
        "insufficient_labeled_unsupported": [p["claim_id"] for p in label_reasoning_mismatch],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("model_key", choices=["qwen", "kanana"])
    parser.add_argument("--split", default="smoke")
    parser.add_argument("--prompt-version", type=int, default=None)
    args = parser.parse_args()

    report = analyze(args.model_key, args.split, args.prompt_version)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
