"""TypeSafe Jev(System One) 대조 실행 — Qwen 후보 선정에는 영향을 주지 않는 side check.

Qwen(로컬 vLLM)과 같은 (Evidence, Claim) 쌍을 같은 판정 경계로 돌려, 3분류를 얼마나
가르는지만 비교한다. Jev는 텍스트를 생성하지 않고 타입이 정해진 선택지 + 확률을 낸다.

## Qwen 경로와 의도적으로 다른 점 (공정성 해석에 필요)

1. **프롬프트가 1:1이 아니다.** production 프롬프트(Langfuse v6)의 *판정 경계 3줄*은 그대로
   옮겼지만, "새 답변을 생성하지 말라"와 "reason은 100자 이내"는 뺐다 — Jev는 애초에 문자열을
   생성하지 않아 두 지시가 성립하지 않는다. 즉 같은 프롬프트가 아니라 **같은 판정 기준**이다.
2. **Schema Valid Rate가 구조적으로 1.0이다.** Jev는 정의된 선택지 밖으로 나갈 수 없다
   (type-error 0%). Qwen의 1.0은 guided decoding + Pydantic 재검증으로 얻은 값이라
   같은 의미가 아니다. 이 지표로 두 시스템을 비교하지 말 것.
3. **latency가 비교 대상이 아니다.** 로컬 GPU 서빙(Qwen) vs 한국→미국 hosted API 왕복(Jev).
   report.md §8과 같은 단서가 그대로 적용된다. 기록은 하되 우열 근거로 쓰지 않는다.
4. **reason이 없다.** Qwen 경로의 실패 분석(report §5.2의 "모델이 든 이유" 열)은 `reason`
   필드에서 나왔다. Jev 결과로는 같은 분석을 할 수 없고, 확률 분포만 남는다.

사용:
    python -m src.eval.run_eval_jev --split test
    python -m src.eval.run_eval_jev --split test --limit 10   # 스모크
"""

import argparse
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv

from src.eval.metrics import EvalRecord, compute_all

REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = REPO_ROOT / "results" / "eval"

MODEL_ID = "jev-1.13.0"
INPUT_PRICE_PER_TOKEN = 0.042 / 1_000_000  # $0.042 / 1M input tokens, output 무료

# production 프롬프트(Langfuse v6)의 판정 경계를 그대로 옮긴 것. 문구를 바꾸지 말 것 —
# 바꾸면 Qwen 수치와 더 이상 같은 기준이 아니다.
CRITERIA = {
    "SUPPORTED": "evidence가 claim을 직접 뒷받침한다",
    "UNSUPPORTED": "evidence가 claim과 명시적으로 충돌한다",
    "INSUFFICIENT": "evidence에 판단할 정보 자체가 없다 (충돌이 아니라 정보 부재)",
}
INSTRUCTIONS = (
    "금융 답변을 검증하는 판정이다. 주어진 (evidence, claim) 쌍에 대해 근거 관계만 판정하라. "
    "evidence에 없는 외부 지식을 사용하지 말고, 주어진 evidence만으로 판단하라."
)

WARMUP_EVIDENCE = "12개월 정기예금 기본금리는 연 3.0%이다."
WARMUP_CLAIM = "12개월 정기예금 기본금리는 연 3.0%이다."

_client = None


def get_client():
    global _client
    if _client is None:
        from typesafe_sdk import TypeSafeClient

        load_dotenv(REPO_ROOT / ".env")
        if not os.environ.get("TYPESAFE_API_KEY"):
            raise RuntimeError("TYPESAFE_API_KEY not set -- add it to .env")
        _client = TypeSafeClient(model=MODEL_ID, timeout=60)
    return _client


def load_claims(split: str) -> list[dict]:
    path = REPO_ROOT / "data" / split / "claim_dataset.json"
    claims = json.loads(path.read_text(encoding="utf-8"))
    return [c for c in claims if c["dataset_split"] == split]


def load_checkpoint(out_path: Path) -> list[dict]:
    if not out_path.exists():
        return []
    return json.loads(out_path.read_text(encoding="utf-8")).get("predictions", [])


def prediction_to_eval_record(pred: dict) -> EvalRecord:
    return EvalRecord(
        gold_label=pred["gold_label"],
        predicted_verdict=pred["predicted_verdict"],
        # Jev는 선택지 밖으로 나갈 수 없어 항상 True다. Qwen의 1.0과 같은 의미가 아니다.
        schema_valid=pred["schema_valid"],
        latency_seconds=pred["latency_seconds"],
    )


def save_checkpoint(out_path: Path, split: str, predictions: list[dict], complete: bool) -> None:
    metrics = compute_all([prediction_to_eval_record(p) for p in predictions])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "model": MODEL_ID,
                "split": split,
                "complete": complete,
                # 재현 계약: 이 두 개가 바뀌면 같은 실험이 아니다.
                "criteria": CRITERIA,
                "instructions": INSTRUCTIONS,
                "schema_valid_rate_note": "Jev는 type-error 0%라 구조적으로 1.0 — Qwen과 비교 금지",
                "metrics": metrics,
                "cost_usd_total": round(sum(p.get("cost_usd", 0.0) for p in predictions), 6),
                "predictions": predictions,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def verify_jev(evidence: str, claim: str) -> dict:
    from typesafe_sdk import Choice

    state = {"evidence": evidence, "claim": claim}
    t0 = time.perf_counter()
    r = get_client().system_one(
        state=state,
        questions={"verdict": Choice(instructions=INSTRUCTIONS, criteria=CRITERIA)},
    )
    latency = time.perf_counter() - t0
    ans = r.answers["verdict"]
    probs = getattr(ans, "probabilities", None)
    input_tokens = getattr(r.usage, "input_tokens", 0) or 0
    return {
        "verdict": str(getattr(ans, "choice", "")).strip(),
        "confidence": getattr(ans, "confidence", None),
        "probabilities": dict(probs) if probs else None,
        "input_tokens": input_tokens,
        "cost_usd": round(input_tokens * INPUT_PRICE_PER_TOKEN, 8),
        "model_returned": getattr(r, "model", None),
        "latency_seconds": round(latency, 3),
    }


def run(split: str, limit: int | None = None) -> dict:
    claims = load_claims(split)
    if not claims:
        raise ValueError(f"no claims found for split={split!r}")
    if limit:
        claims = claims[:limit]

    suffix = f"_limit{limit}" if limit else ""
    out_path = RESULTS_DIR / f"{split}_jev{suffix}.json"
    predictions = load_checkpoint(out_path)
    done = {p["claim_id"] for p in predictions}
    remaining = [c for c in claims if c["claim_id"] not in done]

    if done:
        print(f"[run_eval_jev] resuming: {len(done)} done, {len(remaining)} remaining")
    if remaining:
        print("[jev] warm-up call (excluded from latency)...")
        verify_jev(WARMUP_EVIDENCE, WARMUP_CLAIM)

    for claim in remaining:
        try:
            res = verify_jev(claim["evidence_text"], claim["claim_text"])
        except Exception as e:
            print(f"[run_eval_jev] ERROR on {claim['claim_id']}: {e} -- {len(predictions)} saved")
            save_checkpoint(out_path, split, predictions, complete=False)
            raise

        predictions.append({
            "claim_id": claim["claim_id"],
            "gold_label": claim["label"],
            # 분해 분석용 메타데이터 — 총점보다 이쪽이 이 실험의 본론이다.
            "error_type": claim.get("error_type"),
            "reasoning_type": claim.get("reasoning_type"),
            "source_field": claim.get("source_field"),
            "predicted_verdict": res["verdict"],
            "schema_valid": res["verdict"] in CRITERIA,
            "confidence": res["confidence"],
            "probabilities": res["probabilities"],
            "latency_seconds": res["latency_seconds"],
            "input_tokens": res["input_tokens"],
            "cost_usd": res["cost_usd"],
            "model_returned": res["model_returned"],
        })
        save_checkpoint(out_path, split, predictions, complete=False)
        mark = "O" if res["verdict"] == claim["label"] else "X"
        print(
            f"[jev/{split}] {mark} {claim['claim_id']}: gold={claim['label']} "
            f"pred={res['verdict']} conf={res['confidence']} {res['latency_seconds']}s"
        )

    save_checkpoint(out_path, split, predictions, complete=True)
    metrics = compute_all([prediction_to_eval_record(p) for p in predictions])
    total_cost = sum(p.get("cost_usd", 0.0) for p in predictions)
    print(f"\n[run_eval_jev] -> {out_path.relative_to(REPO_ROOT)}")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    print(f"input tokens 총합 비용: ${total_cost:.6f}")
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", default="test")
    parser.add_argument("--limit", type=int, default=None, help="앞에서 N건만 (스모크용)")
    args = parser.parse_args()
    run(args.split, args.limit)


if __name__ == "__main__":
    main()
