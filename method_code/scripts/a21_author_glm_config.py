"""Environment-only gateway configuration for the public source package.

The legacy pure message parser remains for historical regression tests; the
runtime loader never reads chat history or persistent credential files.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Iterable


class AuthorConfigError(RuntimeError):
    pass


MODEL = "glm-5.2"
_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")
_BASE_URL = re.compile(r"https://[A-Za-z0-9][A-Za-z0-9.-]*(?::[0-9]+)?/v1\b")


def _texts(payload: dict[str, Any]) -> list[str]:
    if payload.get("role") == "user":
        return [str(row.get("text", "")) for row in payload.get("content", [])
                if isinstance(row, dict)]
    if payload.get("type") == "user_message":
        return [str(payload.get("message", ""))]
    return []


def parse_author_messages(messages: Iterable[str]) -> dict[str, Any]:
    """Return the only allowed ephemeral config from the newest matching message."""
    matches: list[dict[str, Any]] = []
    for text in messages:
        normalized = text.replace("\\", "")
        if 'base_url' not in normalized or MODEL not in normalized.lower():
            continue
        keys = list(dict.fromkeys(_KEY.findall(normalized)))
        urls = list(dict.fromkeys(_BASE_URL.findall(normalized)))
        if len(keys) == 6 and len(urls) == 1:
            matches.append({'base_url': urls[0], 'api_keys': keys})
    if not matches:
        raise AuthorConfigError("approved_author_glm_configuration_not_found")
    # The latest matching message is authoritative.  It avoids merging keys
    # across requests and never examines unrelated credential files.
    return matches[-1]


def partition_credential_pools(keys: list[str], workers: int) -> list[list[str]]:
    """Give each worker a disjoint round-robin fallback pool."""
    pools = [keys[index::workers] for index in range(workers)]
    if any(not pool for pool in pools) or len({key for pool in pools for key in pool}) != len(keys):
        raise AuthorConfigError("invalid_independent_credential_pool_partition")
    return pools


def load_author_config() -> dict[str, Any]:
    base_url = os.environ.get("ARB_LLM_BASE_URL", "").rstrip("/")
    try:
        keys = json.loads(os.environ.get("ARB_LLM_API_KEYS_JSON", "[]"))
    except ValueError:
        raise AuthorConfigError("invalid_environment_configuration") from None
    if (not base_url or not isinstance(keys, list) or not keys
            or any(not isinstance(key, str) or not key for key in keys)
            or len(set(keys)) != len(keys)):
        raise AuthorConfigError("missing_or_invalid_environment_configuration")
    return {"base_url": base_url, "api_keys": keys}
