"""The only two ARBITER LLM call sites: extraction and prior sampling."""
from __future__ import annotations

import json
import hashlib
import re
from typing import Any, Iterable

from arbiter.agent.aggregation import SEARCH_FIELDS
from arbiter.adapters.claims import validate_claim_rows
from arbiter.claims import Claim

from .gateway import BudgetExceeded, GatewayClient, GatewayError


def _json_content(content: str) -> Any:
    text = content.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        text = match.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise GatewayError("LLM returned invalid JSON") from exc


def retrieval_queries(display_name: str) -> list[str]:
    display_name = display_name.replace("_", " ")
    return [
        display_name,
        f"{display_name} Minecraft crafting recipe",
        f"how to obtain {display_name} Minecraft",
    ]


CACHE_FALLBACK_PROMPT = """Extract explicit factual claims independently from each
supplied text chunk. Return JSON only as
{"chunks":[{"source_key":"...","claims":[...]}]}. Return exactly one chunk object
for every source_key, including claims=[] when no supported claim is present.

Each claim must contain exactly: claim_type, subject_item, payload, polarity, tense,
span_text. Supported claim_type values are recipe, smelting, drop, craftability, name,
and id. subject_item must be a canonical lowercase snake_case item or block ID. payload
must contain only the structured content explicitly stated in the span. For recipe and
smelting claims use ingredients (with repetitions), output_count, station, and layout
(nine row-major IDs/null for an explicit shaped recipe, otherwise null). For drop use
drops and silk_touch. For name or id use the explicit old/new name or identifier fields.

span_text must be one exact contiguous substring copied from that chunk, without
paraphrase. Use polarity positive/negative and tense current/historical/future. Extract
only explicit claims; do not use outside knowledge, infer from absence, infer inverse
recipes, or combine facts across chunks. Treat each chunk in isolation. No markdown."""
CACHE_EXTRACTOR_VERSION = "sha256:" + hashlib.sha256(
    CACHE_FALLBACK_PROMPT.encode()
).hexdigest()


class CachedClaimExtractor:
    """Deterministic per-chunk fallback matching the public cache row contract."""

    def __init__(self, client: GatewayClient):
        self.client = client

    def extract(self, hit: dict[str, Any]) -> list[dict[str, Any]]:
        if set(hit) != SEARCH_FIELDS:
            raise ValueError("claim fallback accepts only six-field search rows")
        source_key = str(hit["chunk_id"])
        response = self.client.chat(
            "claims_cache_fallback",
            [
                {"role": "system", "content": CACHE_FALLBACK_PROMPT},
                {"role": "user", "content": (
                    f"SOURCE_KEY: {source_key}\n<CHUNK>\n{hit['text']}\n</CHUNK>"
                )},
            ],
            temperature=0.0,
            max_tokens=1800,
        )
        parsed = _json_content(response["content"])
        chunks = parsed.get("chunks") if isinstance(parsed, dict) else None
        if (not isinstance(chunks, list) or len(chunks) != 1
                or not isinstance(chunks[0], dict)
                or chunks[0].get("source_key") != source_key
                or not isinstance(chunks[0].get("claims"), list)):
            raise GatewayError("claim fallback output violates the per-chunk contract")
        values = chunks[0]["claims"]
        rows = []
        for index, value in enumerate(values):
            expected = {
                "claim_type", "subject_item", "payload", "polarity", "tense",
                "span_text",
            }
            if not isinstance(value, dict) or set(value) != expected:
                raise GatewayError(f"claim fallback row {index} has invalid fields")
            if not isinstance(value["span_text"], str) or value["span_text"] not in hit["text"]:
                raise GatewayError(f"claim fallback row {index} has a non-verbatim span")
            canonical = {
                "chunk_id": hit["chunk_id"], **value,
                "extractor_version": CACHE_EXTRACTOR_VERSION,
            }
            digest = hashlib.sha256(
                json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            rows.append({
                "claim_id": f"claim_{digest[:16]}", **canonical,
                "normalizer_version": None, "parent_claim_id": None,
            })
        return validate_claim_rows(rows, str(hit["chunk_id"]))


class ClaimExtractor:
    def __init__(self, client: GatewayClient):
        self.client = client

    def extract(self, snippets: Iterable[dict[str, Any]], *, synthetic: bool = False) -> list[Claim]:
        material = list(snippets)
        public_search = bool(material) and all(set(row) == SEARCH_FIELDS for row in material)
        source_instruction = (
            "For public search rows, copy chunk_id exactly into source. " if public_search else ""
        )
        response = self.client.chat(
            "claim_extraction",
            [
                {"role": "system", "content": (
                    "Extract only explicit Minecraft mechanism claims. Return JSON array. "
                    "Each object must contain source, timestamp, tense (current/historical/unknown), "
                    "claim_type, subject, payload, polarity (1 or -1). Preserve item IDs exactly. "
                    "Payload must be the complete assertion object stated in the snippet. "
                    + source_instruction
                )},
                {"role": "user", "content": json.dumps(material, sort_keys=True)},
            ],
            temperature=0.0,
            synthetic=synthetic,
        )
        parsed = _json_content(response["content"])
        if not isinstance(parsed, list):
            raise GatewayError("claim extraction output must be a JSON array")
        return [Claim(
            source=str(item["source"]), timestamp=item.get("timestamp"),
            tense=item.get("tense", "unknown"), claim_type=str(item["claim_type"]),
            subject=str(item["subject"]), payload=dict(item["payload"]),
            polarity=int(item.get("polarity", 1)),
        ) for item in parsed]


class PriorSampler:
    def __init__(self, client: GatewayClient, samples: int = 5):
        self.client = client
        self.samples = samples

    def sample(self, assertions: list[dict[str, Any]], *, synthetic: bool = False) -> list[dict[str, bool]]:
        response = self.client.chat(
            "prior_sampling",
            [
                {"role": "system", "content": (
                    f"Return a JSON array of exactly {self.samples} independent samples from model prior "
                    "knowledge. Each sample must be one object mapping every supplied mechanism_id to "
                    "true or false. Do not add keys."
                )},
                {"role": "user", "content": json.dumps(assertions, sort_keys=True)},
            ],
            temperature=0.7,
            synthetic=synthetic,
        )
        parsed = _json_content(response["content"])
        expected = {item["mechanism_id"] for item in assertions}
        if not isinstance(parsed, list) or len(parsed) != self.samples:
            raise GatewayError("prior output must contain exactly J samples")
        outputs = []
        for sample_index, item in enumerate(parsed):
            if not isinstance(item, dict) or set(item) != expected:
                raise GatewayError(f"prior sample {sample_index} has invalid keys")
            outputs.append({key: bool(value) for key, value in item.items()})
        return outputs


class DirectPlanner:
    """Optional one-seed control; construction and tests make no API call."""

    allowed_methods = {"mine", "craft_grid", "smelt", "check_name"}

    def __init__(self, client: GatewayClient, minimum_remaining: float = 30.0):
        self.client = client
        self.minimum_remaining = minimum_remaining

    def plan(self, snippets: Iterable[dict[str, Any]], goal: dict[str, Any]) -> list[dict[str, Any]]:
        material = list(snippets)
        if any(set(row) != SEARCH_FIELDS for row in material):
            raise ValueError("llm_direct accepts only six-field search rows")
        if self.client.remaining_budget() < self.minimum_remaining:
            raise BudgetExceeded("llm_direct requires at least 30 currency units remaining")
        response = self.client.chat(
            "llm_direct",
            [
                {"role": "system", "content": (
                    "Return only a JSON array of semantic actions. Each action has exactly "
                    "method and params. Allowed methods: mine, craft_grid, smelt, check_name."
                )},
                {"role": "user", "content": json.dumps({
                    "goal": goal, "retrieved_evidence": material,
                }, sort_keys=True)},
            ],
            temperature=0.0,
        )
        parsed = _json_content(response["content"])
        if not isinstance(parsed, list):
            raise GatewayError("llm_direct output must be an action array")
        actions = []
        for index, action in enumerate(parsed):
            if (not isinstance(action, dict) or set(action) != {"method", "params"}
                    or action.get("method") not in self.allowed_methods
                    or not isinstance(action.get("params"), dict)):
                raise GatewayError(f"llm_direct action {index} is invalid")
            actions.append({"method": action["method"], "params": dict(action["params"])})
        return actions
