#!/usr/bin/env python3
"""W15 zero-call post-review IV reanalysis.

All inputs are frozen exports.  This script makes no model, network, or
simulator calls and deliberately preserves unavailable cells as not_reported.
"""
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
ARB = ROOT.parent / "arbiter"
OUT = ROOT / "reports/paperA/w15"
SEED = 20261017
DRAWS = 10_000


def read_csv(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(name: str, rows, fields=None):
    rows = list(rows)
    if fields is None:
        fields = list(rows[0]) if rows else []
    with (OUT / name).open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def sha(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def num(value):
    if value in ("", None):
        return None
    return float(value)


def condition_group(condition):
    if condition.startswith("matched_"):
        return "matched"
    return condition


def interval(deltas):
    """Family-cluster bootstrap of the equal-cluster mean contrast."""
    ordered = np.asarray([deltas[k] for k in sorted(deltas)], dtype=float)
    if not len(ordered):
        return None, None, None
    rng = np.random.default_rng(SEED)
    draw = rng.integers(0, len(ordered), (DRAWS, len(ordered)))
    estimates = ordered[draw].mean(axis=1)
    return float(ordered.mean()), float(np.quantile(estimates, .025)), float(np.quantile(estimates, .975))


def loo(rows, method, value, contrast, scope, model=""):
    """Return registered leave-one-family-out estimates for one method."""
    by_family = defaultdict(list)
    for row in rows:
        if row.get("method") == method and num(row.get(value)) is not None:
            by_family[row["family_id"]].append(row)
    family_delta = {}
    for family, rr in by_family.items():
        left = [num(x[value]) for x in rr if x.get("condition") in contrast[0]]
        right = [num(x[value]) for x in rr if x.get("condition") in contrast[1]]
        if left and right:
            family_delta[family] = float(np.mean(left) - np.mean(right))
    output = []
    for excluded in sorted(family_delta):
        retained = {k: v for k, v in family_delta.items() if k != excluded}
        point, low, high = interval(retained)
        output.append(dict(scope=scope, model=model, method=method, metric=value,
                           contrast=contrast[2], excluded_family=excluded,
                           included_families=len(retained), estimate=point,
                           ci95_low=low, ci95_high=high,
                           crosses_zero=int(low <= 0 <= high), draws=DRAWS, seed=SEED,
                           status="reported"))
    if not output:
        output.append(dict(scope=scope, model=model, method=method, metric=value,
                           contrast=contrast[2], excluded_family="", included_families=0,
                           estimate="", ci95_low="", ci95_high="", crosses_zero="",
                           draws=DRAWS, seed=SEED, status="not_reported_no_complete_pairs"))
    return output


def family_table(rows, scope, model=""):
    groups = defaultdict(list)
    for r in rows:
        if num(r.get("SR")) is None:
            continue
        groups[(r.get("family_id", ""), r.get("method", ""))].append(r)
    result = []
    for (family, method), rr in sorted(groups.items()):
        for metric in ("SR", "action_contact"):
            values = [num(r.get(metric)) for r in rr if num(r.get(metric)) is not None]
            result.append(dict(scope=scope, model=model, family_id=family, method=method,
                               metric=metric, numerator=sum(values), denominator=len(values),
                               rate=sum(values) / len(values) if values else "", status="reported"))
    return result


def ext_loo():
    path = ARB / "reports/main/post_review/a17_ext_agents_20261017/per_episode.csv"
    rows = read_csv(path)
    for r in rows:
        r["SR"] = r["SR"]
    methods = ("naive_rag", "direct_llm", "react", "reflexion")
    output = []
    for method in methods:
        output += loo(rows, method, "SR",
                      (("mismatched_new2old", "mismatched_old2new"),
                       ("matched_1.16.5", "matched_1.19", "matched_1.21.11"),
                       "mismatched_minus_matched"), "EXT-v2")
        output += loo(rows, method, "action_contact",
                      (("mismatched_new2old", "mismatched_old2new"),
                       ("matched_1.16.5", "matched_1.19", "matched_1.21.11"),
                       "mismatched_minus_matched"), "EXT-v2")
    return output, family_table(rows, "EXT-v2"), [path]


def cds_source(label, episode_path):
    rows = read_csv(episode_path)
    # Both source formats use episode-level discriminative stale contact.
    for row in rows:
        row["action_contact"] = row.get("action_contact", row.get("discriminative_stale_contact", ""))
        if row["action_contact"] == "":
            row["action_contact"] = row.get("discriminative_stale_contact", "")
        row["SR"] = row.get("success", row.get("SR", ""))
    result = []
    for method in sorted({r["method"] for r in rows}):
        result += loo(rows, method, "action_contact", (("K+S",), ("K+N",), "K+S_minus_K+N"), "CDS", label)
        result += loo(rows, method, "SR", (("empty",), ("K",), "empty_minus_K"), "CDS", label)
    return result, family_table(rows, "CDS", label), rows


def classify_contact(rows, actions, suite, model):
    """Classify positive contact episodes using the registered fixed priority."""
    by_key = defaultdict(list)
    for event in actions:
        # In EXT/CDS action audits `method` is the semantic action method;
        # the agent method is deliberately stored as `episode_method`.
        method = event.get("episode_method", event.get("method", ""))
        if event.get("instance_id"):
            key = ("instance", method, event.get("condition", ""), event["instance_id"])
        else:
            # EXT-v2 records omit seed on action rows while the matching
            # episode retains it; there is only one frozen run per remaining
            # method/condition/world/task key, so seed is not part of this
            # join key.
            key = ("episode", method, event.get("condition", ""),
                   event.get("world", event.get("target_world", "")), event.get("task_id", ""))
        by_key[key].append(event)
    result = []
    for episode in rows:
        method = episode.get("method", "")
        contact = num(episode.get("discriminative_contact", episode.get("total_contact", episode.get("HTR_all", ""))))
        if not contact:
            continue
        instance = episode.get("instance_id", episode.get("cds_instance_id", ""))
        if instance:
            key = ("instance", method, episode.get("condition", ""), instance)
        else:
            key = ("episode", method, episode.get("condition", ""),
                   episode.get("world", episode.get("target_world", "")), episode.get("task_id", ""))
        ee = by_key.get(key, [])
        # `propagation` in the EXT summary predates v3-d and cannot be
        # substituted for it. Prefer an explicit frozen v3-d event label;
        # otherwise use the episode-level lower bound when the source exports
        # one. This intentionally leaves no third-party legacy field as proof.
        explicit_v3d = [e.get("v3d_propagation") for e in ee if e.get("v3d_propagation") not in (None, "")]
        if explicit_v3d:
            propagated = any(str(x).lower() in ("1", "true") for x in explicit_v3d)
        else:
            propagated = str(episode.get("propagation_lower", episode.get("v3d_lower", "0"))).lower() in ("1", "true")
        executed = any(e.get("action_type") == "action" and str(e.get("mismatch", "1")) in ("1", "True", "true") for e in ee)
        if propagated:
            category = "propagated_after_denial"
        elif executed:
            category = "stale_action_executed"
        elif ee:
            category = "abandoned_after_checking"
        else:
            category = "unresolved_no_traceable_event"
        result.append(dict(suite=suite, model=model, method=method, condition=episode.get("condition", ""),
                           world=episode.get("world", episode.get("target_world", "")), task_id=episode.get("task_id", ""),
                           seed=episode.get("seed", ""), contact_positive=1, category=category,
                           traceable_events=len(ee), source="frozen_episode_and_stale_action_exports"))
    return result


def contact_decomposition():
    external_main_methods = {"direct_llm", "react", "reflexion", "adam", "xenon"}
    main_episodes = [r for r in read_csv(ARB / "reports/main/post_review/measurement_v3d/discriminative_contact_v3d_episodes.csv")
                     if r.get("method") in external_main_methods]
    main_actions = read_csv(ARB / "reports/main/post_review/measurement_v3d/propagation_v3d_actions.csv")
    main = classify_contact(main_episodes, main_actions, "main_110", "GLM-5.2")
    ext_llm_methods = {"direct_llm", "react", "reflexion"}
    ext_ep = [r for r in read_csv(ARB / "reports/main/post_review/a17_ext_agents_20261017/per_episode.csv")
              if r.get("method") in ext_llm_methods]
    ext_actions = read_csv(ARB / "reports/main/post_review/a17_ext_agents_20261017/per_stale_action.csv")
    ext = classify_contact(ext_ep, ext_actions, "EXT-v2", "GLM-5.2")
    glm_ep_path = ARB / "reports/main/post_review/cds_agents_20261017/episode_metrics.csv"
    glm_ep = read_csv(glm_ep_path)
    glm_actions = read_csv(ARB / "reports/main/post_review/cds_agents_20261017/stale_action_audit.csv") if (ARB / "reports/main/post_review/cds_agents_20261017/stale_action_audit.csv").exists() else []
    glm = classify_contact(glm_ep, glm_actions, "CDS", "GLM-5.2")
    q_ep = read_csv(ARB / "reports/main/post_review/a18_qwen_cds_20261017/remote_service/episode_metrics.csv")
    q_actions = read_csv(ARB / "reports/main/post_review/a18_qwen_cds_20261017/remote_service/stale_actions.csv")
    qwen = classify_contact(q_ep, q_actions, "CDS", "Qwen")
    rows = main + ext + glm + qwen
    summary = []
    group = defaultdict(list)
    for row in rows:
        group[(row["suite"], row["model"], row["method"])].append(row)
    for (suite, model, method), rr in sorted(group.items()):
        total = len(rr)
        for category in ("abandoned_after_checking", "stale_action_executed", "propagated_after_denial", "unresolved_no_traceable_event"):
            n = sum(x["category"] == category for x in rr)
            summary.append(dict(suite=suite, model=model, method=method, category=category,
                                numerator=n, denominator=total, proportion=n / total if total else ""))
    # Existing 78-query ledger is an external consistency reference, never rewritten.
    ledger = ARB / "reports/main/post_review/a9_cds_followup/query_followup_counts.csv"
    return rows, summary, [ledger]


def failure_attribution():
    census = read_csv(ROOT / "reports/paperA/w13_vbrag/diagnostics/retrieval_203_interpreted.csv")
    callbacks = read_csv(ROOT / "reports/paperA/w14/research_callbacks.csv")
    upper_episodes = [json.loads(line) for line in (ROOT / "reports/paperA/w14/cp_rag_recall_upper/episodes.jsonl").read_text().splitlines()]
    frozen_metrics = read_csv(ROOT / "reports/paperA/w14/episode_metrics.csv")
    frozen_cp = {(r["reading_mode"], r["condition"], r["world"], r["task_id"]): r
                 for r in frozen_metrics if r.get("method") == "cp_rag"}
    term = {(r["reading_mode"], r["condition"], r["world"], r["task_id"]): r for r in upper_episodes}
    callback = defaultdict(list)
    for r in callbacks:
        callback[(r["reading_mode"], r["condition"], r["world"], r["task_id"], r["query"])].append(r)
    output = []
    for r in census:
        key = (r["reading_mode"], r["condition"], r["world"], r["task_id"], r["query"])
        upper = term.get(key[:4], {})
        frozen = frozen_cp.get(key[:4], {})
        cb = callback.get(key, [])
        category = "unresolved"
        reason = "no_registered_rule_satisfied"
        if (r.get("category") == "no_target_world_positive_counterpart" or
                r.get("scan_category") == "corpus_absent_confirmed"):
            category, reason = "initial_retrieval_coverage", "no_positive_target_counterpart"
        elif r.get("compiler_after_full_recall") in ("False", "0", "false") and r.get("scan_category") in ("exists_bm25_missed", "exists_retrieved_uncompilable"):
            category, reason = "candidate_compilation", "counterpart_not_compilable_after_full_recall"
        elif sum(int(x.get("new_candidates") or 0) for x in cb) > 0 and frozen.get("SR") == "0":
            category, reason = "mechanism_inference", "new_compilable_callback_without_success"
        output.append(dict(reading_mode=r["reading_mode"], condition=r["condition"], world=r["world"],
                           task_id=r["task_id"], query=r["query"], census_category=r.get("category", ""),
                           callbacks=len(cb), new_candidates=sum(int(x.get("new_candidates") or 0) for x in cb),
                           frozen_CP_SR=frozen.get("SR", ""), upper_SR=upper.get("metrics", {}).get("SR", ""),
                           attribution=category, reason=reason))
    # The registered source contains 203 re-search census rows. The named 113
    # Naive pre-action abstentions are kept as an input coverage accounting row;
    # no fabricated query-level match is possible where a frozen key is absent.
    output.append(dict(reading_mode="all", condition="all", world="all", task_id="", query="",
                       census_category="Naive_pre_action_abstentions", callbacks="", new_candidates="",
                       frozen_CP_SR="", upper_SR="", attribution="unresolved", reason="113_episode_summary_without_query_key"))
    return output


def loo_summary(rows):
    groups = defaultdict(list)
    for r in rows:
        if r.get("status") == "reported":
            groups[(r["scope"], r["model"], r["method"], r["metric"], r["contrast"])].append(r)
    output = []
    for key, rr in sorted(groups.items()):
        worst = min(rr, key=lambda r: (abs(float(r["estimate"])), r["excluded_family"]))
        output.append(dict(scope=key[0], model=key[1], method=key[2], metric=key[3], contrast=key[4],
                           exclusions=len(rr), loo_estimate_min=min(float(r["estimate"]) for r in rr),
                           loo_estimate_max=max(float(r["estimate"]) for r in rr),
                           worst_excluded_family=worst["excluded_family"],
                           worst_estimate=worst["estimate"], worst_ci95_low=worst["ci95_low"],
                           worst_ci95_high=worst["ci95_high"],
                           any_interval_crosses_zero=int(any(r["crosses_zero"] == 1 for r in rr)),
                           crossing_exclusions=";".join(r["excluded_family"] for r in rr if r["crosses_zero"] == 1)))
    return output


def query_crosscheck():
    """Preserve the pre-existing 78-query classification without re-labelling it."""
    rows = read_csv(ARB / "reports/main/post_review/a9_cds_followup/query_followup_counts.csv")
    # This is an event ledger, not the positive-contact-episode denominator in
    # contact_decomposition.csv. The supplied 67/10/1 is reported verbatim.
    return [dict(source="a9_cds_followup/query_followup_counts.csv", unit="query follow-up events",
                 registered_category="abandoned_after_checking", numerator=67, denominator=78,
                 status="legacy_author_supplied_67_10_1"),
            dict(source="a9_cds_followup/query_followup_counts.csv", unit="query follow-up events",
                 registered_category="stale_action_executed", numerator=10, denominator=78,
                 status="legacy_author_supplied_67_10_1"),
            dict(source="a9_cds_followup/query_followup_counts.csv", unit="query follow-up events",
                 registered_category="propagated_after_denial", numerator=1, denominator=78,
                 status="legacy_author_supplied_67_10_1"),
            dict(source="a9_cds_followup/query_followup_counts.csv", unit="comparison", registered_category="direct_equality_test",
                 numerator="", denominator="", status="not_comparable_event_ledger_vs_episode_level_v3d")]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    inputs = [
        ARB / "reports/main/post_review/a17_ext_agents_20261017/per_episode.csv",
        ARB / "reports/main/post_review/a17_ext_agents_20261017/per_stale_action.csv",
        ARB / "reports/main/post_review/cds_agents_20261017/episode_metrics.csv",
        ARB / "reports/main/post_review/cds_agents_20261017/stale_action_audit.csv",
        ARB / "reports/main/post_review/a18_qwen_cds_20261017/remote_service/episode_metrics.csv",
        ARB / "reports/main/post_review/a18_qwen_cds_20261017/remote_service/stale_actions.csv",
        ARB / "reports/main/post_review/measurement_v3d/discriminative_contact_v3d_episodes.csv",
        ARB / "reports/main/post_review/measurement_v3d/propagation_v3d_actions.csv",
        ROOT / "reports/paperA/w13_vbrag/diagnostics/retrieval_203_interpreted.csv",
        ROOT / "reports/paperA/w14/research_callbacks.csv",
        ROOT / "reports/paperA/w14/cp_rag_recall_upper/episodes.jsonl",
        ARB / "reports/main/post_review/a9_cds_followup/query_followup_counts.csv",
        ROOT / "prereg/w15_post_review_iv_20261017.md",
        ROOT / "scripts/w15_reanalysis.py",
    ]
    loo_rows, ext_families, ext_inputs = ext_loo()
    glm_path = ARB / "reports/main/post_review/cds_agents_20261017/episode_metrics.csv"
    qwen_path = ARB / "reports/main/post_review/a18_qwen_cds_20261017/remote_service/episode_metrics.csv"
    for label, path in (("GLM-5.2", glm_path), ("Qwen", qwen_path)):
        rows, families, _ = cds_source(label, path)
        loo_rows.extend(rows)
        ext_families.extend(families)
    contact_rows, contact_summary, cross_inputs = contact_decomposition()
    failures = failure_attribution()
    loo_rollup = loo_summary(loo_rows)
    write_csv("loo_results.csv", loo_rows)
    write_csv("loo_summary.csv", loo_rollup)
    write_csv("contact_decomposition.csv", contact_rows)
    write_csv("contact_decomposition_summary.csv", contact_summary)
    write_csv("contact_decomposition_crosscheck_78.csv", query_crosscheck())
    write_csv("failure_attribution.csv", failures)
    write_csv("family_descriptive.csv", ext_families)
    write_csv("input_hashes.csv", [dict(path=str(p.relative_to(ROOT.parent)), sha256=sha(p), bytes=p.stat().st_size)
                                    for p in inputs if p.exists()])
    crossings = [r for r in loo_rows if r.get("crosses_zero") == 1]
    fail_counts = Counter(x["attribution"] for x in failures)
    report = ["# W15 zero-call reanalysis", "", "Preregistration: `f0c6e24`.",
              "All results were reconstructed from frozen exports; no model, network, or simulator calls were made.", "",
              "## Leave-one-family-out", "",
              f"Reported LOO rows: {sum(r['status'] == 'reported' for r in loo_rows)}.",
              f"LOO rows whose registered interval includes zero: {len(crossings)}.",
              "Each such row is retained in `loo_results.csv`; absence of a full condition pair is `not_reported`.", "",
              "## Contact consequences", "",
              "The three registered categories use propagation > stale action executed > abandonment priority.",
              "Rows lacking a traceable stale-event record remain unresolved rather than being forced into a category.",
              "The existing 78-query classification is 67 abandoned / 10 executed / 1 propagated.",
              "It is a query-follow-up event ledger, whereas this decomposition is episode-level v3-d; the two units are not directly equal.", "",
              "## Failure attribution", "",
              "Counts from reproducible query-level records: " + ", ".join(f"{k}={v}" for k, v in sorted(fail_counts.items())) + ".",
              "The 113 Naive pre-action abstentions lack query-level keys in the registered sources and are shown as unresolved accounting, not assigned to a causal category.", "",
              "## Descriptive family tables", "",
              "`family_descriptive.csv` contains numerator, denominator and rate only. It makes no family-level comparison claim."]
    (OUT / "REPORT.md").write_text("\n".join(report) + "\n")
    digest_rows = []
    for p in sorted(OUT.iterdir()):
        if p.is_file() and p.name != "OUTPUTS.sha256.csv":
            digest_rows.append(dict(path=p.name, sha256=sha(p), bytes=p.stat().st_size))
    write_csv("OUTPUTS.sha256.csv", digest_rows)


if __name__ == "__main__":
    main()
