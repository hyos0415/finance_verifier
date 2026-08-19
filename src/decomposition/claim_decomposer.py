"""Issue #12 — decompose synthetic answers into atomic claims + attach metadata.

Sanity checks are rule-based, not another LLM call (CLAUDE.md: "최소 sanity
check만"):
  - Atomicity: does each decomposed claim contain exactly one factual assertion?
  - Coverage: does every number/percentage in the original answer survive into
    at least one decomposed claim?

Metadata (label/error_type/reasoning_type) is known from generation time
(#12's generate_synthetic_answers.py controls what was injected) and is
carried onto each decomposed claim. When decomposition doesn't cleanly map
1:1 to what was intended (more claims came out than expected, or a scenario
was deliberately multi-claim), MIXED_SUB_CLAIM_RULES resolves per-claim
metadata by keyword match; anything left ambiguous is flagged for review
rather than silently guessed.

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

# Only scenarios deliberately built as multi-claim need per-claim metadata
# rules; everything else expects a single decomposed claim carrying the
# scenario's top-level metadata straight through.
MIXED_SUB_CLAIM_RULES = {
    "p002_c05": [
        {
            "match": ["3.65", "기본금리"],
            "label": "SUPPORTED",
            "error_type": None,
            "reasoning_type": ["numeric_threshold"],
        },
        {
            "match": ["보너스", "0.1"],
            "label": "UNSUPPORTED",
            "error_type": "conditional_benefit_generalization",
            "reasoning_type": ["all_of"],
        },
    ]
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


def resolve_claim_metadata(scenario: dict, claim_text: str, num_claims: int) -> dict:
    rules = MIXED_SUB_CLAIM_RULES.get(scenario["claim_id"])
    if rules:
        for rule in rules:
            if all(kw in claim_text for kw in rule["match"]):
                return {
                    "label": rule["label"],
                    "error_type": rule["error_type"],
                    "reasoning_type": rule["reasoning_type"],
                    "insufficient_source": None,
                    "needs_manual_review": False,
                }
        return {
            "label": None,
            "error_type": None,
            "reasoning_type": [],
            "insufficient_source": None,
            "needs_manual_review": True,
        }

    if num_claims == 1:
        return {
            "label": scenario["label"],
            "error_type": scenario["error_type"],
            "reasoning_type": scenario["reasoning_type"],
            "insufficient_source": scenario["insufficient_source"],
            "needs_manual_review": False,
        }

    # Decomposition split a claim we expected to stay atomic — flag it rather
    # than guess which sub-claim inherits the scenario's gold label.
    return {
        "label": scenario["label"],
        "error_type": scenario["error_type"],
        "reasoning_type": scenario["reasoning_type"],
        "insufficient_source": scenario["insufficient_source"],
        "needs_manual_review": True,
    }


def main() -> None:
    answers = json.loads(ANSWERS_PATH.read_text(encoding="utf-8"))

    dataset = []
    for scenario in answers:
        claims = decompose(scenario["answer_text"])
        coverage_ok, missing_numbers = check_coverage(scenario["answer_text"], claims)

        for i, claim_text in enumerate(claims, start=1):
            metadata = resolve_claim_metadata(scenario, claim_text, len(claims))
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
