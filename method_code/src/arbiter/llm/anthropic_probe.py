"""Minimal Anthropic Messages transport for the preregistered second-model probe."""
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

from .gateway import GatewayError


class SecondModelInfrastructureError(GatewayError):
    """A retryable network or provider-service failure."""


class SecondModelProtocolError(GatewayError):
    """A non-retryable response-contract failure."""


class SecondModelRefusal(GatewayError):
    """A model refusal, which is a scored terminal rather than infrastructure."""


class AnthropicProbeClient:
    """Gateway-compatible client with no credential persistence or implicit retries."""

    def __init__(self, ledger: Path, *, model: str) -> None:
        if not os.environ.get("ANTHROPIC_BASE_URL") or not os.environ.get("ANTHROPIC_API_KEY"):
            raise GatewayError("ANTHROPIC_BASE_URL and ANTHROPIC_API_KEY are required")
        self.model = model
        base = os.environ["ANTHROPIC_BASE_URL"].rstrip("/")
        self._url = base + ("/messages" if base.endswith("/v1") else "/v1/messages")
        self._key = os.environ["ANTHROPIC_API_KEY"]
        self.ledger = ledger
        self.min_interval_seconds = 0.0
        self._last_finished: float | None = None
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.nominal_cost_usd = 0.0
        self.task_calls = 0
        self.task_input_tokens = 0
        self.task_output_tokens = 0
        self.task_nominal_cost_usd = 0.0
        self._noncanary_since_check = 0
        self.returned_models: set[str] = set()

    def _append(self, row: dict[str, Any]) -> None:
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.ledger, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(descriptor, "a") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
        self.ledger.chmod(0o600)

    def validate_canary(self, *, thinking: str | None = None) -> str:
        response = self.chat(
            "canary",
            [
                {"role": "system", "content": "Return only this exact JSON object: {\"action\":\"inventory\",\"args\":{},\"intent\":\"commit\"}."},
                {"role": "user", "content": "Return the exact object now."},
            ],
            temperature=0.0,
            max_tokens=1024,
            json_object=True,
            thinking=thinking,
            canary=True,
        )
        try:
            valid = json.loads(response["content"]) == {
                "action": "inventory", "args": {}, "intent": "commit",
            }
        except (TypeError, ValueError):
            valid = False
        if not valid or response["finish_reason"] != "stop":
            raise SecondModelProtocolError("canary_payload_mismatch")
        return response["model"]

    def chat(self, call_type: str, messages: list[dict[str, str]], *,
             temperature: float = 0.0, synthetic: bool = False,
             canary: bool = False, max_tokens: int = 4096,
             json_object: bool = False, thinking: str | None = None) -> dict[str, Any]:
        del synthetic, json_object
        if thinking not in {None, "disabled"}:
            raise SecondModelProtocolError("thinking must remain disabled")
        if not canary and self._noncanary_since_check >= 200:
            self.validate_canary(thinking=thinking)
            self._noncanary_since_check = 0
        system = [message["content"] for message in messages if message["role"] == "system"]
        conversation = [message for message in messages if message["role"] != "system"]
        if len(system) != 1 or not conversation:
            raise SecondModelProtocolError("unsupported message layout")
        payload = {
            "model": self.model,
            "system": system[0],
            "messages": conversation,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if self._last_finished is not None:
            wait = self.min_interval_seconds - (time.monotonic() - self._last_finished)
            if wait > 0:
                time.sleep(wait)
        prompt_hash = hashlib.sha256(json.dumps(messages, sort_keys=True).encode()).hexdigest()
        request = urllib.request.Request(
            self._url,
            data=json.dumps(payload).encode(),
            headers={
                "x-api-key": self._key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            method="POST",
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                body = json.loads(response.read())
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
                ConnectionError, http.client.RemoteDisconnected,
                http.client.IncompleteRead, json.JSONDecodeError) as exc:
            status = exc.code if isinstance(exc, urllib.error.HTTPError) else None
            self._append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "call_type": call_type, "status": "infrastructure_error",
                "error_type": type(exc).__name__, "http_status": status,
                "prompt_sha256": prompt_hash, "canary": canary,
            })
            if isinstance(status, int) and status not in {408, 429} and status < 500:
                raise SecondModelProtocolError(f"non-retryable provider HTTP {status}") from None
            raise SecondModelInfrastructureError(
                f"second-model infrastructure failure: {type(exc).__name__}"
            ) from None
        finally:
            self._last_finished = time.monotonic()
        if not isinstance(body, dict) or not isinstance(body.get("content"), list):
            raise SecondModelProtocolError("invalid Anthropic response envelope")
        response_model = str(body.get("model", ""))
        if not response_model:
            raise SecondModelProtocolError("missing server model identifier")
        text = "".join(
            block.get("text", "") for block in body["content"]
            if isinstance(block, dict) and block.get("type") == "text"
        )
        usage = body.get("usage") or {}
        input_tokens = int(usage.get("input_tokens", 0) or 0)
        output_tokens = int(usage.get("output_tokens", 0) or 0)
        # Nominal public-list estimate; the relay's actual billed amount is not exposed.
        nominal_cost = (3.0 * input_tokens + 15.0 * output_tokens) / 1_000_000
        stop_reason = str(body.get("stop_reason", ""))
        finish_reason = {"end_turn": "stop", "stop_sequence": "stop", "max_tokens": "length"}.get(
            stop_reason, stop_reason or "unknown"
        )
        self.calls += 1
        self.returned_models.add(response_model)
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.nominal_cost_usd += nominal_cost
        if not canary:
            self._noncanary_since_check += 1
            self.task_calls += 1
            self.task_input_tokens += input_tokens
            self.task_output_tokens += output_tokens
            self.task_nominal_cost_usd += nominal_cost
        self._append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "call_type": call_type, "status": "ok", "model": response_model,
            "requested_model": self.model, "finish_reason": finish_reason,
            "input_tokens": input_tokens, "output_tokens": output_tokens,
            "nominal_cost_usd": nominal_cost,
            "actual_provider_cost": None,
            "elapsed_seconds": time.monotonic() - started,
            "prompt_sha256": prompt_hash,
            "response_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "canary": canary,
        })
        if stop_reason in {"refusal", "model_refusal"}:
            raise SecondModelRefusal("model_refusal")
        return {
            "content": text,
            "model": response_model,
            "usage": {"prompt_tokens": input_tokens, "completion_tokens": output_tokens},
            "cost": nominal_cost,
            "finish_reason": finish_reason,
        }
