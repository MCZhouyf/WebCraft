"""Source-bearing claims used by context inference."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


Tense = Literal["current", "historical", "unknown"]
Polarity = Literal[-1, 1]


@dataclass(frozen=True)
class Claim:
    source: str
    timestamp: str | None
    tense: Tense
    claim_type: str
    subject: str
    payload: dict[str, Any]
    polarity: Polarity = 1

    def __post_init__(self) -> None:
        if not self.source:
            raise ValueError("source is required")
        if self.polarity not in (-1, 1):
            raise ValueError("polarity must be -1 or 1")

