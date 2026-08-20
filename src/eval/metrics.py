"""Issue #14 — Eval metrics, in CLAUDE.md's stated priority order:

    False Accept Rate (핵심) > UNSUPPORTED Recall / Macro F1 > Schema Valid Rate > Latency

Accuracy is deliberately not computed -- CLAUDE.md marks it a secondary
metric excluded from the priority ranking. Peak VRAM isn't computed here
either; it comes from vLLM's own startup profiling logs (see #7's smoke
report), not from a list of predictions.

False Accept Rate / UNSUPPORTED Recall / Macro F1 are computed over
schema-valid records only. An invalid JSON parse isn't a wrong verdict, it's
a separate failure mode that schema_valid_rate already captures -- folding
it into the classification metrics would conflate two different failure
types under one number.
"""

import statistics
from dataclasses import dataclass
from typing import Optional

LABELS = ("SUPPORTED", "UNSUPPORTED", "INSUFFICIENT")
NEGATIVE_LABELS = ("UNSUPPORTED", "INSUFFICIENT")


@dataclass
class EvalRecord:
    gold_label: str
    predicted_verdict: Optional[str]  # None when schema_valid is False
    schema_valid: bool
    latency_seconds: float


def _round_or_none(value):
    return None if value is None else round(value, 4)


def schema_valid_rate(records: list[EvalRecord]) -> float:
    if not records:
        return 0.0
    return sum(r.schema_valid for r in records) / len(records)


def false_accept_rate(records: list[EvalRecord]) -> Optional[float]:
    """Fraction of actual UNSUPPORTED/INSUFFICIENT claims wrongly approved as SUPPORTED."""
    valid = [r for r in records if r.schema_valid]
    negatives = [r for r in valid if r.gold_label in NEGATIVE_LABELS]
    if not negatives:
        return None
    false_accepts = sum(1 for r in negatives if r.predicted_verdict == "SUPPORTED")
    return false_accepts / len(negatives)


def unsupported_recall(records: list[EvalRecord]) -> Optional[float]:
    valid = [r for r in records if r.schema_valid]
    actual = [r for r in valid if r.gold_label == "UNSUPPORTED"]
    if not actual:
        return None
    correct = sum(1 for r in actual if r.predicted_verdict == "UNSUPPORTED")
    return correct / len(actual)


def macro_f1(records: list[EvalRecord]) -> Optional[float]:
    valid = [r for r in records if r.schema_valid]
    if not valid:
        return None

    f1s = []
    for label in LABELS:
        tp = sum(1 for r in valid if r.gold_label == label and r.predicted_verdict == label)
        fp = sum(1 for r in valid if r.gold_label != label and r.predicted_verdict == label)
        fn = sum(1 for r in valid if r.gold_label == label and r.predicted_verdict != label)
        precision = 0.0 if tp + fp == 0 else tp / (tp + fp)
        recall = 0.0 if tp + fn == 0 else tp / (tp + fn)
        f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
        f1s.append(f1)
    return statistics.mean(f1s)


def latency_percentiles(records: list[EvalRecord]) -> dict:
    latencies = sorted(r.latency_seconds for r in records)
    if not latencies:
        return {"p50": None, "p95": None}

    def percentile(p: float) -> float:
        idx = min(len(latencies) - 1, max(0, round(p * (len(latencies) - 1))))
        return latencies[idx]

    return {"p50": round(percentile(0.5), 3), "p95": round(percentile(0.95), 3)}


def compute_all(records: list[EvalRecord]) -> dict:
    return {
        "n": len(records),
        "false_accept_rate": _round_or_none(false_accept_rate(records)),
        "unsupported_recall": _round_or_none(unsupported_recall(records)),
        "macro_f1": _round_or_none(macro_f1(records)),
        "schema_valid_rate": round(schema_valid_rate(records), 4),
        "latency": latency_percentiles(records),
    }
