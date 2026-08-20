"""Reference-only side check -- NOT part of the Qwen/Kanana candidate comparison.

Same idea as run_eval_claude.py: run the same v2 system prompt and 64-claim
smoke dataset through a model hosted on NVIDIA's build.nvidia.com (OpenAI-
compatible NIM endpoint) as an informational comparison point. Does not
affect the finalized Qwen candidate selection.

Usage:
    python -m src.eval.run_eval_nvidia nemotron [--split smoke]
"""

import argparse
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import ValidationError

from src.eval.metrics import EvalRecord, compute_all
from src.verifier.client import SYSTEM_PROMPT
from src.verifier.schemas import VERIFIER_JSON_SCHEMA, VerifierOutput

REPO_ROOT = Path(__file__).resolve().parents[2]
CLAIM_DATASET_PATH = REPO_ROOT / "data" / "smoke" / "claim_dataset.json"
RESULTS_DIR = REPO_ROOT / "results" / "eval"

BASE_URL = "https://integrate.api.nvidia.com/v1"
MODEL_NAMES = {
    "nemotron": "nvidia/nemotron-3-ultra-550b-a55b",
}

WARMUP_EVIDENCE = "12개월 정기예금 기본금리는 연 3.0%이다."
WARMUP_CLAIM = "12개월 정기예금 기본금리는 연 3.0%이다."

_client = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        load_dotenv(REPO_ROOT / ".env")
        api_key = os.environ.get("NVIDIA_API_KEY")
        if not api_key:
            raise RuntimeError("NVIDIA_API_KEY not set -- add it to .env")
        _client = OpenAI(base_url=BASE_URL, api_key=api_key, timeout=90)
    return _client


def load_claims(split: str) -> list[dict]:
    claims = json.loads(CLAIM_DATASET_PATH.read_text(encoding="utf-8"))
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


def verify_nvidia(evidence: str, claim: str, model_name: str):
    client = get_client()
    user = f"Evidence: {evidence}\n\nClaim: {claim}"
    t0 = time.perf_counter()
    response = client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        temperature=0,
        max_tokens=512,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        response_format={"type": "json_schema", "json_schema": {"name": "verifier_output", "schema": VERIFIER_JSON_SCHEMA}},
    )
    latency = time.perf_counter() - t0
    raw_content = response.choices[0].message.content
    try:
        output = VerifierOutput.model_validate(json.loads(raw_content))
        return True, output, raw_content, None, latency
    except (json.JSONDecodeError, ValidationError) as e:
        return False, None, raw_content, str(e), latency


def run(model_key: str, split: str) -> dict:
    model_name = MODEL_NAMES[model_key]
    claims = load_claims(split)
    if not claims:
        raise ValueError(f"no claims found for split={split!r} in {CLAIM_DATASET_PATH}")

    out_path = RESULTS_DIR / f"{split}_nvidia-{model_key}_prompt-v2.json"
    predictions = load_checkpoint(out_path)
    done_ids = {p["claim_id"] for p in predictions}
    remaining = [c for c in claims if c["claim_id"] not in done_ids]

    if done_ids:
        print(f"[run_eval_nvidia] resuming: {len(done_ids)} already done, {len(remaining)} remaining")

    if remaining:
        print(f"[{model_key}/{split}] warm-up call (excluded from latency)...")
        verify_nvidia(WARMUP_EVIDENCE, WARMUP_CLAIM, model_name)

    for claim in remaining:
        try:
            schema_valid, output, raw_content, error, latency = verify_nvidia(
                claim["evidence_text"], claim["claim_text"], model_name
            )
        except Exception as e:
            print(f"[run_eval_nvidia] ERROR on {claim['claim_id']}: {e} -- stopping, {len(predictions)} results saved")
            save_checkpoint(out_path, f"nvidia-{model_key}", split, predictions, complete=False)
            raise

        predicted = output.verdict.value if schema_valid else None
        predictions.append({
            "claim_id": claim["claim_id"],
            "gold_label": claim["label"],
            "error_type": claim["error_type"],
            "predicted_verdict": predicted,
            "schema_valid": schema_valid,
            "latency_seconds": round(latency, 3),
            "reason": output.reason if schema_valid else None,
            "raw_content": raw_content,
            "error": error,
        })
        save_checkpoint(out_path, f"nvidia-{model_key}", split, predictions, complete=False)
        print(f"[{model_key}/{split}] {claim['claim_id']}: gold={claim['label']} pred={predicted}")

    save_checkpoint(out_path, f"nvidia-{model_key}", split, predictions, complete=True)

    metrics = compute_all([prediction_to_eval_record(p) for p in predictions])
    print(f"[run_eval_nvidia] metrics -> {out_path.relative_to(REPO_ROOT)}")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("model_key", choices=list(MODEL_NAMES))
    parser.add_argument("--split", default="smoke")
    args = parser.parse_args()
    run(args.model_key, args.split)


if __name__ == "__main__":
    main()
