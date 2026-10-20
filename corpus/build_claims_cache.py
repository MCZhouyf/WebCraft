"""Build cross-index deduplicated LLM claim caches for frozen BM25 chunks."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

from openai import APITimeoutError, RateLimitError

from corpus.claims_cache import CLAIM_KEYS, FORBIDDEN_KEYS, _has_forbidden_key
from corpus.extract_claims import extract_chunk_claims
from corpus.llm_client import BudgetStop, BudgetedLLMClient, STATE_PATH
from corpus.block_templates import is_template_chunk, public_template_claims


ROOT = Path(__file__).resolve().parents[1]
PRIVATE_PROGRESS = ROOT / "corpus/private/claims_cache_progress.jsonl"
PRIVATE_META = ROOT / "corpus/private/claims_cache_progress_meta.json"
PREFILTER = re.compile(
    r"\b(?:crafting|craft|recipe|smelt|furnace|drop|obtain|mined|id|renamed|formerly)\b",
    re.I,
)


def passes_prefilter(text: str) -> bool:
    """Keep mechanism-bearing prose, excluding history-only chronology chunks."""
    if not PREFILTER.search(text):
        return False
    lower = text.lower()
    first_history = lower.find("historical claim:")
    if first_history < 0:
        return True
    before_history = lower[:first_history]
    mechanism_markers = (
        "obtaining.",
        "crafting recipe",
        "smelting recipe",
        " drops ",
        " drop ",
        "primarily used",
    )
    return any(marker in before_history for marker in mechanism_markers)


def extractor_version() -> str:
    return hashlib.sha256((ROOT / "corpus/extract_claims.py").read_bytes()).hexdigest()[:8]


def load_documents(index_root: Path) -> dict[str, list[dict[str, Any]]]:
    return {
        directory.name: [
            json.loads(line)
            for line in (directory / "documents.jsonl").read_text().splitlines()
            if line.strip()
        ]
        for directory in sorted(index_root.iterdir())
        if directory.is_dir() and (directory / "documents.jsonl").is_file()
    }


def chunk_versions(documents: dict[str, list[dict[str, Any]]]) -> dict[str, tuple[str, ...]]:
    memberships: dict[str, set[str]] = {}
    for index_name, rows in documents.items():
        if not index_name.startswith("matched_"):
            continue
        version = index_name.removeprefix("matched_")
        for row in rows:
            memberships.setdefault(row["chunk_id"], set()).add(version)
    return {key: tuple(sorted(value)) for key, value in memberships.items()}


def load_existing(output_root: Path) -> dict[str, dict[str, list[dict[str, Any]]]]:
    existing = {}
    for directory in output_root.iterdir() if output_root.exists() else ():
        path = directory / "chunk_claims.jsonl"
        if not path.is_file():
            continue
        existing[directory.name] = {
            row["chunk_id"]: row["claims"]
            for row in (json.loads(line) for line in path.read_text().splitlines() if line)
        }
    return existing


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def load_progress(version: str) -> dict[str, list[dict[str, Any]]]:
    if not PRIVATE_PROGRESS.exists():
        return {}
    meta = json.loads(PRIVATE_META.read_text()) if PRIVATE_META.exists() else {}
    if meta.get("extractor_version") != version:
        raise RuntimeError("existing claims-cache progress uses another extractor version")
    rows = {}
    for line in PRIVATE_PROGRESS.read_text().splitlines():
        row = json.loads(line)
        rows[row["text_hash"]] = row["claims"]
    return rows


def append_progress(value: dict[str, Any]) -> None:
    PRIVATE_PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    with PRIVATE_PROGRESS.open("a") as handle:
        handle.write(json.dumps(value, sort_keys=True) + "\n")


def public_claim(base: dict[str, Any], chunk_id: str, version: str) -> dict[str, Any]:
    body = {
        "chunk_id": chunk_id,
        "claim_type": base["claim_type"],
        "subject_item": base["subject_item"],
        "payload": base["payload"],
        "polarity": base["polarity"],
        "tense": base["tense"],
        "span_text": base["span_text"],
        "extractor_version": version,
    }
    identity = json.dumps(body, sort_keys=True, separators=(",", ":"))
    claim = {
        "claim_id": "claim_" + hashlib.sha256(identity.encode()).hexdigest()[:20],
        **body,
        "parent_claim_id": None,
        "normalizer_version": None,
    }
    if set(claim) != CLAIM_KEYS or _has_forbidden_key(claim):
        raise ValueError("generated claim violates public cache contract")
    return claim


def state_spent() -> Decimal:
    if not STATE_PATH.exists():
        return Decimal("0")
    return Decimal(json.loads(STATE_PATH.read_text())["spent"])


def extract_complete_batch(
    batch: list[tuple[str, str]],
    client: BudgetedLLMClient,
    *,
    purpose: str = "claims_cache_chunk_extraction",
    retry_single: bool = True,
    max_tokens: int = 8000,
    transient_retries: int = 2,
) -> dict[str, list[dict[str, Any]]]:
    """Recover malformed or incomplete batch responses by splitting missing keys."""
    try:
        extracted = extract_chunk_claims(
            batch, client, purpose=purpose, max_tokens=max_tokens
        )
    except (APITimeoutError, RateLimitError) as exc:
        if transient_retries <= 0:
            raise
        print(json.dumps({
            "event": "transient_retry",
            "error": type(exc).__name__,
            "remaining": transient_retries,
            "batch_size": len(batch),
        }), flush=True)
        return extract_complete_batch(
            batch,
            client,
            purpose="claims_cache_chunk_extraction_transient_retry",
            retry_single=retry_single,
            max_tokens=max_tokens,
            transient_retries=transient_retries - 1,
        )
    except (json.JSONDecodeError, TypeError):
        extracted = {}
        print(
            json.dumps({"event": "malformed_json", "batch_size": len(batch)}),
            flush=True,
        )

    missing = [(key, text) for key, text in batch if key not in extracted]
    if not missing:
        return extracted
    if len(missing) == 1:
        if not retry_single:
            raise RuntimeError("model twice omitted or malformed one chunk result")
        extracted.update(
            extract_complete_batch(
                missing,
                client,
                purpose="claims_cache_chunk_extraction_retry",
                retry_single=False,
                max_tokens=max_tokens,
                transient_retries=transient_retries,
            )
        )
        return extracted

    midpoint = len(missing) // 2
    print(
        json.dumps(
            {
                "event": "split_retry",
                "missing": len(missing),
                "parts": [midpoint, len(missing) - midpoint],
            }
        ),
        flush=True,
    )
    for part in (missing[:midpoint], missing[midpoint:]):
        extracted.update(
            extract_complete_batch(
                part,
                client,
                purpose="claims_cache_chunk_extraction_retry",
                max_tokens=max_tokens,
                transient_retries=transient_retries,
            )
        )
    return extracted


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=15)
    parser.add_argument("--budget", type=Decimal, default=Decimal("40"))
    parser.add_argument("--task-budget", type=Decimal)
    parser.add_argument("--max-tokens", type=int, default=8000)
    parser.add_argument("--prefilter", action="store_true")
    parser.add_argument("--skip-on-task-budget", action="store_true")
    args = parser.parse_args()

    version = extractor_version()
    documents = load_documents(args.index_root)
    memberships = chunk_versions(documents)
    existing = load_existing(args.output_root)
    unique = {}
    for index_name, rows in documents.items():
        for row in rows:
            if (
                not is_template_chunk(row["text"])
                and row["chunk_id"] not in existing.get(index_name, {})
            ):
                unique.setdefault(text_hash(row["text"]), row["text"])
    progress = load_progress(version)
    start_spent = state_spent()
    if PRIVATE_META.exists():
        meta = json.loads(PRIVATE_META.read_text())
        start_spent = Decimal(meta["start_spent"])
    else:
        PRIVATE_META.write_text(
            json.dumps(
                {"extractor_version": version, "start_spent": str(start_spent)},
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
    task_start_spent = state_spent()

    pending = [(key, unique[key]) for key in sorted(unique) if key not in progress]
    prefilter_skipped = 0
    if args.prefilter:
        selected = []
        for key, value in pending:
            if passes_prefilter(value):
                selected.append((key, value))
                continue
            append_progress(
                {"text_hash": key, "claims": [], "prefilter_skipped": True}
            )
            progress[key] = []
            prefilter_skipped += 1
        pending = selected
    client = BudgetedLLMClient()
    budget_skipped = 0
    try:
        for offset in range(0, len(pending), args.batch_size):
            if state_spent() - start_spent >= args.budget:
                raise BudgetStop("claims-cache budget reached")
            if args.task_budget is not None and state_spent() - task_start_spent >= args.task_budget:
                raise BudgetStop("claims-cache task budget reached")
            batch = pending[offset : offset + args.batch_size]
            extracted = extract_complete_batch(
                batch, client, max_tokens=args.max_tokens
            )
            for key, _ in batch:
                claims = extracted[key]
                append_progress({"text_hash": key, "claims": claims})
                progress[key] = claims
            print(
                json.dumps(
                    {
                        "completed_unique": len(progress),
                        "total_unique": len(unique),
                        "spent": str(state_spent() - start_spent),
                    }
                ),
                flush=True,
            )
    except BudgetStop:
        if not args.skip_on_task_budget:
            raise
        for key, _ in pending:
            if key in progress:
                continue
            append_progress(
                {"text_hash": key, "claims": [], "budget_skipped": True}
            )
            progress[key] = []
            budget_skipped += 1
        print(
            json.dumps({"event": "task_budget_stop", "budget_skipped": budget_skipped}),
            flush=True,
        )
    finally:
        client.close()

    summary = {}
    for index_name, rows in documents.items():
        output = args.output_root / index_name / "chunk_claims.jsonl"
        output.parent.mkdir(parents=True, exist_ok=True)
        claim_count = 0
        with output.open("w") as handle:
            for row in rows:
                if is_template_chunk(row["text"]):
                    claims = public_template_claims(
                        row["text"], row["page_title"], row["chunk_id"],
                        memberships.get(row["chunk_id"], ("1.21.11",)),
                    )
                elif row["chunk_id"] in existing.get(index_name, {}):
                    claims = existing[index_name][row["chunk_id"]]
                else:
                    claims = [public_claim(base, row["chunk_id"], version)
                              for base in progress[text_hash(row["text"])]]
                claim_count += len(claims)
                handle.write(
                    json.dumps(
                        {"chunk_id": row["chunk_id"], "claims": claims},
                        sort_keys=True,
                    )
                    + "\n"
                )
        summary[index_name] = {"chunks": len(rows), "claims": claim_count}
    print(
        json.dumps(
            {
                "extractor_version": version,
                "unique_chunks": len(unique),
                "incremental_cost": str(state_spent() - start_spent),
                "task_cost": str(state_spent() - task_start_spent),
                "prefilter_skipped": prefilter_skipped,
                "budget_skipped": budget_skipped,
                "indexes": summary,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
