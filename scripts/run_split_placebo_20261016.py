#!/usr/bin/env python3
"""Run the two preregistered W7b placebo components separately."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ARB = Path("/root/autodl-tmp/arbiter")
sys.path[:0] = [str(ROOT), str(ROOT / "reports/paperA/placebo_invariance"),
                str(ARB / "src"), str(ARB / "scripts")]

from arbiter.evaluation.main_matrix import MAIN_CELLS  # noqa: E402
from arbiter.evaluation.p6_matrix import P6Job  # noqa: E402
from arbiter.evaluation.post_review_measurement import terminal  # noqa: E402
from arbiter.pinning import build_pin, verify_webcraft_pin  # noqa: E402
from corpus.search_backend import RETURN_KEYS, tokenize  # noqa: E402
import arbiter.adapters.claims as claims_adapter  # noqa: E402
import arbiter.adapters.search as search_adapter  # noqa: E402
import arbiter.adapters.webcraft as webcraft_adapter  # noqa: E402
import arbiter.evaluation.p6_execution as p6  # noqa: E402
import run_main  # type: ignore  # noqa: E402


OUT = ROOT / "reports/paperA/review_v14_zero_call/split_placebo"
INDEX = ROOT / "corpus/indexes/formal_p1"
SEED = 20261016
SOURCE_SEED = 20261006
METHODS = (
    "parametric", "probe_then_parametric", "naive_rag", "majority_rag",
    "truthfinder_rag", "verify_all", "arbiter_shell_naive", "arbiter",
    "oracle", "probe_then_rag",
)
ORIGINAL_LOADER = p6.load_search_backend


def jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)


def alias(site: str) -> str:
    return "w7b-" + hashlib.sha256(f"{SEED}:{site}".encode()).hexdigest()[:16] + ".invalid"


class MetadataBackend:
    def __init__(self, condition: str, index_dir: str, **kwargs: Any):
        self.base = ORIGINAL_LOADER(condition, index_dir, **kwargs)

    def search(self, query: str, k: int) -> list[dict[str, Any]]:
        rows = self.base.search(query, k)
        return [{**row, "source_site": alias(str(row["source_site"])),
                 "text": str(row["text"]) + "\nW7B_DECORATIVE_TEXT"} for row in rows]


class CandidateBackend:
    def __init__(self, condition: str, index_dir: str, **kwargs: Any):
        root = Path(index_dir)
        config = json.loads((root / "config.json").read_text())
        if condition not in config["accepted_conditions"]:
            raise ValueError("condition/index mismatch")
        self.documents = jsonl(root / "documents.jsonl")
        stats = json.loads((root / "bm25.json").read_text())
        self.df, self.avgdl = stats["document_frequency"], stats["average_document_length"]
        self.tf = [Counter(tokenize(row["text"])) for row in self.documents]
        self.lengths = [sum(counter.values()) for counter in self.tf]

    def search(self, query: str, k: int) -> list[dict[str, Any]]:
        terms, count, scores = tokenize(query), len(self.documents), []
        for index, frequencies in enumerate(self.tf):
            score = 0.0
            for term in terms:
                frequency = frequencies.get(term, 0)
                if not frequency: continue
                df = self.df.get(term, 0)
                inverse = math.log(1.0 + (count - df + .5) / (df + .5))
                denominator = frequency + 1.5 * (1 - .75 + .75 * self.lengths[index] / self.avgdl)
                score += inverse * frequency * 2.5 / denominator
            if score > 0:
                scores.append((score, str(self.documents[index]["chunk_id"]), index))
        # The frozen backend uses chunk_id ascending within an exact-score tie.
        # This placebo changes only that tie break by reversing the canonical
        # order; scores and every document field remain untouched.
        scores.sort(key=lambda value: value[1], reverse=True)
        scores.sort(key=lambda value: -value[0])
        return [{key: self.documents[index][key] for key in RETURN_KEYS}
                for _, _, index in scores[:k]]


def key(row: dict[str, Any]) -> tuple[Any, ...]:
    return row["method"], row["condition"], row["world"], row["task_id"]


def query_chunks(row: dict[str, Any]) -> list[list[str]]:
    return [[str(chunk) for chunk in event.get("chunk_ids", [])]
            for event in row.get("reader", {}).get("search_events", [])]


def setup(label: str = "combined") -> tuple[Path, dict[str, Any]]:
    OUT.mkdir(parents=True, exist_ok=True)
    pin = OUT / f"webcraft_pin_{label}.json"
    pin.write_text(json.dumps(build_pin(ROOT), indent=2, sort_keys=True) + "\n")
    state = verify_webcraft_pin(pin, ROOT, require_ready=True)
    def cached(*args: Any, **kwargs: Any) -> dict[str, Any]: return state
    p6.verify_webcraft_pin = cached
    claims_adapter.verify_webcraft_pin = cached
    search_adapter.verify_webcraft_pin = cached
    webcraft_adapter.verify_webcraft_pin = cached
    class ForbiddenGateway:
        def __init__(self, *args: Any, **kwargs: Any): raise RuntimeError("placebo is zero-call")
    p6.GatewayClient = ForbiddenGateway
    prior, _ = run_main.load_prior_bundle(ARB / "artifacts/prior/frozen_prior_bundle.json")
    return pin, prior


def run_mode(mode: str, pin: Path, prior: dict[str, Any], temporary: Path,
             methods: tuple[str, ...] = METHODS,
             cells: tuple[tuple[str, str], ...] = tuple(MAIN_CELLS)) -> list[dict[str, Any]]:
    p6.load_search_backend = MetadataBackend if mode == "metadata" else CandidateBackend
    tasks = run_main.load_evaluation_tasks(ROOT / "data/tasks.jsonl", ROOT / "config/resource_model.json")
    jobs = [P6Job(condition, task["id"], world, method, SEED)
            for condition, world in cells for task in tasks for method in methods]
    output = p6.run_matrix(jobs, tasks, root=ROOT, pin_path=pin, index_dir=str(INDEX),
                           output_dir=temporary / mode, top_k=10,
                           reading_mode="all_public", prior_bundle=prior)
    return jsonl(output)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def run_single(mode: str, methods: tuple[str, ...] = METHODS, tag: str = "all",
               cells: tuple[tuple[str, str], ...] = tuple(MAIN_CELLS)) -> None:
    unknown = set(methods) - set(METHODS)
    if unknown:
        raise ValueError(f"unknown methods: {sorted(unknown)}")
    pin, prior = setup(f"{mode}_{tag}")
    with tempfile.TemporaryDirectory(prefix=f"w7b_placebo_{mode}_{tag}_", dir="/root/autodl-tmp") as directory:
        result = run_mode(mode, pin, prior, Path(directory), methods, cells)
    expected = 34 * len(cells) * len(methods)
    if len(result) != expected:
        raise RuntimeError(f"{mode} matrix has {len(result)} episodes")
    write_jsonl(OUT / f"episodes_{mode}_{tag}.jsonl", result)


def aggregate() -> None:
    baseline = jsonl(ARB / "artifacts/main/all_public/episodes.jsonl")
    baseline += jsonl(ARB / "artifacts/post_run_probe_then_rag/all_public/episodes.jsonl")
    base = {key(row): row for row in baseline if int(row["seed"]) == SOURCE_SEED}
    details, summary = [], []
    for mode in ("metadata", "candidate_order"):
        partials = sorted(OUT.glob(f"episodes_{mode}_*.jsonl"))
        if not partials:
            raise RuntimeError(f"no {mode} worker outputs")
        raw = [row for path in partials for row in jsonl(path)]
        test = {key(row): row for row in raw}
        if set(test) != set(base) or len(test) != 3740:
            raise RuntimeError(f"{mode} matrix mismatch")
        by_method: dict[str, list[dict[str, int]]] = defaultdict(list)
        for episode_key in sorted(base):
            left, right = base[episode_key], test[episode_key]
            left_chunks, right_chunks = query_chunks(left), query_chunks(right)
            values = {
                "retrieval_sequence_changed": int(left_chunks != right_chunks),
                "topk_content_changed": int(
                    len(left_chunks) != len(right_chunks)
                    or any(set(a) != set(b) for a, b in zip(left_chunks, right_chunks))
                ),
                "terminal_changed": int(terminal(left) != terminal(right)),
                "plan_acceptance_changed": int(left["metrics"]["HTR_commit"] != right["metrics"]["HTR_commit"]),
            }
            details.append({
                "placebo": mode, "method": left["method"], "condition": left["condition"],
                "world": left["world"], "task_id": left["task_id"],
                "baseline_terminal": terminal(left), "placebo_terminal": terminal(right), **values,
            })
            by_method[left["method"]].append(values)
        for method, values in sorted(by_method.items()):
            summary.append({"placebo": mode, "method": method, "episodes": len(values), **{
                name: sum(row[name] for row in values) for name in values[0]
            }})
    write_csv(OUT / "instance_diffs.csv", details)
    write_csv(OUT / "summary.csv", summary)
    totals = []
    for mode in ("metadata", "candidate_order"):
        selected = [row for row in details if row["placebo"] == mode]
        totals.append({"placebo": mode, "episodes": len(selected), **{
            name: sum(row[name] for row in selected)
            for name in ("retrieval_sequence_changed", "topk_content_changed",
                         "terminal_changed", "plan_acceptance_changed")
        }})
    write_csv(OUT / "totals.csv", totals)
    (OUT / "REPORT.md").write_text(
        "# Split placebo\n\nZero LLM calls; one deterministic seed over 3,740 episodes per intervention.\n\n"
        "The source-site perturbation is a bijection over the two frozen source groups, so source "
        "partitioning and source priors are unchanged.\n\n" +
        "\n".join(f"- {row['placebo']}: terminal changes {row['terminal_changed']}; "
                  f"top-k changes {row['topk_content_changed']}." for row in totals) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("metadata", "candidate_order", "aggregate", "all"),
                        nargs="?", default="all")
    parser.add_argument("--methods", default=",")
    parser.add_argument("--tag", default="all")
    parser.add_argument("--cell-indices", default=",")
    args = parser.parse_args()
    methods = METHODS if args.methods == "," else tuple(args.methods.split(","))
    indices = range(len(MAIN_CELLS)) if args.cell_indices == "," else (
        int(value) for value in args.cell_indices.split(",")
    )
    cells = tuple(MAIN_CELLS[index] for index in indices)
    if args.phase == "all":
        run_single("metadata", methods, args.tag, cells)
        run_single("candidate_order", methods, args.tag, cells)
        aggregate()
    elif args.phase == "aggregate":
        aggregate()
    else:
        run_single(args.phase, methods, args.tag, cells)


if __name__ == "__main__": main()
