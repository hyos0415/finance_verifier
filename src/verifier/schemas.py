"""Issue #13 — Verifier output schema.

Matches CLAUDE.md's Verifier 출력 스키마 exactly: verdict enum + evidence +
reason, no confidence field. Both the JSON schema (for vLLM's guided
decoding / response_format) and the Pydantic model (for the Schema Valid
Rate eval metric) live here so they can't drift apart.
"""

from enum import Enum

from pydantic import BaseModel


class Verdict(str, Enum):
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    INSUFFICIENT = "INSUFFICIENT"


class VerifierOutput(BaseModel):
    verdict: Verdict
    evidence: str
    reason: str


VERIFIER_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["SUPPORTED", "UNSUPPORTED", "INSUFFICIENT"]},
        "evidence": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "evidence", "reason"],
    "additionalProperties": False,
}
