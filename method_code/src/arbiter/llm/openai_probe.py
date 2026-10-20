"""Strict accounting wrapper for the author-amended same-model probe."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .gateway import GatewayClient, GatewayError


class ProbeRefusal(GatewayError):
    """A provider refusal that must be scored rather than retried."""


class OpenAIProbeClient(GatewayClient):
    """Single-credential GLM client with explicit probe-level counters."""

    def __init__(self, ledger: Path, *, model: str) -> None:
        super().__init__(ledger)
        if self.model != model:
            raise GatewayError("requested probe model differs from preregistration")
        if self.credential_count != 1:
            raise GatewayError("same-model probe requires exactly one credential per run")
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.task_calls = 0
        self.task_input_tokens = 0
        self.task_output_tokens = 0
        self.returned_models: set[str] = set()
        self.priced_cost: float | None = 0.0
        self.task_priced_cost: float | None = 0.0

    def chat(self, call_type: str, messages: list[dict[str, str]], *,
             temperature: float = 0.0, synthetic: bool = False,
             canary: bool = False, max_tokens: int = 4096,
             json_object: bool = False, thinking: str | None = None) -> dict[str, Any]:
        response = super().chat(
            call_type, messages, temperature=temperature, synthetic=synthetic,
            canary=canary, max_tokens=max_tokens, json_object=json_object,
            thinking=thinking,
        )
        usage = response.get("usage") or {}
        inputs = int(usage.get("prompt_tokens", 0) or 0)
        outputs = int(usage.get("completion_tokens", 0) or 0)
        self.calls += 1
        self.input_tokens += inputs
        self.output_tokens += outputs
        self.returned_models.add(str(response.get("model", "")))
        cost = response.get("cost")
        if cost is None:
            self.priced_cost = None
        elif self.priced_cost is not None:
            self.priced_cost += float(cost)
        if not canary:
            self.task_calls += 1
            self.task_input_tokens += inputs
            self.task_output_tokens += outputs
            if cost is None:
                self.task_priced_cost = None
            elif self.task_priced_cost is not None:
                self.task_priced_cost += float(cost)
        if response.get("finish_reason") in {"content_filter", "refusal"}:
            raise ProbeRefusal("model_refusal")
        return response
