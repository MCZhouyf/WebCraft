#!/usr/bin/env python3
"""Canary-check author-supplied A21 GLM credentials without persisting them."""
from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from arbiter.llm.gateway import GatewayError
from scripts.a21_author_glm_config import MODEL, load_author_config
from scripts.run_cds_agents_20261017 import AuditedClient


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/post_review/a21_mds_20261018/glm_preflight.json"
LEDGER = ROOT / "artifacts/post_review/a21_mds_20261018/glm_preflight_ledger.jsonl"


def clean_env() -> None:
    for name in ("WC_LLM_API_KEY", "WC_LLM_BASE_URL", "WC_LLM_MODEL", "ARB_LLM_BUDGET",
                 "WC_LLM_PRICE_IN_PER_MTOK", "WC_LLM_PRICE_CACHED_IN_PER_MTOK",
                 "WC_LLM_PRICE_OUT_PER_MTOK", "WC_LLM_CURRENCY"):
        os.environ.pop(name, None)


def main() -> int:
    config = load_author_config()
    lock = Path("/root/autodl-tmp/server.lock").open("r")
    fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
    checked: list[dict[str, object]] = []
    old = os.environ.copy()
    try:
        for slot, key in enumerate(config["api_keys"], 1):
            clean_env()
            os.environ.update({"WC_LLM_API_KEY": key, "WC_LLM_BASE_URL": config["base_url"],
                               "WC_LLM_MODEL": MODEL, "ARB_LLM_BUDGET": "1000000",
                               "WC_LLM_PRICE_IN_PER_MTOK": "8", "WC_LLM_PRICE_CACHED_IN_PER_MTOK": "2",
                               "WC_LLM_PRICE_OUT_PER_MTOK": "28", "WC_LLM_CURRENCY": "CNY"})
            client = AuditedClient(LEDGER, model=MODEL)
            try:
                client.validate_canary(thinking="disabled")
                checked.append({"credential_slot": slot, "status": "passed",
                                "server_model_identifiers": sorted(client.returned_models)})
            except GatewayError as exc:
                checked.append({"credential_slot": slot, "status": "failed",
                                "error_type": type(exc).__name__})
        # This result deliberately contains neither the endpoint nor credential material.
        OUT.write_text(json.dumps({"requested_model": MODEL, "checked": checked,
                                   "passed": sum(row["status"] == "passed" for row in checked),
                                   "credential_count": len(checked),
                                   "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                                   "credentials_persisted": False}, indent=2, sort_keys=True) + "\n")
        return 0 if any(row["status"] == "passed" for row in checked) else 1
    finally:
        clean_env()
        os.environ.clear(); os.environ.update(old)
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
