"""Issue #13 — Verifier client smoke test.

Runs a small, hand-picked slice of #12's claim_dataset.json (one per major
label/error_type) through whichever model is currently served on
localhost:8000 (start it with scripts/run_vllm_container.sh first). This
only proves the pipeline runs end to end against each candidate -- it is
NOT a model-selection comparison (that's Pilot's job, per CLAUDE.md's Eval
단계 table).

Usage:
    python -m src.verifier.smoke_test <qwen|kanana>
"""

import json
import sys
from pathlib import Path

from src.verifier.client import verify

REPO_ROOT = Path(__file__).resolve().parents[2]
CLAIM_DATASET_PATH = REPO_ROOT / "data" / "smoke" / "claim_dataset.json"
OUT_PATH = REPO_ROOT / "data" / "smoke" / "verifier_smoke_results.json"

# One representative claim per label/error_type from #12's smoke output.
SMOKE_CLAIM_IDS = [
    "p002_c01",     # SUPPORTED, baseline numeric
    "p002_c02",     # UNSUPPORTED, base_vs_max_rate
    "p003_c02_4",   # UNSUPPORTED, condition_reversal
    "p021_c01_4",   # UNSUPPORTED, boundary_condition_error
    "p022_c01",     # INSUFFICIENT, natural_missing
    "p002_c05_3",   # UNSUPPORTED, conditional_benefit_generalization
]


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in ("qwen", "kanana"):
        print("usage: python -m src.verifier.smoke_test <qwen|kanana>")
        sys.exit(1)
    model_key = sys.argv[1]

    all_claims = {c["claim_id"]: c for c in json.loads(CLAIM_DATASET_PATH.read_text(encoding="utf-8"))}
    claims = [all_claims[cid] for cid in SMOKE_CLAIM_IDS]

    results = []
    for claim in claims:
        result = verify(claim["evidence_text"], claim["claim_text"], model_key)
        gold = claim["label"]
        predicted = result.output.verdict.value if result.schema_valid else None
        results.append({
            "claim_id": claim["claim_id"],
            "gold_label": gold,
            "predicted_verdict": predicted,
            "match": predicted == gold,
            "schema_valid": result.schema_valid,
            "latency_seconds": round(result.latency_seconds, 3),
            "reason": result.output.reason if result.schema_valid else None,
            "error": result.error,
            "raw_content": result.raw_content,
        })
        status = "OK" if result.schema_valid else "SCHEMA_INVALID"
        print(f"[{model_key}] {claim['claim_id']}: gold={gold} pred={predicted} "
              f"({status}, {result.latency_seconds:.2f}s)")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = json.loads(OUT_PATH.read_text(encoding="utf-8")) if OUT_PATH.exists() else {}
    existing[model_key] = results
    OUT_PATH.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[smoke] {len(results)} results -> {OUT_PATH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
