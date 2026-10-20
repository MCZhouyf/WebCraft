"""Small on-disk BM25 backend implementing the frozen corpus search contract."""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path


RETURN_KEYS = {"text", "source_site", "page_title", "revid", "timestamp", "chunk_id"}
ALLOWED_CONDITIONS = {
    "matched",
    "matched_1.16.5",
    "matched_1.19",
    "matched_1.21.11",
    "mismatched_old2new",
    "mismatched_new2old",
    "mixed",
    "polluted",
}


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_]+", text.casefold())


class SearchBackend:
    def __init__(self, condition: str, index_dir: str):
        if condition not in ALLOWED_CONDITIONS:
            raise ValueError(f"unsupported corpus condition: {condition}")
        root = Path(index_dir)
        config = json.loads((root / "config.json").read_text())
        if condition not in config["accepted_conditions"]:
            raise ValueError("condition does not match index")
        self.documents = [
            json.loads(line) for line in (root / "documents.jsonl").read_text().splitlines()
        ]
        statistics = json.loads((root / "bm25.json").read_text())
        self.df = statistics["document_frequency"]
        self.avgdl = statistics["average_document_length"]
        self.term_frequencies = [Counter(tokenize(row["text"])) for row in self.documents]
        self.lengths = [sum(counter.values()) for counter in self.term_frequencies]

    def search(self, query: str, k: int) -> list[dict]:
        if k <= 0:
            return []
        terms = tokenize(query)
        if not terms or not self.documents:
            return []
        count = len(self.documents)
        scores = []
        for index, frequencies in enumerate(self.term_frequencies):
            score = 0.0
            for term in terms:
                frequency = frequencies.get(term, 0)
                if not frequency:
                    continue
                df = self.df.get(term, 0)
                inverse = math.log(1.0 + (count - df + 0.5) / (df + 0.5))
                denominator = frequency + 1.5 * (
                    1 - 0.75 + 0.75 * self.lengths[index] / self.avgdl
                )
                score += inverse * frequency * 2.5 / denominator
            if score > 0:
                scores.append((score, self.documents[index]["chunk_id"], index))
        scores.sort(key=lambda row: (-row[0], row[1]))
        return [
            {key: self.documents[index][key] for key in RETURN_KEYS}
            for _, _, index in scores[:k]
        ]
