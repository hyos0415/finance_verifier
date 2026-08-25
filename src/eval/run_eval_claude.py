"""Reference-only side check -- NOT part of the Qwen/Kanana candidate comparison.

CLAUDE.md restricts the Verifier candidates to two local 3~4B SLMs (Qwen/Kanana);
this script does not change that. It answers a different, ad hoc question: "what
does a large frontier model score on the same task, so we have a rough ceiling to
read the local candidates' numbers against?" Same 64-claim smoke dataset, same v2
system prompt (src.verifier.client.SYSTEM_PROMPT), same metrics.py -- but called
through the Claude API instead of the local vLLM endpoint, so latency numbers are
not comparable (network + hosted-model latency, not local batch=1 GPU serving).

Usage:
    python -m src.eval.run_eval_claude <sonnet|haiku> [--split smoke]
"""

import argparse
import json
import time
from pathlib import Path

from pydantic import ValidationError

from src.decomposition.anthropic_client import call_json_schema
from src.eval.metrics import EvalRecord, compute_all
from src.verifier.client import PROMPT_NAME, SYSTEM_PROMPT
from src.verifier.langfuse_client import get_or_create_prompt
from src.verifier.schemas import VERIFIER_JSON_SCHEMA, VerifierOutput

REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = REPO_ROOT / "results" / "eval"

MODEL_NAMES = {
    "sonnet": "claude-sonnet-5",
    "haiku": "claude-haiku-4-5-20251001",
}

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


def verify_claude(evidence: str, claim: str, model_name: str, system_text: str = SYSTEM_PROMPT):
    user = f"Evidence: {evidence}\n\nClaim: {claim}"
    t0 = time.perf_counter()
    # temperature is deprecated/rejected for claude-sonnet-5 and claude-haiku-4-5 --
    # omit it and rely on each model's fixed default instead.
    try:
        raw_dict = call_json_schema(
            system=system_text, user=user, schema=VERIFIER_JSON_SCHEMA, max_tokens=512, model=model_name
        )
    except (json.JSONDecodeError, StopIteration) as e:
        # 출력이 max_tokens에서 잘려 JSON이 깨지거나 text 블록이 아예 없는 경우. API 장애가 아니라
        # 모델의 스키마 준수 실패이므로 예외로 죽지 말고 schema_valid=False로 집계한다
        # (그게 Schema Valid Rate 지표의 정의다).
        return False, None, "", f"{type(e).__name__}: {e}", time.perf_counter() - t0
    latency = time.perf_counter() - t0
    raw_content = json.dumps(raw_dict, ensure_ascii=False)
    try:
        output = VerifierOutput.model_validate(raw_dict)
        return True, output, raw_content, None, latency
    except ValidationError as e:
        return False, None, raw_content, str(e), latency


def run(model_key: str, split: str, prompt_label: str = "production") -> dict:
    model_name = MODEL_NAMES[model_key]
    # run_eval과 동일하게 Langfuse 라벨로 프롬프트를 지목한다 (production을 이동시키지 않고 변형 A/B).
    prompt_obj = get_or_create_prompt(PROMPT_NAME, SYSTEM_PROMPT, label=prompt_label)
    system_text = prompt_obj.prompt if prompt_obj else SYSTEM_PROMPT
    prompt_version = prompt_obj.version if prompt_obj else 2
    claims = load_claims(split)
    if not claims:
        raise ValueError(f"no claims found for split={split!r} in data/{split}/claim_dataset.json")

    out_path = RESULTS_DIR / f"{split}_claude-{model_key}_prompt-v{prompt_version}.json"
    predictions = load_checkpoint(out_path)
    done_ids = {p["claim_id"] for p in predictions}
    remaining = [c for c in claims if c["claim_id"] not in done_ids]

    if done_ids:
        print(f"[run_eval_claude] resuming: {len(done_ids)} already done, {len(remaining)} remaining")

    if remaining:
        print(f"[{model_key}/{split}] warm-up call (excluded from latency)...")
        verify_claude(WARMUP_EVIDENCE, WARMUP_CLAIM, model_name, system_text)

    for claim in remaining:
        try:
            schema_valid, output, raw_content, error, latency = verify_claude(
                claim["evidence_text"], claim["claim_text"], model_name, system_text
            )
        except Exception as e:
            print(f"[run_eval_claude] ERROR on {claim['claim_id']}: {e} -- stopping, {len(predictions)} results saved")
            save_checkpoint(out_path, f"claude-{model_key}", split, predictions, complete=False)
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
        save_checkpoint(out_path, f"claude-{model_key}", split, predictions, complete=False)
        print(f"[{model_key}/{split}] {claim['claim_id']}: gold={claim['label']} pred={predicted}")

    save_checkpoint(out_path, f"claude-{model_key}", split, predictions, complete=True)

    metrics = compute_all([prediction_to_eval_record(p) for p in predictions])
    print(f"[run_eval_claude] metrics -> {out_path.relative_to(REPO_ROOT)}")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("model_key", choices=["sonnet", "haiku"])
    parser.add_argument("--split", default="smoke")
    parser.add_argument("--prompt-label", default="production",
                        help='Langfuse prompt label (예: "experiment2"로 절차형 프롬프트 대조)')
    args = parser.parse_args()
    run(args.model_key, args.split, args.prompt_label)


if __name__ == "__main__":
    main()
