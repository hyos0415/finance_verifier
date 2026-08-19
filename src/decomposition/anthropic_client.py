"""Shared Claude API client for the Claim Decomposer pipeline (#12).

Both the synthetic-answer generator and the decomposer call through this so
model/auth/JSON-schema-call config lives in one place.
"""

import json
import os
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL = "claude-sonnet-5"

_client = None


def get_client() -> Anthropic:
    global _client
    if _client is None:
        load_dotenv(REPO_ROOT / ".env")
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set — add it to .env")
        _client = Anthropic(api_key=api_key)
    return _client


def call_json_schema(system: str, user: str, schema: dict, max_tokens: int = 1024) -> dict:
    """Call Claude with a JSON-schema-constrained response, return the parsed dict."""
    client = get_client()
    response = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"format": {"type": "json_schema", "schema": schema}},
    )
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)
