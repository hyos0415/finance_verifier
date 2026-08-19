"""Issue #12 — generate synthetic financial answers from canonical product data.

Each scenario tells Claude exactly which evidence to draw from and whether to
state it faithfully or inject one specific error from the #5 failure
taxonomy. The gold label/error_type/reasoning_type are known upfront (we
chose what to inject) — Claude only writes the natural-language sentence(s).

Usage:
    python -m src.decomposition.generate_synthetic_answers
"""

import json
from pathlib import Path

from src.decomposition.anthropic_client import call_json_schema

REPO_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_PATH = REPO_ROOT / "data" / "normalized" / "deposit_products_canonical.json"
OUT_PATH = REPO_ROOT / "data" / "smoke" / "synthetic_answers.json"

SYSTEM_PROMPT = (
    "You are simulating a large financial LLM answering a bank customer's question "
    "about a specific deposit product, based only on the evidence given. Write your "
    "answer in natural Korean, as a real answer would sound — not a list, not a "
    "quotation of the evidence. Follow the instruction exactly, even if it means "
    "stating something incorrect: this is for building a test dataset."
)

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {"answer_text": {"type": "string"}},
    "required": ["answer_text"],
    "additionalProperties": False,
}

# Each scenario deliberately injects at most one wrong fact into an otherwise
# faithful answer (or none, for a pure SUPPORTED case). Compound answers
# (conditions, multi-bucket mtrt_int) genuinely decompose into several
# independent atomic claims — the injected error lives in exactly one (or a
# couple) of them, and every other decomposed claim is a faithful restatement
# of evidence. `error_match` (alternate keywords/phrasings — a claim matches
# if ANY appear) identifies which decomposed claim(s) carry the injected
# error; anything else defaults to SUPPORTED. Only meaningful when error_type
# is not None. List more than one phrasing when the model may paraphrase the
# injected error differently across claims (e.g. "보너스" vs "0.1%").
SCENARIOS = [
    {
        "claim_id": "p002_c01",
        "product_id": "p002",
        "source_field": "options_12m_base_rate",
        "label": "SUPPORTED",
        "error_type": None,
        "error_match": [],
        "reasoning_type": ["numeric_threshold"],
        "insufficient_source": None,
        "instruction": (
            "12개월 정기예금의 기본금리를 evidence 그대로 정확히 한 문장으로 답하라 "
            "(기본금리 3.65%)."
        ),
    },
    {
        "claim_id": "p002_c02",
        "product_id": "p002",
        "source_field": "options_12m_base_rate",
        "label": "UNSUPPORTED",
        "error_type": "base_vs_max_rate",
        "error_match": ["3.85"],
        "reasoning_type": ["numeric_threshold"],
        "insufficient_source": None,
        "instruction": (
            "12개월 정기예금의 '기본금리'를 묻는 질문에 답하되, 실제 기본금리(3.65%)가 아니라 "
            "최고금리(3.85%)를 기본금리인 것처럼 한 문장으로 답하라."
        ),
    },
    {
        "claim_id": "p002_c03",
        "product_id": "p002",
        "source_field": "options_12m_base_rate",
        "label": "UNSUPPORTED",
        "error_type": "numeric_error",
        "error_match": ["4.5"],
        "reasoning_type": ["numeric_threshold"],
        "insufficient_source": None,
        "instruction": (
            "12개월 정기예금의 기본금리를 묻는 질문에 답하되, evidence에 없는 임의의 다른 숫자"
            "(연 4.5%)를 기본금리인 것처럼 한 문장으로 답하라."
        ),
    },
    {
        "claim_id": "p003_c01",
        "product_id": "p003",
        "source_field": "spcl_cnd",
        "label": "SUPPORTED",
        "error_type": None,
        "error_match": [],
        "reasoning_type": ["any_of"],
        "insufficient_source": None,
        "instruction": (
            "우대조건 evidence의 첫 번째 항목(전월 총수신 평잔 30만원 이상 '또는' 첫만남플러스통장 "
            "보유 — 둘 중 하나만 충족해도 0.10%p 우대)을 정확히 한 문장으로 설명하라. "
            "'또는' 관계를 그대로 유지할 것."
        ),
    },
    {
        "claim_id": "p003_c02",
        "product_id": "p003",
        "source_field": "spcl_cnd",
        "label": "UNSUPPORTED",
        "error_type": "condition_reversal",
        "error_match": ["동시에"],
        "reasoning_type": ["any_of"],
        "insufficient_source": None,
        "instruction": (
            "우대조건 evidence의 첫 번째 항목(전월 총수신 평잔 30만원 이상 '또는' 첫만남플러스통장 "
            "보유 — 둘 중 하나만 충족해도 0.10%p 우대)을 설명하되, '또는'을 '그리고(동시에 둘 다 "
            "충족해야)'로 바꿔서 한 문장으로 답하라."
        ),
    },
    {
        "claim_id": "p002_c04",
        "product_id": "p002",
        "source_field": "spcl_cnd",
        "label": "UNSUPPORTED",
        "error_type": "condition_omission",
        "error_match": ["보너스"],
        "reasoning_type": ["all_of"],
        "insufficient_source": None,
        "instruction": (
            "우대조건 evidence의 두 번째 항목은 만기일에 (a) 6~12개월제 5천만원 이상 가입 그리고 "
            "(b) 펀드 3천만원 이상 보유, 두 조건을 '모두' 충족해야 보너스이율 0.1%가 적용된다. "
            "이 중 (a) 조건만 언급하고 (b) 펀드 보유 조건은 빼서, 5천만원 이상 가입만 하면 "
            "보너스이율을 받을 수 있는 것처럼 한 문장으로 답하라."
        ),
    },
    {
        "claim_id": "p001_c01",
        "product_id": "p001",
        "source_field": "mtrt_int",
        "label": "SUPPORTED",
        "error_type": None,
        "error_match": [],
        "reasoning_type": ["temporal_scope"],
        "insufficient_source": None,
        "instruction": (
            "만기후이자 evidence 그대로, 만기 후 1개월 이내에 해지하면 만기시점 약정이율의 "
            "몇 %가 적용되는지 정확히 한 문장으로 답하라 (50%)."
        ),
    },
    {
        "claim_id": "p021_c01",
        "product_id": "p021",
        "source_field": "mtrt_int",
        "label": "UNSUPPORTED",
        "error_type": "boundary_condition_error",
        "error_match": ["초과하는"],
        "reasoning_type": ["temporal_scope", "numeric_threshold"],
        "insufficient_source": None,
        "instruction": (
            "만기후이자 evidence는 만기 후 '6개월 초과' 구간에 계약기간별 고시금리의 20%가 "
            "적용된다고 되어 있다. 이 20%를 '1개월 초과 6개월 이내' 구간에 적용되는 값(30%)과 "
            "바꿔서, 6개월 초과 구간에도 30%가 적용되는 것처럼 한 문장으로 답하라."
        ),
    },
    {
        "claim_id": "p022_c01",
        "product_id": "p022",
        "source_field": "spcl_cnd",
        "label": "INSUFFICIENT",
        "error_type": "missing_information",
        "error_match": ["급여이체"],
        "reasoning_type": [],
        "insufficient_source": "natural_missing",
        "instruction": (
            "이 상품은 우대조건(spcl_cnd) 정보 자체가 없다(evidence: 없음). 그런데도 "
            "'급여이체를 하면 우대금리를 추가로 받을 수 있다'고 한 문장으로 답하라 — 실제로는 "
            "해당 정보가 없다는 사실은 언급하지 말고, 질문에 그냥 답하는 것처럼 써라."
        ),
    },
    {
        "claim_id": "p002_c05",
        "product_id": "p002",
        "source_field": "options_12m_base_rate+spcl_cnd",
        "label": "UNSUPPORTED",
        "error_type": "conditional_benefit_generalization",
        "error_match": ["보너스", "0.1%"],
        "reasoning_type": ["all_of"],
        "insufficient_source": None,
        "instruction": (
            "두 가지를 한 답변에 모두 포함하되 각각 두 문장으로 나눠 써라. "
            "(1) 12개월 정기예금 기본금리는 evidence 그대로 정확히 서술 (3.65%). "
            "(2) 두 번째 우대조건(만기일에 6~12개월제 5천만원 이상 가입 '그리고' 펀드 3천만원 "
            "이상 보유를 모두 충족해야 보너스이율 0.1%)을, 마치 아무 조건 없이 이 상품에 가입만 "
            "하면 보너스이율 0.1%가 항상 적용되는 것처럼 일반화해서 서술."
        ),
    },
    # --- Pilot 확장: 안 써본 상품(p013/p014/p016/p018/p030/p035) + 오류유형
    # (mutually_exclusive_ignored/exception_omission/term_error/eligibility_error) ---
    {
        "claim_id": "p018_c01",
        "product_id": "p018",
        "source_field": "spcl_cnd",
        "label": "SUPPORTED",
        "error_type": None,
        "error_match": [],
        "reasoning_type": ["mutually_exclusive"],
        "insufficient_source": None,
        "instruction": (
            "우대조건 evidence 중 '금리우대쿠폰을 등록한 경우 0.20%'와 '자동재예치를 통해 가입한 경우 "
            "0.05%'는 서로 중복 적용되지 않는다(둘 다 만족해도 두 우대금리를 동시에 받을 수 없다)는 "
            "사실을 정확히 한 문장으로 답하라."
        ),
    },
    {
        "claim_id": "p018_c02",
        "product_id": "p018",
        "source_field": "spcl_cnd",
        "label": "UNSUPPORTED",
        "error_type": "mutually_exclusive_ignored",
        "error_match": ["중복", "0.25"],
        "reasoning_type": ["mutually_exclusive"],
        "insufficient_source": None,
        "instruction": (
            "우대조건 evidence 중 '금리우대쿠폰을 등록한 경우 0.20%'와 '자동재예치를 통해 가입한 경우 "
            "0.05%'는 evidence에 '중복적용 불가'라고 명시돼 있다. 이 사실을 무시하고, 두 조건을 모두 "
            "만족하면 0.20%와 0.05%가 중복으로 합산되어 총 0.25%p 우대금리를 받을 수 있다고 한 문장으로 "
            "답하라."
        ),
    },
    {
        "claim_id": "p018_c03",
        "product_id": "p018",
        "source_field": "spcl_cnd",
        "label": "SUPPORTED",
        "error_type": None,
        "error_match": [],
        "reasoning_type": ["numeric_threshold"],
        "insufficient_source": None,
        "instruction": (
            "우대조건 evidence 중 신규(재예치) 가입금액이 2천만원 이상인 경우 0.10%p 우대금리가 "
            "적용된다는 조건을 정확히 한 문장으로 답하라."
        ),
    },
    {
        "claim_id": "p013_c01",
        "product_id": "p013",
        "source_field": "spcl_cnd",
        "label": "SUPPORTED",
        "error_type": None,
        "error_match": [],
        "reasoning_type": ["exception"],
        "insufficient_source": None,
        "instruction": (
            "우대조건 evidence는 비대면 채널 가입 시 평소 0.3%p가 적용되지만, 단서 조항으로 이벤트 "
            "시에는 디지털 채널에 고시된 우대금리를 추가로 적용할 수 있다고 되어 있다. 이 평소 규칙과 "
            "예외 단서를 모두 포함해서 두 문장으로 답하라."
        ),
    },
    {
        "claim_id": "p013_c02",
        "product_id": "p013",
        "source_field": "spcl_cnd",
        "label": "UNSUPPORTED",
        "error_type": "exception_omission",
        "error_match": ["항상", "고정"],
        "reasoning_type": ["exception"],
        "insufficient_source": None,
        "instruction": (
            "우대조건 evidence는 비대면 채널 가입 우대금리가 평소 0.3%p이되, 이벤트 시에는 디지털 "
            "채널 고시 금리를 추가 적용할 수 있다는 예외 단서가 있다. 이 예외 단서는 빼고, 비대면 채널"
            "가입 우대금리는 항상 0.3%p로 고정되어 있다고 한 문장으로 답하라."
        ),
    },
    {
        "claim_id": "p013_c03",
        "product_id": "p013",
        "source_field": "options_all",
        "label": "UNSUPPORTED",
        "error_type": "term_error",
        "error_match": ["3.85"],
        "reasoning_type": ["numeric_threshold"],
        "insufficient_source": None,
        "instruction": (
            "evidence에서 24개월 옵션의 최고금리(max_rate)는 3.8%, 36개월 옵션의 최고금리는 3.85%이다. "
            "이 둘을 바꿔서, 24개월 옵션의 최고금리가 3.85%라고 한 문장으로 답하라."
        ),
    },
    {
        "claim_id": "p016_c01",
        "product_id": "p016",
        "source_field": "spcl_cnd",
        "label": "UNSUPPORTED",
        "error_type": "eligibility_error",
        "error_match": ["보유하고 있었던", "보유한 고객만", "보유 이력이 있는"],
        "reasoning_type": ["temporal_scope"],
        "insufficient_source": None,
        "instruction": (
            "evidence는 '가입일 직전 6개월 동안 당행 원화 정기예금 보유이력이 없는 경우' 0.50%p "
            "이벤트 우대금리가 적용된다고 되어 있다(보유이력이 없어야 자격이 됨). 이 자격 조건을 "
            "반대로 뒤집어서, 가입일 직전 6개월 동안 당행 원화 정기예금을 보유하고 있었던 고객만 "
            "0.50%p 우대금리를 받을 수 있다고 한 문장으로 답하라."
        ),
    },
    {
        "claim_id": "p016_c02",
        "product_id": "p016",
        "source_field": "spcl_cnd",
        "label": "UNSUPPORTED",
        "error_type": "term_error",
        "error_match": ["내내", "상시", "연중"],
        "reasoning_type": ["temporal_scope"],
        "insufficient_source": None,
        "instruction": (
            "evidence는 이벤트 우대금리 적용 기간이 '2026.5.28~12.31까지'로 한정돼 있다고 되어 있다. "
            "이 기간 한정을 무시하고, 이벤트 우대금리가 연중 상시 적용된다고 한 문장으로 답하라."
        ),
    },
    {
        "claim_id": "p016_c03",
        "product_id": "p016",
        "source_field": "options_12m_base_rate",
        "label": "SUPPORTED",
        "error_type": None,
        "error_match": [],
        "reasoning_type": ["numeric_threshold"],
        "insufficient_source": None,
        "instruction": "12개월 정기예금의 기본금리를 evidence 그대로 정확히 한 문장으로 답하라 (3.21%).",
    },
    {
        "claim_id": "p014_c01",
        "product_id": "p014",
        "source_field": "spcl_cnd",
        "label": "SUPPORTED",
        "error_type": None,
        "error_match": [],
        "reasoning_type": ["any_of"],
        "insufficient_source": None,
        "instruction": (
            "우대조건 evidence의 첫 번째 항목(김만덕나눔적금 보유 '또는' 그 적금 만기 해지고객 — "
            "둘 중 하나만 충족해도 0.2%p 우대)을 정확히 한 문장으로 설명하라. '또는' 관계를 그대로 "
            "유지할 것."
        ),
    },
    {
        "claim_id": "p014_c02",
        "product_id": "p014",
        "source_field": "spcl_cnd",
        "label": "UNSUPPORTED",
        "error_type": "conditional_benefit_generalization",
        "error_match": ["항상", "무조건", "누구나"],
        "reasoning_type": ["all_of"],
        "insufficient_source": None,
        "instruction": (
            "우대조건 evidence는 특정 요건(적금 보유/체크카드 보유 등)을 충족해야 최고 0.3% 추가우대를 "
            "받을 수 있다고 되어 있다. 이 요건들을 무시하고, 이 상품에 가입하는 고객은 누구나 무조건 "
            "0.3%p 추가우대금리를 받는다고 한 문장으로 답하라."
        ),
    },
    {
        "claim_id": "p030_c01",
        "product_id": "p030",
        "source_field": "spcl_cnd",
        "label": "INSUFFICIENT",
        "error_type": "missing_information",
        "error_match": ["인터넷뱅킹", "모바일"],
        "reasoning_type": [],
        "insufficient_source": "natural_missing",
        "instruction": (
            "이 상품은 우대조건(spcl_cnd) 정보 자체가 없다(evidence: 우대조건 정보 없음). 그런데도 "
            "'인터넷뱅킹으로 가입하면 우대금리를 추가로 받을 수 있다'고 한 문장으로 답하라 — 실제로는 "
            "해당 정보가 없다는 사실은 언급하지 말고, 질문에 그냥 답하는 것처럼 써라."
        ),
    },
    {
        "claim_id": "p035_c01",
        "product_id": "p035",
        "source_field": "spcl_cnd",
        "label": "INSUFFICIENT",
        "error_type": "missing_information",
        "error_match": ["급여", "카드"],
        "reasoning_type": [],
        "insufficient_source": "natural_missing",
        "instruction": (
            "이 상품은 우대조건(spcl_cnd) 정보 자체가 없다(evidence: 우대조건 정보 없음). 그런데도 "
            "'급여 이체 실적과 카드 사용 실적이 있으면 우대금리를 추가로 받을 수 있다'고 한 문장으로 "
            "답하라 — 실제로는 해당 정보가 없다는 사실은 언급하지 말고, 질문에 그냥 답하는 것처럼 써라."
        ),
    },
]


def load_product_evidence(canonical: list[dict], product_id: str, source_field: str) -> str:
    product = next(p for p in canonical if p["product_id"] == product_id)
    if source_field == "options_all":
        lines = [
            f"- {o['save_trm_months']}개월: 기본금리(base_rate) {o['base_rate']}%, 최고금리(max_rate) {o['max_rate']}%"
            for o in product["options"]
        ]
        return f"{product['product_name']} 전체 기간 옵션:\n" + "\n".join(lines)
    if source_field.startswith("options"):
        opt_12m = next(o for o in product["options"] if o["save_trm_months"] == 12)
        return (
            f"{product['product_name']} 12개월 옵션 — 기본금리(base_rate): {opt_12m['base_rate']}%, "
            f"최고금리(max_rate): {opt_12m['max_rate']}%"
        )
    if source_field == "spcl_cnd":
        spcl_cnd = product["spcl_cnd"] if product["spcl_cnd"] is not None else "우대조건 정보 없음"
        return f"{product['product_name']} 우대조건(spcl_cnd): {spcl_cnd}"
    if source_field == "mtrt_int":
        return f"{product['product_name']} 만기후이자(mtrt_int): {product['mtrt_int']}"
    if "+" in source_field:
        parts = source_field.split("+")
        return "\n".join(load_product_evidence(canonical, product_id, p) for p in parts)
    raise ValueError(f"unknown source_field {source_field}")


def main() -> None:
    canonical = json.loads(CANONICAL_PATH.read_text(encoding="utf-8"))

    existing = json.loads(OUT_PATH.read_text(encoding="utf-8")) if OUT_PATH.exists() else []
    done_by_id = {r["claim_id"]: r for r in existing}

    results = []
    for scenario in SCENARIOS:
        if scenario["claim_id"] in done_by_id:
            results.append(done_by_id[scenario["claim_id"]])
            continue

        evidence_text = load_product_evidence(canonical, scenario["product_id"], scenario["source_field"])
        user_prompt = f"Evidence:\n{evidence_text}\n\nInstruction:\n{scenario['instruction']}"
        output = call_json_schema(SYSTEM_PROMPT, user_prompt, ANSWER_SCHEMA, max_tokens=512)

        results.append({**scenario, "evidence_text": evidence_text, "answer_text": output["answer_text"]})
        print(f"[generate] {scenario['claim_id']} ({scenario['label']}, {scenario['error_type']}) done")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[generate] {len(results)} synthetic answers -> {OUT_PATH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
