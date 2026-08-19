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

from src.verifier.langfuse_client import get_langfuse, get_or_create_prompt
from src.verifier.schemas import VERIFIER_JSON_SCHEMA, VerifierOutput

ENDPOINT = "http://localhost:8000/v1/chat/completions"
TEMPERATURE = 0
MAX_TOKENS = 512
PROMPT_NAME = "verifier-system-prompt"

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
    "evidence에 없는 외부 지식을 사용하지 말고, 주어진 evidence만으로 판단하라.\n\n"
    "reason은 반드시 한 문장, 100자 이내로 간결하게 작성하라. evidence 원문을 다시 인용하거나 "
    "같은 근거를 여러 번 반복하지 마라."
)


@dataclass
class VerifierResult:
    model_key: str
    schema_valid: bool
    output: Optional[VerifierOutput]
    raw_content: str
    error: Optional[str]
    latency_seconds: float


def _flatten_metadata(metadata: Optional[dict]) -> dict:
    """Langfuse v4 metadata is dict[str, str] (200-char values) -- lists/None
    etc. get silently coerced/dropped otherwise, so flatten explicitly."""
    if not metadata:
        return {}
    flat = {}
    for key, value in metadata.items():
        if value is None:
            continue
        text = ",".join(value) if isinstance(value, (list, tuple)) else str(value)
        flat[key] = text[:200]
    return flat


def verify(evidence: str, claim: str, model_key: str, metadata: Optional[dict] = None) -> VerifierResult:
    config = MODEL_CONFIGS[model_key]
    prompt_obj = get_or_create_prompt(PROMPT_NAME, SYSTEM_PROMPT)
    system_text = prompt_obj.prompt if prompt_obj else SYSTEM_PROMPT

    payload = {
        "model": config["model"],
        "messages": [
            {"role": "system", "content": system_text},
            {"role": "user", "content": f"Evidence: {evidence}\n\nClaim: {claim}"},
        ],
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "response_format": {"type": "json_schema", "json_schema": {"name": "verifier_output", "schema": VERIFIER_JSON_SCHEMA}},
    }
    if config["chat_template_kwargs"]:
        payload["chat_template_kwargs"] = config["chat_template_kwargs"]

    langfuse = get_langfuse()
    with langfuse.start_as_current_observation(
        as_type="generation",
        name="verifier-call",
        model=config["model"],
        input={"evidence": evidence, "claim": claim},
        metadata={"model_key": model_key, **_flatten_metadata(metadata)},
        prompt=prompt_obj,
    ) as generation:
        t0 = time.perf_counter()
        response = requests.post(ENDPOINT, json=payload, timeout=60)
        response.raise_for_status()
        latency = time.perf_counter() - t0

        raw_content = response.json()["choices"][0]["message"]["content"]
        generation.update(output=raw_content)

        try:
            output = VerifierOutput.model_validate(json.loads(raw_content))
            generation.update(metadata={"schema_valid": "true", "verdict": output.verdict.value})
            return VerifierResult(model_key, True, output, raw_content, None, latency)
        except (json.JSONDecodeError, ValidationError) as e:
            generation.update(metadata={"schema_valid": "false"}, level="ERROR", status_message=str(e))
            return VerifierResult(model_key, False, None, raw_content, str(e), latency)
