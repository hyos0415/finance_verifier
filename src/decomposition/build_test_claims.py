"""Issue #23 -- build the Test-stage (unseen) Claim Dataset without calling an LLM API.

The Smoke/Pilot pipeline (generate_synthetic_answers.py + claim_decomposer.py)
calls the Claude API twice per scenario: once to phrase a natural-language
answer, once to decompose it into atomic claims. For Test, an agent
(Claude Code or Codex, working directly in this repo) already IS the model
that would have been called -- writing the answer and its decomposition by
hand costs nothing extra and skips the API round-trip entirely.

This script does NOT generate content. It takes hand-authored scenarios
(each with its own answer_text and already-decomposed claims) and:
  1. loads the real evidence_text for the scenario's product/field (same
     loader as generate_synthetic_answers.py), unless evidence_override is
     set (used by the evidence_mismatch error type -- see SCENARIOS_CLAUDE
     for what that means)
  2. runs the exact same rule-based sanity checks as claim_decomposer.py
     (atomicity, coverage)
  3. resolves per-claim label/error_type the same way (error_match keyword
     lookup), so a hand-authored dataset is checked by the identical rules
     an API-generated one would be

Merges scenario lists from multiple authors (Claude Code directly, Codex via
`codex exec`) into one data/test/claim_dataset.json, tagged dataset_split="test".

Usage:
    python -m src.decomposition.build_test_claims
"""

import json
from pathlib import Path

from src.decomposition.claim_decomposer import check_atomicity, check_coverage, resolve_claim_metadata
from src.decomposition.generate_synthetic_answers import load_product_evidence
from src.decomposition.test_scenarios_claude import SCENARIOS_CLAUDE
from src.decomposition.test_scenarios_codex import SCENARIOS_CODEX

REPO_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_PATH = REPO_ROOT / "data" / "normalized" / "deposit_products_canonical.json"
ANSWERS_OUT_PATH = REPO_ROOT / "data" / "test" / "synthetic_answers.json"
CLAIMS_OUT_PATH = REPO_ROOT / "data" / "test" / "claim_dataset.json"

DATASET_SPLIT = "test"


def resolve_evidence(canonical: list[dict], scenario: dict) -> str:
    if scenario.get("evidence_override"):
        return scenario["evidence_override"]
    return load_product_evidence(canonical, scenario["product_id"], scenario["source_field"])


def build(scenarios: list[dict], canonical: list[dict]) -> tuple[list[dict], list[dict]]:
    answers = []
    claims_out = []

    for scenario in scenarios:
        evidence_text = resolve_evidence(canonical, scenario)
        claims = scenario["claims"]

        answers.append({
            **{k: v for k, v in scenario.items() if k not in ("claims",)},
            "evidence_text": evidence_text,
        })

        coverage_ok, missing_numbers = check_coverage(scenario["answer_text"], claims)
        per_claim_metadata = resolve_claim_metadata(scenario, claims)

        for i, (claim_text, metadata) in enumerate(zip(claims, per_claim_metadata), start=1):
            claims_out.append({
                "claim_id": f"{scenario['claim_id']}_{i}" if len(claims) > 1 else scenario["claim_id"],
                "product_id": scenario["product_id"],
                "source_field": scenario["source_field"],
                "claim_text": claim_text,
                "answer_text": scenario["answer_text"],
                "evidence_text": evidence_text,
                **metadata,
                "atomicity_ok": check_atomicity(claim_text),
                "coverage_ok": coverage_ok,
                "coverage_missing_numbers": missing_numbers if i == 1 else [],
                "dataset_split": DATASET_SPLIT,
                "author": scenario["author"],
            })

    return answers, claims_out


def main() -> None:
    canonical = json.loads(CANONICAL_PATH.read_text(encoding="utf-8"))
    all_scenarios = SCENARIOS_CLAUDE + SCENARIOS_CODEX

    seen_ids = set()
    for s in all_scenarios:
        if s["claim_id"] in seen_ids:
            raise ValueError(f"duplicate scenario claim_id: {s['claim_id']}")
        seen_ids.add(s["claim_id"])

    answers, claims_out = build(all_scenarios, canonical)

    flagged = [c for c in claims_out if c.get("needs_manual_review")]
    bad_atomicity = [c for c in claims_out if not c["atomicity_ok"]]
    bad_coverage = [c for c in claims_out if not c["coverage_ok"]]

    ANSWERS_OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    ANSWERS_OUT_PATH.write_text(json.dumps(answers, ensure_ascii=False, indent=2), encoding="utf-8")
    CLAIMS_OUT_PATH.write_text(json.dumps(claims_out, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[build_test_claims] {len(all_scenarios)} scenarios -> {len(claims_out)} claims")
    print(f"  by author: Claude={len(SCENARIOS_CLAUDE)} scenarios, Codex={len(SCENARIOS_CODEX)} scenarios")
    print(f"  needs_manual_review: {len(flagged)} {[c['claim_id'] for c in flagged]}")
    print(f"  atomicity_ok=False: {len(bad_atomicity)} {[c['claim_id'] for c in bad_atomicity]}")
    print(f"  coverage_ok=False: {len(bad_coverage)} {[c['claim_id'] for c in bad_coverage]}")
    print(f"  -> {ANSWERS_OUT_PATH.relative_to(REPO_ROOT)}")
    print(f"  -> {CLAIMS_OUT_PATH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
