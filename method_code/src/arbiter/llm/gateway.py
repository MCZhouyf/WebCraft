"""Budgeted OpenAI-compatible gateway without credential persistence."""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class GatewayError(RuntimeError):
    pass


class BudgetExceeded(GatewayError):
    pass


class CanaryFailed(GatewayError):
    pass


class GatewayProtocolError(GatewayError):
    pass


class RateLimited(GatewayError):
    pass


class GatewayClient:
    def __init__(self, ledger: Path = Path("logs/arbiter_llm_ledger.jsonl")) -> None:
        required = ("WC_LLM_BASE_URL", "WC_LLM_MODEL", "ARB_LLM_BUDGET")
        missing = [name for name in required if not os.environ.get(name)]
        if not os.environ.get("WC_LLM_API_KEYS") and not os.environ.get("WC_LLM_API_KEY"):
            missing.append("WC_LLM_API_KEY or WC_LLM_API_KEYS")
        if missing:
            raise GatewayError(f"missing gateway environment: {', '.join(missing)}")
        try:
            keys = (json.loads(os.environ["WC_LLM_API_KEYS"])
                    if os.environ.get("WC_LLM_API_KEYS") else [os.environ["WC_LLM_API_KEY"]])
        except (ValueError, KeyError):
            raise GatewayError("invalid credential pool encoding") from None
        if (not isinstance(keys, list) or not keys or
                any(not isinstance(key, str) or not key.strip() or "\n" in key or "\r" in key
                    for key in keys)):
            raise GatewayError("credential pool must contain nonempty strings")
        self._keys = list(dict.fromkeys(keys))
        self._credential_index = 0
        self._rate_limited_slots: set[int] = set()
        self._key = self._keys[0]
        self._url = os.environ["WC_LLM_BASE_URL"].rstrip("/") + "/chat/completions"
        self.model = os.environ["WC_LLM_MODEL"]
        if "glm-5.2" not in self.model.lower():
            raise GatewayError("P5 requires glm-5.2; silent model substitution is forbidden")
        self.currency = os.environ.get("WC_LLM_CURRENCY", "CNY")
        self.budget = float(os.environ["ARB_LLM_BUDGET"])
        self.price_in = float(os.environ.get("WC_LLM_PRICE_IN_PER_MTOK", "0"))
        self.price_out = float(os.environ.get("WC_LLM_PRICE_OUT_PER_MTOK", "0"))
        self.price_cached = float(os.environ.get("WC_LLM_PRICE_CACHED_IN_PER_MTOK", "0"))
        self.ledger = ledger
        self.run_cap = (
            float(os.environ["ARB_LLM_RUN_CAP"])
            if os.environ.get("ARB_LLM_RUN_CAP") else None
        )
        self._run_baseline = self.spent()
        self._blocked = False
        self.min_interval_seconds = 0.0
        self._last_finished: float | None = None

    @property
    def credential_count(self) -> int:
        return len(self._keys)

    def _switch_after_429(self) -> None:
        # A throttled key is quarantined for this client/run, not cycled back.
        self._rate_limited_slots.add(self._credential_index)
        candidates = [index for index in range(len(self._keys))
                      if index not in self._rate_limited_slots]
        if not candidates:
            self._blocked = True
            raise RateLimited("credential_pool_rate_limited") from None
        previous = self._credential_index + 1
        self._credential_index = candidates[0]
        self._key = self._keys[self._credential_index]
        self._append({
            "record_type": "credential_switch", "status": "pending_canary",
            "from_slot": previous, "reason": "http_429",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def _records(self) -> list[dict[str, Any]]:
        if not self.ledger.exists():
            return []
        return [json.loads(line) for line in self.ledger.read_text().splitlines() if line]

    def spent(self) -> float:
        return sum(
            float(record["cost"]) for record in self._records()
            if record.get("status") == "ok" and record.get("cost") is not None
        )

    def remaining_budget(self) -> float:
        return self.budget - self.spent()

    def require_priced_budget(self) -> None:
        """Reject paid runs when the ledger cannot calculate their cost."""
        if self.budget <= 0:
            raise GatewayError("ARB_LLM_BUDGET must be positive for a paid run")
        if self.price_in <= 0 or self.price_out <= 0:
            raise GatewayError(
                "WC_LLM_PRICE_IN_PER_MTOK and WC_LLM_PRICE_OUT_PER_MTOK "
                "must be positive for a paid run"
            )

    def _check_budget(self, messages: list[dict[str, str]], max_tokens: int) -> None:
        if self.spent() >= 0.8 * self.budget:
            raise BudgetExceeded("ARBITER LLM ledger reached the 80% stop threshold")
        if self.run_cap is not None:
            input_upper = len(json.dumps(messages, ensure_ascii=False).encode()) + 256
            reserve = (
                input_upper * max(self.price_in, self.price_cached)
                + max_tokens * self.price_out
            ) / 1_000_000
            incremental = self.spent() - self._run_baseline
            if incremental + reserve > self.run_cap:
                raise BudgetExceeded("ARBITER LLM run cap would be exceeded")

    def _append(self, record: dict[str, Any]) -> None:
        record = {**record, "credential_slot": self._credential_index + 1}
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.ledger, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(descriptor, "a") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
        self.ledger.chmod(0o600)

    def validate_canary(self, *, thinking: str | None = None) -> None:
        """Latch any failed health check until an explicit new client/run."""
        if self._blocked:
            raise CanaryFailed("canary_circuit_open")
        try:
            reply = self.chat(
                "canary", [
                    {"role": "system", "content": "Return only this exact JSON object: {\"action\":\"inventory\",\"args\":{},\"intent\":\"commit\"}."},
                    {"role": "user", "content": "Return the exact object now."},
                ], temperature=0.0, synthetic=True, canary=True,
                max_tokens=1024, json_object=True, thinking=thinking,
            )
            if (reply.get("finish_reason") != "stop" or
                    json.loads(reply["content"]) != {
                        "action": "inventory", "args": {}, "intent": "commit",
                    }):
                raise ValueError("canary_payload_mismatch")
        except (GatewayError, TypeError, ValueError) as exc:
            self._blocked = True
            self._append({
                "record_type": "canary_validation", "status": "failed",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "error_type": type(exc).__name__, "canary": True,
            })
            raise CanaryFailed("canary_validation_failed") from None
        self._append({
            "record_type": "canary_validation", "status": "passed", "canary": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def chat(self, call_type: str, messages: list[dict[str, str]], *,
             temperature: float = 0.0, synthetic: bool = False,
             canary: bool = False, max_tokens: int = 4096,
             json_object: bool = False, thinking: str | None = None) -> dict[str, Any]:
        while True:
            try:
                return self._chat_once(
                    call_type, messages, temperature=temperature, synthetic=synthetic,
                    canary=canary, max_tokens=max_tokens, json_object=json_object,
                    thinking=thinking,
                )
            except RateLimited:
                if self.credential_count == 1:
                    raise
                self._switch_after_429()
                if not canary:
                    self.validate_canary(thinking=thinking)

    def _chat_once(self, call_type: str, messages: list[dict[str, str]], *,
                   temperature: float = 0.0, synthetic: bool = False,
                   canary: bool = False, max_tokens: int = 4096,
                   json_object: bool = False, thinking: str | None = None) -> dict[str, Any]:
        if thinking not in {None, "enabled", "disabled"}:
            raise ValueError("invalid thinking mode")
        if self._blocked:
            raise CanaryFailed("canary_circuit_open")
        self._check_budget(messages, max_tokens)
        if not canary:
            since_check = 0
            for record in reversed(self._records()):
                if record.get("record_type") == "canary_validation":
                    if record.get("status") == "failed":
                        self._blocked = True
                        raise CanaryFailed("canary_circuit_open")
                    break
                if not record.get("canary") and "call_type" in record:
                    since_check += 1
            if since_check >= 200:
                self.validate_canary(thinking=thinking)
        self._check_budget(messages, max_tokens)
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_object:
            payload["response_format"] = {"type": "json_object"}
        if thinking is not None:
            payload["thinking"] = {"type": thinking}
        if self._last_finished is not None:
            remaining = self.min_interval_seconds - (time.monotonic() - self._last_finished)
            if remaining > 0:
                time.sleep(remaining)
        request = urllib.request.Request(
            self._url,
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"},
            method="POST",
        )
        started = time.monotonic()
        prompt_hash = hashlib.sha256(json.dumps(messages, sort_keys=True).encode()).hexdigest()
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                body = json.loads(response.read())
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
                ConnectionError, http.client.RemoteDisconnected,
                http.client.IncompleteRead, json.JSONDecodeError) as exc:
            retry_after = exc.headers.get("Retry-After") if isinstance(exc, urllib.error.HTTPError) else None
            self._append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "call_type": call_type, "status": "infrastructure_error",
                "error_type": type(exc).__name__, "prompt_sha256": prompt_hash,
                "http_status": exc.code if isinstance(exc, urllib.error.HTTPError) else None,
                "retry_after_seconds": float(retry_after) if retry_after and retry_after.isdecimal() else None,
                "synthetic": synthetic, "canary": canary,
            })
            if isinstance(exc, urllib.error.HTTPError) and exc.code == 429:
                raise RateLimited("http_429") from None
            raise GatewayError(f"gateway infrastructure failure: {type(exc).__name__}") from exc
        finally:
            self._last_finished = time.monotonic()
        if (not isinstance(body, dict) or not isinstance(body.get("choices"), list)
                or not body["choices"] or not isinstance(body["choices"][0], dict)
                or not isinstance(body["choices"][0].get("message"), dict)):
            self._blocked = True
            self._append({"call_type": call_type, "status": "protocol_error",
                          "error_type": "invalid_response_envelope", "canary": canary})
            raise GatewayProtocolError("invalid_response_envelope")
        choice = body["choices"][0]
        usage = body.get("usage") or {}
        input_tokens = int(usage.get("prompt_tokens", 0))
        output_tokens = int(usage.get("completion_tokens", 0))
        cached_tokens = int(usage.get("prompt_tokens_details", {}).get("cached_tokens", 0) or 0)
        uncached = max(0, input_tokens - cached_tokens)
        priced = self.price_in > 0 and self.price_out > 0
        cost = None if not priced else (
            uncached * self.price_in + cached_tokens * self.price_cached
            + output_tokens * self.price_out
        ) / 1_000_000
        response_model = str(body.get("model", ""))
        content = choice["message"].get("content")
        model_matches = response_model == self.model
        finish_reason = choice.get("finish_reason")
        self._append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "call_type": call_type, "status": "ok" if model_matches else "model_mismatch", "model": response_model,
            "requested_model": self.model, "finish_reason": finish_reason,
            "thinking": thinking or "provider_default",
            "visible_characters": len(content) if isinstance(content, str) else 0,
            "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
            "input_tokens": input_tokens, "cached_input_tokens": cached_tokens,
            "output_tokens": output_tokens, "cost": cost, "currency": self.currency,
            "cost_status": "priced" if priced else "unpriced",
            "elapsed_seconds": time.monotonic() - started, "prompt_sha256": prompt_hash,
            "response_sha256": hashlib.sha256(str(content).encode()).hexdigest(),
            "synthetic": synthetic, "canary": canary,
        })
        if not model_matches:
            self._blocked = True
            raise GatewayProtocolError("response_model_mismatch")
        return {"content": content, "model": response_model, "usage": usage,
                "cost": cost, "finish_reason": finish_reason}
