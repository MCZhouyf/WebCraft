"""Budgeted OpenAI-compatible client with persistent usage and canary accounting."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from openai import OpenAI


ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "corpus/private/llm_state.json"
LEDGER_PATH = ROOT / "corpus/private/llm_usage.jsonl"
LOCK_PATH = ROOT / "corpus/private/llm.lock"
RESPONSE_DIR = ROOT / "corpus/private/llm_responses"
CANARY_MESSAGES = [
    {"role": "system", "content": "Return exactly one JSON object and no prose."},
    {"role": "user", "content": '{"canary":"WebCraft fixed gateway integrity input"}'},
]
RATE_LIMIT_BACKOFF_SECONDS = 8.0


class BudgetStop(RuntimeError):
    pass


def _decimal(name: str) -> Decimal:
    value = Decimal(os.environ[name])
    if not value.is_finite() or value < 0:
        raise ValueError(f"invalid numeric configuration: {name}")
    return value


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


class BudgetedLLMClient:
    def __init__(self) -> None:
        required = (
            "WC_LLM_MODEL",
            "WC_LLM_BASE_URL",
            "WC_LLM_API_KEY",
            "WC_LLM_CURRENCY",
            "WC_LLM_BUDGET",
            "WC_LLM_PRICE_IN_PER_MTOK",
            "WC_LLM_PRICE_OUT_PER_MTOK",
            "WC_LLM_PRICE_CACHED_IN_PER_MTOK",
        )
        if any(not os.environ.get(name) for name in required):
            raise ValueError("missing WC_LLM configuration")
        if os.environ.get("CUDA_VISIBLE_DEVICES") != "":
            raise ValueError("CUDA must be disabled")
        self.model = os.environ["WC_LLM_MODEL"]
        self.currency = os.environ["WC_LLM_CURRENCY"]
        self.budget = _decimal("WC_LLM_BUDGET")
        self.price_in = _decimal("WC_LLM_PRICE_IN_PER_MTOK")
        self.price_out = _decimal("WC_LLM_PRICE_OUT_PER_MTOK")
        self.price_cached = _decimal("WC_LLM_PRICE_CACHED_IN_PER_MTOK")
        self.client = OpenAI(
            base_url=os.environ["WC_LLM_BASE_URL"],
            api_key=os.environ["WC_LLM_API_KEY"],
            timeout=90.0,
            max_retries=0,
        )
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOCK_PATH.touch(mode=0o600, exist_ok=True)

    def close(self) -> None:
        self.client.close()

    def _initial_state(self) -> dict[str, Any]:
        prior = Decimal("0")
        old_ledger = ROOT / "logs/stages/sensenova_glm52_budget.json"
        if old_ledger.exists():
            prior = Decimal(json.loads(old_ledger.read_text()).get("spent_known_amount", "0"))
        return {
            "ordinary_calls": 0,
            "canary_calls": 0,
            "spent": str(prior),
            "currency": self.currency,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cached_tokens": 0,
            "canary_response_hash": None,
            "last_attempt_epoch": 0.0,
        }

    def _state(self) -> dict[str, Any]:
        return json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else self._initial_state()

    def _reserve(self, messages: list[dict[str, str]], max_tokens: int) -> Decimal:
        # UTF-8 bytes are a conservative upper bound for tokenizer input units.
        input_upper = len(json.dumps(messages, ensure_ascii=False).encode()) + 256
        return (
            Decimal(input_upper) * max(self.price_in, self.price_cached)
            + Decimal(max_tokens) * self.price_out
        ) / Decimal(1_000_000)

    def _perform(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int,
        purpose: str,
        is_canary: bool,
        seed: int | None = None,
    ) -> str:
        request_body = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
        }
        if seed is not None:
            request_body["seed"] = seed
        request_hash = hashlib.sha256(
            json.dumps(request_body, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        with LOCK_PATH.open("r+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = self._state()
            reserve = self._reserve(messages, max_tokens)
            if Decimal(state["spent"]) + reserve >= self.budget * Decimal("0.8"):
                raise BudgetStop("80% budget threshold would be reached")
            state["last_attempt_epoch"] = time.time()
            _atomic_json(STATE_PATH, state)
            started = time.monotonic()
            try:
                extra_body: dict[str, Any] = {"thinking": {"type": "disabled"}}
                if seed is not None:
                    extra_body["seed"] = seed
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=False,
                    response_format={"type": "json_object"},
                    extra_body=extra_body,
                )
            except Exception as exc:
                record = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "purpose": purpose,
                    "is_canary": is_canary,
                    "request_hash": request_hash,
                    "status": "request_failed_without_response_usage",
                    "error_class": type(exc).__name__,
                    "http_status": getattr(exc, "status_code", None),
                    "usage_available": False,
                    "cost_accounted": False,
                    "latency_seconds": round(time.monotonic() - started, 3),
                }
                with LEDGER_PATH.open("a") as handle:
                    handle.write(json.dumps(record, sort_keys=True) + "\n")
                raise
            latency = time.monotonic() - started
            if not response.model or response.model != self.model:
                raise RuntimeError("gateway model field missing or changed")
            usage = response.usage
            if usage is None:
                raise RuntimeError("gateway response omitted usage")
            prompt = usage.prompt_tokens
            completion = usage.completion_tokens
            total = usage.total_tokens
            if any(type(x) is not int or x < 0 for x in (prompt, completion, total)):
                raise RuntimeError("invalid token usage")
            if total != prompt + completion:
                raise RuntimeError("inconsistent token usage")
            details = getattr(usage, "prompt_tokens_details", None)
            cached = getattr(details, "cached_tokens", None) if details else None
            cache_reported = type(cached) is int and 0 <= cached <= prompt
            if not cache_reported:
                cached = 0
            cost = (
                Decimal(prompt - cached) * self.price_in
                + Decimal(cached) * self.price_cached
                + Decimal(completion) * self.price_out
            ) / Decimal(1_000_000)
            content = response.choices[0].message.content or ""
            response_hash = hashlib.sha256(content.encode()).hexdigest()
            RESPONSE_DIR.mkdir(parents=True, exist_ok=True)
            (RESPONSE_DIR / f"{response_hash}.txt").write_text(content)
            if is_canary:
                baseline = state["canary_response_hash"]
                if baseline is not None and baseline != response_hash:
                    raise RuntimeError("deterministic canary response changed")
                state["canary_response_hash"] = response_hash
                state["canary_calls"] += 1
            else:
                state["ordinary_calls"] += 1
            state["spent"] = str(Decimal(state["spent"]) + cost)
            state["prompt_tokens"] += prompt
            state["completion_tokens"] += completion
            state["cached_tokens"] += cached
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "purpose": purpose,
                "is_canary": is_canary,
                "request_hash": request_hash,
                "response_hash": response_hash,
                "model": response.model,
                "usage": {
                    "prompt_tokens": prompt,
                    "completion_tokens": completion,
                    "total_tokens": total,
                    "cached_tokens": cached,
                    "cache_detail_reported": cache_reported,
                },
                "cost": str(cost),
                "currency": self.currency,
                "latency_seconds": round(latency, 3),
            }
            with LEDGER_PATH.open("a") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
            _atomic_json(STATE_PATH, state)
            return content

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.0,
        max_tokens: int = 1800,
        purpose: str,
        seed: int | None = None,
    ) -> str:
        for attempt in range(4):
            try:
                content = self._perform(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    purpose=purpose,
                    is_canary=False,
                    seed=seed,
                )
                break
            except Exception as exc:
                if getattr(exc, "status_code", None) != 429 or attempt == 3:
                    raise
                time.sleep(RATE_LIMIT_BACKOFF_SECONDS * 2**attempt)
        state = self._state()
        if state["ordinary_calls"] % 200 == 0:
            for attempt in range(4):
                try:
                    self._perform(
                        CANARY_MESSAGES,
                        temperature=0.0,
                        max_tokens=64,
                        purpose="fixed_canary",
                        is_canary=True,
                        seed=None,
                    )
                    break
                except Exception as exc:
                    if getattr(exc, "status_code", None) != 429 or attempt == 3:
                        raise
                    time.sleep(RATE_LIMIT_BACKOFF_SECONDS * 2**attempt)
        return content
