"""Issue #14 — Eval Harness entrypoint.

Runs every claim in a given dataset_split through one Verifier candidate and
computes the metrics.py priority metrics. Each claim's evidence/claim/gold
metadata was already fixed at Claim Dataset build time (#12) -- this script
only calls the Verifier and scores it.

Two things every run does that a naive loop wouldn't:
  - Warm-up: one throwaway call before the timed loop, so CUDA-graph/kernel
    warm-up on the first real request doesn't inflate CLAUDE.md's latency
    metric (which is defined "warm-up 제외").
  - Checkpointing: results are written to disk after every claim, and a
    resumed run skips claim_ids already present in that file. A vLLM
    server hiccup mid-run costs one claim's retry, not the whole split.

Usage:
    python -m src.eval.run_eval <qwen|kanana> [--split smoke]
"""

import argparse
import json
from pathlib import Path

from src.eval.metrics import EvalRecord, compute_all
from src.verifier.client import PROMPT_NAME, SYSTEM_PROMPT, verify
from src.verifier.langfuse_client import get_langfuse, get_or_create_prompt

REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = REPO_ROOT / "results" / "eval"

WARMUP_EVIDENCE = "12개월 정기예금 기본금리는 연 3.0%이다."
WARMUP_CLAIM = "12개월 정기예금 기본금리는 연 3.0%이다."


def load_claims(split: str) -> list[dict]:
    claim_dataset_path = REPO_ROOT / "data" / split / "claim_dataset.json"
    claims = json.loads(claim_dataset_path.read_text(encoding="utf-8"))
    return [c for c in claims if c["dataset_split"] == split]


def load_checkpoint(out_path: Path) -> list[dict]:
    if not out_path.exists():
        return []
    return json.loads(out_path.read_text(encoding="utf-8")).get("predictions", [])


def save_checkpoint(out_path: Path, model_key: str, split: str, predictions: list[dict], complete: bool) -> None:
    metrics = compute_all([prediction_to_eval_record(p) for p in predictions])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {"model": model_key, "split": split, "complete": complete, "metrics": metrics, "predictions": predictions},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )


def prediction_to_eval_record(pred: dict) -> EvalRecord:
    return EvalRecord(
        gold_label=pred["gold_label"],
        predicted_verdict=pred["predicted_verdict"],
        schema_valid=pred["schema_valid"],
        latency_seconds=pred["latency_seconds"],
    )


def run(model_key: str, split: str, prompt_label: str = "production") -> dict:
    claims = load_claims(split)
    if not claims:
        raise ValueError(f"no claims found for split={split!r} in data/{split}/claim_dataset.json")

    prompt_obj = get_or_create_prompt(PROMPT_NAME, SYSTEM_PROMPT, label=prompt_label)
    prompt_version = prompt_obj.version if prompt_obj else "unknown"
    out_path = RESULTS_DIR / f"{split}_{model_key}_prompt-v{prompt_version}.json"
    predictions = load_checkpoint(out_path)
    done_ids = {p["claim_id"] for p in predictions}
    remaining = [c for c in claims if c["claim_id"] not in done_ids]

    if done_ids:
        print(f"[run_eval] resuming: {len(done_ids)} already done, {len(remaining)} remaining")

    if remaining:
        print(f"[{model_key}/{split}] warm-up call (excluded from latency)...")
        verify(WARMUP_EVIDENCE, WARMUP_CLAIM, model_key, metadata={"purpose": "warmup"}, prompt_label=prompt_label)

    for claim in remaining:
        metadata = {
            "product_id": claim["product_id"],
            "source_field": claim["source_field"],
            "gold_label": claim["label"],
            "error_type": claim["error_type"],
            "reasoning_type": claim["reasoning_type"],
            "dataset_split": claim["dataset_split"],
        }
        try:
            result = verify(claim["evidence_text"], claim["claim_text"], model_key, metadata=metadata, prompt_label=prompt_label)
        except Exception as e:
            print(f"[run_eval] ERROR on {claim['claim_id']}: {e} -- stopping, {len(predictions)} results saved")
            save_checkpoint(out_path, model_key, split, predictions, complete=False)
            get_langfuse().flush()
            raise

        predicted = result.output.verdict.value if result.schema_valid else None
        predictions.append({
            "claim_id": claim["claim_id"],
            "gold_label": claim["label"],
            "error_type": claim["error_type"],
            "predicted_verdict": predicted,
            "schema_valid": result.schema_valid,
            "latency_seconds": round(result.latency_seconds, 3),
            "reason": result.output.reason if result.schema_valid else None,
            "raw_content": result.raw_content,
            "error": result.error,
        })
        save_checkpoint(out_path, model_key, split, predictions, complete=False)
        print(f"[{model_key}/{split}] {claim['claim_id']}: gold={claim['label']} pred={predicted}")

    save_checkpoint(out_path, model_key, split, predictions, complete=True)
    get_langfuse().flush()

    metrics = compute_all([prediction_to_eval_record(p) for p in predictions])
    print(f"[run_eval] metrics -> {out_path.relative_to(REPO_ROOT)}")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("model_key", choices=["qwen", "kanana"])
    parser.add_argument("--split", default="smoke")
    parser.add_argument("--prompt-label", default="production",
                        help='Langfuse prompt label (예: "experiment"로 production을 건드리지 않고 변형 검증)')
    args = parser.parse_args()
    run(args.model_key, args.split, args.prompt_label)


if __name__ == "__main__":
    main()
