"""Issue #12 — decompose synthetic answers into atomic claims + attach metadata.

Sanity checks are rule-based, not another LLM call (CLAUDE.md: "최소 sanity
check만"):
  - Atomicity: does each decomposed claim contain exactly one factual assertion?
  - Coverage: does every number/percentage in the original answer survive into
    at least one decomposed claim?

Metadata resolution assumes each scenario injects at most one wrong fact into
an otherwise faithful answer: any decomposed claim matching the scenario's
`error_match` keywords gets the scenario's label/error_type; every other
claim is a faithful restatement of evidence and defaults to SUPPORTED. This
replaced an earlier "answer = one claim = one label" assumption that broke
the moment a compound answer (AND/OR conditions, multi-bucket mtrt_int)
decomposed into several independently-verifiable atomic claims — most
Level-2 answers do exactly that, so it wasn't an edge case to special-case,
it was the wrong default. Only when the injected error can't be found in any
decomposed claim (error_match matches nothing) do we flag the whole scenario
for manual review rather than guess.

Usage:
    python -m src.decomposition.claim_decomposer
"""

import json
import re
from pathlib import Path

from src.decomposition.anthropic_client import call_json_schema

REPO_ROOT = Path(__file__).resolve().parents[2]
ANSWERS_PATH = REPO_ROOT / "data" / "smoke" / "synthetic_answers.json"
OUT_PATH = REPO_ROOT / "data" / "smoke" / "claim_dataset.json"

SYSTEM_PROMPT = (
    "You decompose a financial answer into atomic claims. Each atomic claim must "
    "state exactly one factual assertion (one number, one condition, or one "
    "relationship) in a single self-contained Korean sentence. Do not add "
    "information that isn't in the answer, and do not drop any factual content — "
    "every number and condition in the original answer must appear in some claim."
)

CLAIMS_SCHEMA = {
    "type": "object",
    "properties": {"claims": {"type": "array", "items": {"type": "string"}}},
    "required": ["claims"],
    "additionalProperties": False,
}

NUMERIC_PATTERN = re.compile(r"\d+(\.\d+)?\s*(%|퍼센트|만원|억원|개월|년)")
SENTENCE_END_PATTERN = re.compile(r"(다|음|니다)\.")

DEFAULT_CLAIM_METADATA = {
    "label": "SUPPORTED",
    "error_type": None,
    "reasoning_type": [],
    "insufficient_source": None,
    "needs_manual_review": False,
}


def decompose(answer_text: str) -> list[str]:
    output = call_json_schema(SYSTEM_PROMPT, f"Answer:\n{answer_text}", CLAIMS_SCHEMA, max_tokens=512)
    return output["claims"]


def check_atomicity(claim_text: str) -> bool:
    """A claim is atomic if it doesn't read as more than one sentence/assertion."""
    return len(SENTENCE_END_PATTERN.findall(claim_text)) <= 1


def check_coverage(original_text: str, claims: list[str]) -> tuple[bool, list[str]]:
    """Every number/percentage in the original answer must survive into some claim."""
    original_numbers = {m.group() for m in NUMERIC_PATTERN.finditer(original_text)}
    covered_numbers = {m.group() for claim in claims for m in NUMERIC_PATTERN.finditer(claim)}
    missing = sorted(original_numbers - covered_numbers)
    return len(missing) == 0, missing


def resolve_claim_metadata(scenario: dict, claims: list[str]) -> list[dict]:
    """One metadata dict per claim in `claims`, same order.

    error_type is None -> every claim is faithful, all get the scenario's own
    (SUPPORTED) label. Otherwise the scenario's label/error_type apply only to
    claims matching error_match; everything else defaults to SUPPORTED. If
    error_match matches nothing at all, the injected error didn't survive
    decomposition recognizably — flag every claim instead of guessing which
    one (if any) is actually wrong.
    """
    if scenario["error_type"] is None:
        return [
            {
                "label": scenario["label"],
                "error_type": None,
                "reasoning_type": scenario["reasoning_type"],
                "insufficient_source": scenario["insufficient_source"],
                "needs_manual_review": False,
            }
            for _ in claims
        ]

    error_match = scenario["error_match"]
    matches = [any(kw in claim_text for kw in error_match) for claim_text in claims]

    if not any(matches):
        return [
            {"label": None, "error_type": None, "reasoning_type": [], "insufficient_source": None,
             "needs_manual_review": True}
            for _ in claims
        ]

    return [
        {
            "label": scenario["label"],
            "error_type": scenario["error_type"],
            "reasoning_type": scenario["reasoning_type"],
            "insufficient_source": scenario["insufficient_source"],
            "needs_manual_review": False,
        }
        if matched else dict(DEFAULT_CLAIM_METADATA)
        for matched in matches
    ]


def main() -> None:
    answers = json.loads(ANSWERS_PATH.read_text(encoding="utf-8"))

    dataset = []
    for scenario in answers:
        claims = decompose(scenario["answer_text"])
        coverage_ok, missing_numbers = check_coverage(scenario["answer_text"], claims)
        per_claim_metadata = resolve_claim_metadata(scenario, claims)

        for i, (claim_text, metadata) in enumerate(zip(claims, per_claim_metadata), start=1):
            dataset.append({
                "claim_id": f"{scenario['claim_id']}_{i}" if len(claims) > 1 else scenario["claim_id"],
                "product_id": scenario["product_id"],
                "source_field": scenario["source_field"],
                "claim_text": claim_text,
                "answer_text": scenario["answer_text"],
                "evidence_text": scenario["evidence_text"],
                **metadata,
                "atomicity_ok": check_atomicity(claim_text),
                "coverage_ok": coverage_ok,
                "coverage_missing_numbers": missing_numbers if i == 1 else [],
                "dataset_split": "smoke",
            })
        print(f"[decompose] {scenario['claim_id']} -> {len(claims)} claim(s), coverage_ok={coverage_ok}")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[decompose] {len(dataset)} atomic claims -> {OUT_PATH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
