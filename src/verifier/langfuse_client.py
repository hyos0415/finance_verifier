"""Issue #14 — shared Langfuse client + prompt management helper.

Langfuse Cloud (US region, existing account) rather than self-hosting --
the eval data is public Finlife disclosure data with no PII and no real end
users, and self-hosting would need a multi-service stack CLAUDE.md already
rules out.

v4's metadata is dict[str, str] only (200-char values, non-strings coerced)
-- see notes/langfuse_v4_notes.md -- so callers must pre-flatten anything
like a list (e.g. reasoning_type) into a string before passing it in.
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from langfuse import Langfuse, get_client

REPO_ROOT = Path(__file__).resolve().parents[2]

_initialized = False


def get_langfuse() -> Langfuse:
    global _initialized
    if not _initialized:
        load_dotenv(REPO_ROOT / ".env")
        _initialized = True
    return get_client()


def get_or_create_prompt(name: str, default_text: str, label: str = "production"):
    """Fetch a Langfuse-managed prompt, seeding it with default_text on first use.

    Falls back to None (caller uses default_text directly, untracked) if
    Langfuse is unreachable -- prompt management is a nice-to-have here, not
    on the critical path.
    """
    langfuse = get_langfuse()
    try:
        return langfuse.get_prompt(name, label=label)
    except Exception:
        pass
    try:
        langfuse.create_prompt(name=name, type="text", prompt=default_text, labels=[label])
        return langfuse.get_prompt(name, label=label)
    except Exception:
        return None
