"""Issue #13 — Verifier client: (Evidence, Atomic Claim) -> SUPPORTED/UNSUPPORTED/INSUFFICIENT.

Calls whichever candidate (Qwen/Kanana) is currently served by
scripts/run_vllm_container.sh's vLLM OpenAI-compatible endpoint. Everything
except the model name and Qwen's enable_thinking flag (MODEL_CONFIGS) is
held fixed across both candidates so a later comparison isn't confounded by
prompt/config differences (CLAUDE.md's stated methodology).

The Verifier never generates a new answer -- it only judges the given
(evidence, claim) pair against the SUPPORTED/UNSUPPORTED/INSUFFICIENT
boundary CLAUDE.md defines.

Usage:
    from src.verifier.client import verify
    result = verify(evidence, claim, model_key="kanana")
"""

import json
import time
from dataclasses import dataclass
from typing import Optional

import requests
from pydantic import ValidationError

from src.verifier.schemas import VERIFIER_JSON_SCHEMA, VerifierOutput

ENDPOINT = "http://localhost:8000/v1/chat/completions"
TEMPERATURE = 0
MAX_TOKENS = 300

MODEL_CONFIGS = {
    "qwen": {
        "model": "Intel/Qwen3.5-4B-int4-AutoRound",
        "chat_template_kwargs": {"enable_thinking": False},
    },
    "kanana": {
        "model": "kakaocorp/kanana-2-3b-instruct",
        "chat_template_kwargs": None,
    },
}

SYSTEM_PROMPT = (
    "당신은 금융 답변을 검증하는 Verifier다. 새로운 답변을 생성하지 말고, 주어진 "
    "(Evidence, Claim) 쌍에 대해 근거 관계만 판정하라.\n\n"
    "판정 기준:\n"
    "- SUPPORTED: evidence가 claim을 직접 뒷받침한다\n"
    "- UNSUPPORTED: evidence가 claim과 명시적으로 충돌한다\n"
    "- INSUFFICIENT: evidence에 판단할 정보 자체가 없다 (충돌이 아니라 정보 부재)\n\n"
    "evidence에 없는 외부 지식을 사용하지 말고, 주어진 evidence만으로 판단하라."
)


@dataclass
class VerifierResult:
    model_key: str
    schema_valid: bool
    output: Optional[VerifierOutput]
    raw_content: str
    error: Optional[str]
    latency_seconds: float


def verify(evidence: str, claim: str, model_key: str) -> VerifierResult:
    config = MODEL_CONFIGS[model_key]
    payload = {
        "model": config["model"],
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Evidence: {evidence}\n\nClaim: {claim}"},
        ],
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "response_format": {"type": "json_schema", "json_schema": {"name": "verifier_output", "schema": VERIFIER_JSON_SCHEMA}},
    }
    if config["chat_template_kwargs"]:
        payload["chat_template_kwargs"] = config["chat_template_kwargs"]

    t0 = time.perf_counter()
    response = requests.post(ENDPOINT, json=payload, timeout=60)
    response.raise_for_status()
    latency = time.perf_counter() - t0

    raw_content = response.json()["choices"][0]["message"]["content"]

    try:
        output = VerifierOutput.model_validate(json.loads(raw_content))
        return VerifierResult(model_key, True, output, raw_content, None, latency)
    except (json.JSONDecodeError, ValidationError) as e:
        return VerifierResult(model_key, False, None, raw_content, str(e), latency)
