from __future__ import annotations

import json
from pathlib import Path

from arbiter.inference import ContextMixtureModel, SourceEvidence
from arbiter.llm import ClaimExtractor, GatewayClient, PriorSampler
from arbiter.normalization import mechanism_id


def _gold(root: Path) -> tuple[list[dict], list[dict]]:
    records = [json.loads(line) for line in (root / "data/conflicts.jsonl").read_text().splitlines()]
    selected = [record for record in records if record["assertion"]["kind"] in {"drop", "name", "craftability"}][:20]
    snippets, gold = [], []
    for index, record in enumerate(selected):
        assertion = record["assertion"]
        kind = assertion["kind"]
        subject = assertion.get("block") or assertion.get("item")
        snippets.append({
            "source": f"synthetic-gold-{index}", "timestamp": "2026-01-01",
            "synthetic": True,
            "text": (
                f"Current mechanism assertion for {subject}: "
                f"{json.dumps(assertion, sort_keys=True)}"
            ),
        })
        gold.append({"source": f"synthetic-gold-{index}", "subject": subject,
                     "claim_type": kind, "payload": assertion})
    return snippets, gold


def run(root: Path, checkpoint_dir: Path = Path("artifacts/p5")) -> dict:
    client = GatewayClient()
    snippets, gold = _gold(root)
    extracted = ClaimExtractor(client).extract(snippets, synthetic=True)
    gold_keys = {(item["source"], mechanism_id(item["payload"])) for item in gold}
    predicted_keys = {(item.source, mechanism_id(item.payload)) for item in extracted}
    correct = len(gold_keys & predicted_keys)
    precision = correct / len(predicted_keys) if predicted_keys else 0.0
    recall = correct / len(gold_keys)
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    (checkpoint_dir / "extraction_checkpoint.json").write_text(json.dumps({
        "synthetic": True,
        "reportable_scientific_result": False,
        "gold_claims": len(gold_keys), "predicted_claims": len(predicted_keys),
        "correct_claims": correct, "precision": precision, "recall": recall,
    }, indent=2, sort_keys=True) + "\n")
    assertions = [{"mechanism_id": mechanism_id(item["payload"]), "assertion": item["payload"]}
                  for item in gold[:4]]
    priors = PriorSampler(client, samples=5).sample(assertions, synthetic=True)
    source_rows = [SourceEvidence(
        source=f"llm-prior-{index}", timestamp=None,
        statements={key: int(value) for key, value in sample.items()},
    ) for index, sample in enumerate(priors)]
    absorbed = ContextMixtureModel(max_k=3).fit(source_rows)
    result = {
        "dataset": "synthetic_gold_templates_development_only",
        "reportable_scientific_result": False,
        "gold_claims": len(gold_keys), "predicted_claims": len(predicted_keys),
        "correct_claims": correct, "precision": precision, "recall": recall,
        "prior_samples": len(priors), "em_contexts": absorbed.k_,
        "model": client.model, "ledger": str(client.ledger),
        "spent": client.spent(), "currency": client.currency,
        "passed": precision >= 0.9 and recall >= 0.8 and len(priors) == 5,
    }
    return result
