"""Issue #5 — profile the raw Finlife deposit product snapshot.

Multi-angle profiling beyond the minimum checklist: per-bank rollups,
base-vs-max rate spread (the base_vs_max_rate taxonomy axis), and free-text
condition-complexity heuristics (AND/OR markers, numeric conditions) that
inform how hard condition_reversal / conditional_benefit_generalization
claims will be to construct.

Usage:
    python -m src.ingest.profile_deposit_products [snapshot_path]
"""

import json
import re
import statistics
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SNAPSHOT = REPO_ROOT / "data" / "raw" / "finlife_deposit_2026-08-18_page1.json"
OUT_PATH = REPO_ROOT / "results" / "profiling" / "deposit_products_profile.json"

FREE_TEXT_FIELDS = ["spcl_cnd", "mtrt_int", "etc_note"]

# Finlife free-text fields use several placeholder spellings for "no info" —
# an exact match on "해당사항 없음" alone misses "없음" / "해당없음" variants
# and silently overcounts natural-missing samples as populated text.
NO_INFO_PLACEHOLDERS = {"해당사항 없음", "없음", "해당없음"}
AND_MARKERS = ["및", "동시에", "모두 충족", "각각"]
OR_MARKERS = ["또는", "혹은", "중 하나"]
NUMERIC_COND_PATTERN = re.compile(r"\d+(\.\d+)?\s*(%|퍼센트|만원|개월|년)")


def is_no_info(value) -> bool:
    return value is None or (isinstance(value, str) and value.strip() in NO_INFO_PLACEHOLDERS | {""})


def non_null_ratio(records: list[dict], field: str) -> float:
    non_null = sum(1 for r in records if not is_no_info(r.get(field)))
    return round(non_null / len(records), 4) if records else 0.0


def natural_missing_products(records: list[dict], field: str) -> list[dict]:
    return [
        {
            "fin_prdt_cd": r.get("fin_prdt_cd"),
            "kor_co_nm": r.get("kor_co_nm"),
            "fin_prdt_nm": r.get("fin_prdt_nm"),
            "raw_value": r.get(field),
        }
        for r in records
        if is_no_info(r.get(field))
    ]


def length_stats(records: list[dict], field: str) -> dict:
    lengths = [len(r[field]) for r in records if not is_no_info(r.get(field))]
    if not lengths:
        return {"n": 0}
    return {
        "n": len(lengths),
        "min": min(lengths),
        "max": max(lengths),
        "mean": round(statistics.mean(lengths), 1),
        "median": statistics.median(lengths),
        "p90": sorted(lengths)[int(len(lengths) * 0.9) - 1] if len(lengths) > 1 else lengths[0],
    }


def condition_complexity(records: list[dict], field: str) -> dict:
    texts = [r[field] for r in records if not is_no_info(r.get(field))]
    if not texts:
        return {"n": 0}
    has_and = sum(1 for t in texts if any(m in t for m in AND_MARKERS))
    has_or = sum(1 for t in texts if any(m in t for m in OR_MARKERS))
    has_both = sum(1 for t in texts if any(m in t for m in AND_MARKERS) and any(m in t for m in OR_MARKERS))
    numeric_hits = [len(NUMERIC_COND_PATTERN.findall(t)) for t in texts]
    return {
        "n": len(texts),
        "and_marker_ratio": round(has_and / len(texts), 3),
        "or_marker_ratio": round(has_or / len(texts), 3),
        "and_and_or_ratio": round(has_both / len(texts), 3),
        "avg_numeric_conditions_per_text": round(statistics.mean(numeric_hits), 2),
        "max_numeric_conditions_in_one_text": max(numeric_hits),
    }


def rate_distribution(options: list[dict]) -> dict:
    by_term = {}
    for opt in options:
        term = opt.get("save_trm")
        rate = opt.get("intr_rate")
        max_rate = opt.get("intr_rate2")
        if term is None or rate is None:
            continue
        by_term.setdefault(term, {"base_rates": [], "max_rates": [], "spreads": []})
        by_term[term]["base_rates"].append(rate)
        if max_rate is not None:
            by_term[term]["max_rates"].append(max_rate)
            by_term[term]["spreads"].append(round(max_rate - rate, 4))

    summary = {}
    for term, vals in sorted(by_term.items(), key=lambda kv: int(kv[0])):
        summary[term] = {
            "n": len(vals["base_rates"]),
            "base_rate_mean": round(statistics.mean(vals["base_rates"]), 3),
            "max_rate_mean": round(statistics.mean(vals["max_rates"]), 3) if vals["max_rates"] else None,
            "spread_mean": round(statistics.mean(vals["spreads"]), 3) if vals["spreads"] else None,
            "spread_max": max(vals["spreads"]) if vals["spreads"] else None,
        }
    return summary


def per_bank_rollup(base_list: list[dict], options_by_product: dict) -> list[dict]:
    banks = {}
    for prod in base_list:
        bank = prod.get("kor_co_nm", "?")
        key = (prod.get("fin_co_no"), prod.get("fin_prdt_cd"))
        opts = options_by_product.get(key, [])
        base_rates = [o["intr_rate"] for o in opts if o.get("intr_rate") is not None]
        max_rates = [o["intr_rate2"] for o in opts if o.get("intr_rate2") is not None]

        entry = banks.setdefault(bank, {"bank": bank, "product_count": 0, "base_rates": [], "max_rates": []})
        entry["product_count"] += 1
        entry["base_rates"].extend(base_rates)
        entry["max_rates"].extend(max_rates)

    rollup = []
    for bank, entry in banks.items():
        rollup.append({
            "bank": bank,
            "product_count": entry["product_count"],
            "avg_base_rate": round(statistics.mean(entry["base_rates"]), 3) if entry["base_rates"] else None,
            "avg_max_rate": round(statistics.mean(entry["max_rates"]), 3) if entry["max_rates"] else None,
        })
    return sorted(rollup, key=lambda r: r["bank"])


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

    option_counts = [len(v) for v in options_by_product.values()]

    complex_examples = sorted(
        (r["spcl_cnd"] for r in base_list if not is_no_info(r.get("spcl_cnd"))),
        key=len,
        reverse=True,
    )[:10]

    profile = {
        "snapshot_source": str(snapshot_path.relative_to(REPO_ROOT)),
        "fetched_at": snapshot.get("fetched_at"),
        "product_count": len(base_list),
        "option_count": len(option_list),
        "base_list_fields": sorted(base_list[0].keys()) if base_list else [],
        "option_list_fields": sorted(option_list[0].keys()) if option_list else [],
        "non_null_ratio": {f: non_null_ratio(base_list, f) for f in FREE_TEXT_FIELDS},
        "text_length_stats": {f: length_stats(base_list, f) for f in FREE_TEXT_FIELDS},
        "condition_complexity": {f: condition_complexity(base_list, f) for f in FREE_TEXT_FIELDS},
        "options_per_product": {
            "n": len(option_counts),
            "min": min(option_counts) if option_counts else 0,
            "max": max(option_counts) if option_counts else 0,
            "mean": round(statistics.mean(option_counts), 2) if option_counts else 0,
        },
        "rate_distribution_by_term_months": rate_distribution(option_list),
        "per_bank_rollup": per_bank_rollup(base_list, options_by_product),
        "complex_condition_examples": complex_examples,
        "natural_missing_spcl_cnd": natural_missing_products(base_list, "spcl_cnd"),
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[profile] product_count={profile['product_count']} option_count={profile['option_count']}")
    print(f"[profile] saved to {OUT_PATH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
