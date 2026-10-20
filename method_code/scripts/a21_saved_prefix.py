"""Select saved GLM decisions for byte-checked deterministic A21 recovery."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_prefix(path: Path) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], dict[str, Any] | None]:
    pairs = []
    pending = None
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get('call_type') != 'external_react':
            continue
        if row.get('record_type') == 'full_prompt':
            if pending is not None:
                raise ValueError('A21_saved_prefix_unpaired_requests')
            pending = row
        elif row.get('record_type') == 'full_response':
            if pending is None or row.get('model') != 'glm-5.2':
                raise ValueError('A21_saved_prefix_identity_or_alignment')
            pairs.append((pending, row))
            pending = None
    if len(pairs) > 40:
        raise ValueError('A21_saved_prefix_exceeds_registered_budget')
    return pairs, pending


def select_prefix(root: Path, stem: str, exclude: Path) -> tuple[Path | None, list[Any], Any]:
    candidates = []
    for path in sorted(root.glob(f'**/prompts/{stem}.jsonl')):
        if path.resolve() == exclude.resolve():
            continue
        pairs, pending = read_prefix(path)
        if pairs:
            candidates.append((len(pairs), str(path), path, pairs, pending))
    if not candidates:
        return None, [], None
    _, _, path, pairs, pending = max(candidates)
    return path, pairs, pending


def matches(prompt: dict[str, Any], call_type: str, messages: list[dict[str, str]],
            parameters: dict[str, Any]) -> bool:
    return (prompt['call_type'] == call_type and prompt['messages'] == messages
            and all(prompt['parameters'].get(key) == parameters.get(key)
                    for key in ('temperature', 'max_tokens', 'json_object', 'thinking')))
