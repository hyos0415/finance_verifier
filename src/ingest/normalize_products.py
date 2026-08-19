"""Issue #6 — join baseList/optionList into one canonical product record per product.

Join key is (fin_co_no, fin_prdt_cd), same as the profiling script. Free-text
fields that are natural "no info" placeholders (see text_utils.is_no_info)
are normalized to null so the Claim Decomposer never treats "없음" as real
evidence text — this is the natural_missing source identified in #5's
eval-design review.

Usage:
    python -m src.ingest.normalize_products [snapshot_path]
"""

import json
import sys
from pathlib import Path

from src.ingest.text_utils import is_no_info

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SNAPSHOT = REPO_ROOT / "data" / "raw" / "finlife_deposit_2026-08-18_page1.json"
OUT_PATH = REPO_ROOT / "data" / "normalized" / "deposit_products_canonical.json"

# Finlife API's own controlled vocabulary for join_deny (this snapshot only
# has "1", but the code is a fixed 3-value enum per the API docs).
JOIN_DENY_LABELS = {"1": "제한없음", "2": "서민전용", "3": "일부제한"}


def clean_text(value):
    return None if is_no_info(value) else value


def build_options(options: list[dict]) -> list[dict]:
    cleaned = [
        {
            "save_trm_months": int(o["save_trm"]),
            "base_rate": o.get("intr_rate"),
            "max_rate": o.get("intr_rate2"),
            "rate_type": o.get("intr_rate_type_nm"),
        }
        for o in options
        if o.get("save_trm") is not None
    ]
    return sorted(cleaned, key=lambda o: o["save_trm_months"])


def build_record(product_id: str, base: dict, options: list[dict]) -> dict:
    return {
        "product_id": product_id,
        "fin_co_no": base.get("fin_co_no"),
        "fin_prdt_cd": base.get("fin_prdt_cd"),
        "bank_name": base.get("kor_co_nm"),
        "product_name": base.get("fin_prdt_nm"),
        "join_way": base.get("join_way"),
        "join_member": base.get("join_member"),
        "join_deny_code": base.get("join_deny"),
        "join_deny_label": JOIN_DENY_LABELS.get(base.get("join_deny")),
        "max_limit": base.get("max_limit"),
        "spcl_cnd": clean_text(base.get("spcl_cnd")),
        "mtrt_int": clean_text(base.get("mtrt_int")),
        "etc_note": clean_text(base.get("etc_note")),
        "disclosure": {
            "dcls_month": base.get("dcls_month"),
            "dcls_strt_day": base.get("dcls_strt_day"),
            "dcls_end_day": base.get("dcls_end_day"),
            "fin_co_subm_day": base.get("fin_co_subm_day"),
        },
        "options": build_options(options),
    }


def main() -> None:
    snapshot_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SNAPSHOT
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    result = snapshot["data"]["result"]
    base_list = result["baseList"]
    option_list = result["optionList"]

    options_by_product = {}
    for opt in option_list:
        key = (opt.get("fin_co_no"), opt.get("fin_prdt_cd"))
        options_by_product.setdefault(key, []).append(opt)

    records = []
    for idx, base in enumerate(base_list, start=1):
        product_id = f"p{idx:03d}"
        key = (base.get("fin_co_no"), base.get("fin_prdt_cd"))
        records.append(build_record(product_id, base, options_by_product.get(key, [])))

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[normalize] {len(records)} products -> {OUT_PATH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
