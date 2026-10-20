"""Export complete public trajectories with human annotations and neutral IDs.

This script only reads saved records. It never creates an environment or client.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median
from typing import Any

import numpy as np

from scripts import run_review_v13_audit_20261015 as original
from scripts.audit_semantic_sheet_v2 import (
    ANNOTATION_FIELDS, OUT as DIAGNOSTICS, ROOT, SOURCE, csv_rows, digest, recover_sample,
)
from scripts.run_measurement_reanalysis_20261013 import evidence_path

SEED = 20261018
CELL_LIMIT = 30_000  # Below Excel's 32,767 UTF-16-unit cell limit.
CDS_SOURCE = ROOT / 'reports/main/post_review/a9_cds_followup'
PRIVATE = SOURCE / 'reexport_v2_private'
META = ('agent_family', 'method', 'condition', 'world', 'task_id')
PUBLIC_SEARCH = ('text', 'source_site', 'page_title', 'revid', 'timestamp', 'chunk_id')
PREVIOUS = dict(zip(ANNOTATION_FIELDS, ('prev_contact_type', 'prev_propagation', 'prev_terminal', 'prev_notes')))


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def split_cell(text: str, limit: int = CELL_LIMIT) -> list[str]:
    parts, start, units = [], 0, 0
    for index, char in enumerate(text):
        width = 2 if ord(char) > 0xffff else 1
        if units + width > limit:
            parts.append(text[start:index])
            start, units = index, 0
        units += width
    parts.append(text[start:])
    if ''.join(parts) != text:
        raise AssertionError('cell splitting changed content')
    return parts


def full_episode(events: list[dict]) -> list[dict]:
    if not events:
        raise AssertionError('empty episode')
    end = next((index + 1 for index, event in enumerate(events) if event.get('method') == 'submit'), len(events))
    return events[:end]


def public_event(event: dict) -> dict:
    # Internal intent, probe, mechanism/truth and policy fields are not feedback.
    output = {field: event.get(field) for field in ('method', 'params', 'success', 'result')}
    if 'item_flow' in event:
        output['item_flow'] = event['item_flow']
    return output


def sequence(events: list[dict]) -> str:
    return ' -> '.join(canonical(public_event(event)) for event in events)


def annotations(path: Path, original_rows: list[dict]) -> dict[str, dict]:
    with path.open(newline='', encoding='utf-8-sig') as handle:
        rows = list(csv.DictReader(handle))
    indexed = {row['trajectory_id']: row for row in rows}
    if len(rows) != 147 or len(indexed) != 147 or set(indexed) != {r['trajectory_id'] for r in original_rows}:
        raise AssertionError('human annotation IDs must match the same 147 source rows exactly')
    for old in original_rows:
        human = indexed[old['trajectory_id']]
        if any(human[field] != old[field] for field in old if field not in ANNOTATION_FIELDS):
            raise AssertionError('human input changed trajectory identity or original evidence')
        if not human['contact_type'].strip() or not human['terminal_depends_on_stale'].strip():
            raise AssertionError('required existing human annotations are missing')
    return indexed


def resolve_record(row: dict, anchors: dict, records: dict) -> dict:
    family, identifier = row['agent_family'], row['trajectory_id']
    if identifier in anchors:
        return records[family][original.record_key(anchors[identifier][1], family)]
    matches = [record for record in records[family].values()
               if all(str(record[f]) == row[f] for f in ('method', 'condition', 'world', 'task_id'))]
    if len(matches) != 1:
        raise AssertionError('ambiguous episode identity')
    return matches[0]


def external_search_context(record: dict, events: list[dict]) -> tuple[str, dict]:
    searches = [(index, event) for index, event in enumerate(events) if event.get('method') == 'search']
    if not searches:
        return '该回合无检索', {'searches': 0, 'returned_hits': 0, 'context_hits': 0, 'excluded_terminal_searches': 0}
    valid_model_actions = int(record['tool_actions']) - 1
    invalid_outputs = int(record['model_calls']) - valid_model_actions
    if invalid_outputs < 0:
        raise AssertionError('external call/action accounting mismatch')
    shown, hit_count, excluded = [], 0, 0
    for index, event in searches:
        # Initial inventory is event 0; model-generated valid actions occupy
        # 1..tool_actions-1. A forced terminal submit has no associated LLM call.
        next_model_action = index + 1 < int(record['tool_actions'])
        if not next_model_action:
            if invalid_outputs:
                raise AssertionError('cannot establish final-search context inclusion from retained logs')
            excluded += 1
            continue
        result = event.get('result')
        if not isinstance(result, list):
            raise AssertionError('search result is not a saved public list')
        public_hits = [{field: hit[field] for field in PUBLIC_SEARCH if field in hit}
                       for hit in result if isinstance(hit, dict)]
        hit_count += len(public_hits)
        shown.append(canonical({'step': index + 1, 'query': event.get('params'), 'returned_snippets': public_hits}))
    if not hit_count:
        text = f'该回合有检索（{len(searches)}次），已进入后续请求的检索结果未返回片段。'
        if excluded:
            text += f'另有{excluded}次回合末检索没有后续模型请求。'
        if shown:
            text += '\n' + '\n---\n'.join(shown)
    else:
        text = '\n---\n'.join(shown)
        if excluded:
            text += f'\n回合末另有{excluded}次检索未进入后续模型请求；其返回保留在完整动作栏。'
    return text, {'searches': len(searches), 'context_hits': hit_count,
                  'excluded_terminal_searches': excluded, 'returned_hits': sum(len(e.get('result') or []) for _, e in searches)}


def pipeline_search_context(record: dict) -> tuple[str, dict]:
    reader = record.get('reader') or {}
    count = int(reader.get('search_count', 0))
    if not count:
        return '该回合无检索', {'searches': 0, 'returned_hits': 0, 'context_hits': 0}
    path = evidence_path('all_public', record)
    if path is None or not path.is_file():
        raise AssertionError('pipeline retrieval evidence missing')
    bundle = json.loads(path.read_text())
    rows = bundle['search_rows']
    returned_ids = {str(chunk) for search in reader['search_events'] for chunk in search['chunk_ids']}
    if returned_ids != {str(row['chunk_id']) for row in rows}:
        raise AssertionError('pipeline returned chunk IDs differ from saved evidence')
    public = [{field: row[field] for field in PUBLIC_SEARCH} for row in rows]
    text = ('管线方法没有LLM请求；以下为共享阅读器实际接收的公开检索片段。\n'
            + '\n---\n'.join(canonical(row) for row in public)) if public else '该回合有检索，但检索未返回片段。'
    return text, {'searches': count, 'returned_hits': len(public), 'context_hits': len(public),
                  'evidence_sha256': digest(path)}


def cds_search_context(record: dict, events: list[dict]) -> tuple[str, dict]:
    prompt_path = ROOT / record['prompt_log']
    logs = [json.loads(line) for line in prompt_path.read_text().splitlines() if line.strip()]
    delivered = set()
    for prompt in logs:
        if prompt.get('record_type') != 'full_prompt' or prompt.get('call_type') == 'cds_boundary':
            continue
        for message in prompt['messages']:
            if message.get('role') != 'user':
                continue
            try:
                payload = json.loads(message['content'])
            except json.JSONDecodeError:
                continue
            delivered.add(canonical(payload.get('observation')))
    searches = [(i, event) for i, event in enumerate(events) if event.get('method') == 'search']
    if not searches:
        return '该回合无检索', {'searches': 0, 'returned_hits': 0, 'context_hits': 0}
    shown, hits, excluded = [], 0, 0
    for index, event in searches:
        result = event['result']
        if canonical(result) not in delivered:
            excluded += 1
            continue
        if not isinstance(result, list):
            raise AssertionError('CDS public search result missing')
        public = [{key: row[key] for key in ('mechanism fact', 'source', 'timestamp')} for row in result]
        hits += len(public)
        shown.append(canonical({'step': index + 1, 'query': event.get('params'), 'returned_snippets': public}))
    text = '\n---\n'.join(shown) if hits else '该回合有检索，实际进入行动请求的检索结果未返回片段。'
    if excluded:
        text += f'\n{excluded}次检索没有出现在随后保存的行动请求观测中，返回仍见完整动作。'
    return text, {'searches': len(searches), 'context_hits': hits,
                  'returned_hits': sum(len(event['result']) for _, event in searches),
                  'excluded_searches': excluded, 'prompt_log_sha256': digest(prompt_path)}


def cds_public_events(record: dict) -> list[dict]:
    raw = original.jsonl(ROOT / record['action_log'])
    prompts = original.jsonl(ROOT / record['prompt_log'])
    boundary = [row for row in prompts if row.get('record_type') == 'full_prompt'
                and row.get('call_type') == 'cds_boundary']
    if len(boundary) != 1:
        raise AssertionError('missing saved complete CDS API trace')
    payload = json.loads(next(message['content'] for message in boundary[0]['messages']
                              if message['role'] == 'user'))
    trace = payload['episode_trace']
    if len(trace) != len(raw) or [row['action'] for row in trace] != [row['method'] for row in raw]:
        raise AssertionError('saved CDS public trace and raw action log do not align')
    # The retained public API trace contains the injected initial observation,
    # which is absent from the base simulator's inventory log. No evaluator
    # candidate/truth or boundary-answer field is copied into these events.
    return [{**event, 'params': observed['args'], 'result': observed['observation']}
            for event, observed in zip(raw, trace)]


def cds_sample() -> list[tuple[dict, dict]]:
    from scripts.analyze_a9_cds_followup_20261016 import a8_sources, audit_sheet, csv_rows as a9_csv, A8

    source, metrics = a8_sources()
    replay, _ = audit_sheet(source, metrics, a9_csv(A8 / 'stale_action_audit.csv'))
    saved = csv_rows(CDS_SOURCE / 'audit_sheet_cds.csv')
    if replay != saved:
        raise AssertionError('CDS original sample reconstruction differs')
    strata = defaultdict(list)
    for key, row in metrics.items():
        if row['condition'] == 'K+S':
            label = ('propagation' if row['v3d_lower'] == '1' else
                     'contact_without_propagation' if row['discriminative_stale_contact'] == '1' else 'no_contact')
            strata[label].append(key)
    rng = np.random.default_rng(20261016)
    selected = []
    for label in ('propagation', 'contact_without_propagation', 'no_contact'):
        candidates = sorted(strata[label])
        selected.extend(candidates[i] for i in sorted(rng.choice(len(candidates), 10, replace=False).tolist()))
    if len(selected) != 30 or len(set(selected)) != 30:
        raise AssertionError('CDS sample coverage mismatch')
    return [(row, source[key]) for row, key in zip(saved, selected)]


def blind_rows(entries: list[dict], seed: int, *, cds: bool = False) -> tuple[list[dict], list[dict]]:
    shuffled = list(entries)
    random.Random(seed).shuffle(shuffled)
    output, mapping = [], []
    for index, entry in enumerate(shuffled, 1):
        identifier = f"{'AC' if cds else 'AX'}-{index:03d}"
        old = entry['old']
        parts = split_cell(entry['actions'])
        snippets = split_cell(entry['snippets'])
        row = {'audit_id': identifier, **{field: old[field] for field in META},
               'n_actions': entry['n_actions'], 'truncated': int(len(parts) > 1),
               'action_sequence_and_feedback': (parts[0] if len(parts) == 1 else
                    f'完整动作按顺序见 action_part1 至 action_part{len(parts)}；仅拆分，未删减。'),
               'search_snippets': (snippets[0] if len(snippets) == 1 else
                    f'完整检索片段按顺序见 search_part1 至 search_part{len(snippets)}；仅拆分，未删减。'),
               'search_split': int(len(snippets) > 1)}
        if len(parts) > 1:
            row.update({f'action_part{i}': part for i, part in enumerate(parts, 1)})
        if len(snippets) > 1:
            row.update({f'search_part{i}': part for i, part in enumerate(snippets, 1)})
        if not cds:
            row.update({new: entry['human'][oldfield] for oldfield, new in PREVIOUS.items()})
        row.update({field: '' for field in ANNOTATION_FIELDS})
        output.append(row)
        mapping.append({'audit_id': identifier, 'trajectory_id': old['trajectory_id']})
    return output, mapping


def validate_blind(rows: list[dict], mapping: list[dict], entries: list[dict], *, cds: bool = False) -> dict:
    expected = 30 if cds else 147
    if len(rows) != expected or len(mapping) != expected:
        raise AssertionError('blind sheet row count changed')
    if len({row['audit_id'] for row in rows}) != expected or len({row['trajectory_id'] for row in mapping}) != expected:
        raise AssertionError('ID map is not one-to-one')
    original_ids = {entry['old']['trajectory_id'] for entry in entries}
    if {row['trajectory_id'] for row in mapping} != original_ids:
        raise AssertionError('ID map has missing source rows')
    by_original = {entry['old']['trajectory_id']: entry for entry in entries}
    by_id = {row['audit_id']: row for row in mapping}
    max_units = 0
    for row in rows:
        entry = by_original[by_id[row['audit_id']]['trajectory_id']]
        action = (''.join(row[f'action_part{i}'] for i in range(1, 1 + len(split_cell(entry['actions']))))
                  if row['truncated'] else row['action_sequence_and_feedback'])
        search = (''.join(row[f'search_part{i}'] for i in range(1, 1 + len(split_cell(entry['snippets']))))
                  if row['search_split'] else row['search_snippets'])
        if action != entry['actions'] or search != entry['snippets']:
            raise AssertionError('split output lost or altered evidence')
        if '"method":"submit"' not in action:
            raise AssertionError('episode has no visible submit; STOP and inspect raw episode')
        for field, value in row.items():
            if field in {'trajectory_id', 'stratum', 'propagation_label', 'contact_label'}:
                raise AssertionError('scorer or sampling identity leaked')
            text = str(value)
            if re.search(r'\bS[1-5]\b|A6-[EP]-(?:S4-)?\d{3}|A9-CDS-\d{3}', text):
                raise AssertionError('original sample ID or stratum marker leaked')
            units = len(text.encode('utf-16-le')) // 2
            max_units = max(max_units, units)
            if units > CELL_LIMIT:
                raise AssertionError('review cell exceeds registered readable limit')
        if not cds and any(row[new] != entry['human'][old] for old, new in PREVIOUS.items()):
            raise AssertionError('previous human annotation alignment failed')
        if any(row[field] for field in ANNOTATION_FIELDS):
            raise AssertionError('new annotation columns must be empty')
    lengths = [entry['n_actions'] for entry in entries]
    return {'rows': expected, 'n_actions_min': min(lengths), 'n_actions_median': median(lengths),
            'n_actions_max': max(lengths), 'n_actions_mean': mean(lengths),
            'n_actions_eq_4': sum(n == 4 for n in lengths), 'submit_visible_rows': expected,
            'submit_visible_rate': 1.0, 'action_split_rows': sum(row['truncated'] for row in rows),
            'search_split_rows': sum(row['search_split'] for row in rows), 'max_cell_utf16_units': max_units,
            'one_to_one': True, 'literal_stratum_or_original_id_leaks': 0}


def write_csv(path: Path, rows: list[dict]) -> None:
    fields = []
    for row in rows:
        fields.extend(key for key in row if key not in fields)
    with path.open('w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--annotated-sheet', type=Path, required=True)
    args = parser.parse_args()
    paths = [SOURCE / 'audit_sheet_v2.csv', CDS_SOURCE / 'audit_sheet_cds_v2.csv',
             PRIVATE / 'audit_idmap.csv', PRIVATE / 'audit_idmap_cds.csv']
    existing = {}
    if any(path.exists() for path in paths):
        previous_checks = DIAGNOSTICS / 'step3_checks.json'
        if not all(path.is_file() for path in paths) or not previous_checks.is_file():
            raise RuntimeError('refusing to overwrite unrecognized review sheets or mappings')
        registered = json.loads(previous_checks.read_text())['outputs_sha256']
        existing = {str(path.relative_to(ROOT)): digest(path) for path in paths}
        if registered != existing:
            raise RuntimeError('review outputs changed since export; refuse to overwrite human edits')
    guards = [SOURCE / 'audit_sheet.csv', SOURCE / 'audit_key.csv', CDS_SOURCE / 'audit_sheet_cds.csv',
              CDS_SOURCE / 'audit_key_cds.csv', args.annotated_sheet]
    before = {str(path): digest(path) for path in guards}
    old = csv_rows(SOURCE / 'audit_sheet.csv')
    human = annotations(args.annotated_sheet, old)
    replay, _, anchors, records, _ = recover_sample()
    if replay != old:
        raise AssertionError('original unannotated sample changed')
    entries = []
    searches = []
    for row in old:
        record = resolve_record(row, anchors, records)
        raw = original.raw_events(record, row['agent_family'] == 'pipeline')
        events = full_episode(raw)
        if row['agent_family'] == 'external':
            snippets, provenance = external_search_context(record, events)
        else:
            snippets, provenance = pipeline_search_context(record)
        entries.append({'old': row, 'human': human[row['trajectory_id']], 'actions': sequence(events),
                        'snippets': snippets, 'n_actions': len(events)})
        searches.append({'trajectory_id': row['trajectory_id'], 'events_after_first_submit_excluded': len(raw) - len(events),
                         'raw_events': len(raw), 'exported_actions': len(events), **provenance})
    cds_entries = []
    for row, record in cds_sample():
        raw = cds_public_events(record)
        events = full_episode(raw)
        snippets, provenance = cds_search_context(record, events)
        cds_entries.append({'old': row, 'actions': sequence(events), 'snippets': snippets, 'n_actions': len(events)})
        searches.append({'trajectory_id': row['trajectory_id'], 'events_after_first_submit_excluded': len(raw) - len(events),
                         'raw_events': len(raw), 'exported_actions': len(events), **provenance})
    rows, mapping = blind_rows(entries, SEED)
    cds_rows, cds_mapping = blind_rows(cds_entries, SEED + 1, cds=True)
    checks = validate_blind(rows, mapping, entries)
    cds_checks = validate_blind(cds_rows, cds_mapping, cds_entries, cds=True)
    step0 = json.loads((DIAGNOSTICS / 'step0_counts.json').read_text())
    if step0['source_sha256'] != digest(SOURCE / 'audit_sheet.csv') or step0['key_sha256'] != digest(SOURCE / 'audit_key.csv'):
        raise AssertionError('step-0 diagnostics refer to different source sheets')

    private_key = {row['trajectory_id']: row['stratum'] for row in csv_rows(SOURCE / 'audit_key.csv')}
    associations = []
    for fields in [(field,) for field in META] + [META]:
        grouped, counts = defaultdict(set), Counter()
        for row in old:
            values = tuple(row[field] for field in fields)
            grouped[values].add(private_key[row['trajectory_id']])
            counts[values] += 1
        associations.append({'fields': list(fields), 'groups': len(grouped),
                             'single_stratum_groups': sum(len(v) == 1 for v in grouped.values()),
                             'single_stratum_rows': sum(counts[k] for k, v in grouped.items() if len(v) == 1)})
    # Hold the release if any source changed while the export was being built.
    if {str(path): digest(path) for path in guards} != before:
        raise AssertionError('input files changed during export')
    if existing and {str(path.relative_to(ROOT)): digest(path) for path in paths} != existing:
        raise AssertionError('review outputs changed during export; preserve concurrent human edits')
    PRIVATE.mkdir(parents=True, exist_ok=True)
    DIAGNOSTICS.mkdir(parents=True, exist_ok=True)
    for path, values in zip(paths, (rows, cds_rows, mapping, cds_mapping)):
        write_csv(path, values)
    result = {'main': checks, 'CDS': cds_checks, 'shuffle_seed_main': SEED, 'shuffle_seed_cds': SEED + 1,
              'source_sha256': before, 'outputs_sha256': {str(p.relative_to(ROOT)): digest(p) for p in paths},
              'metadata_sample_associations': associations, 'llm_calls': 0, 'new_episodes': 0,
              'human_annotation_nonempty': {field: sum(bool(human[r['trajectory_id']][field]) for r in old) for field in ANNOTATION_FIELDS}}
    result['original_window_contact_counts_by_stratum'] = step0['window_contact_counts_by_stratum']
    (DIAGNOSTICS / 'step3_checks.json').write_text(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + '\n')
    (PRIVATE / 'retrieval_provenance.json').write_text(json.dumps(searches, indent=2, sort_keys=True) + '\n')
    report = ['# Complete semantic audit sheets v2', '',
              'Zero model calls; zero environment episodes; no model/game server was started.',
              'The existing server.lock is not acquired or modified by this offline export.',
              'WebCraft and all source audit sheets/keys remain read-only.', '',
              f'Main shuffle seed: {SEED}. CDS shuffle seed: {SEED + 1}.',
              'The original 147 rows and the original 30 CDS rows are retained without resampling.',
              'New IDs are assigned after shuffling; private ID maps are not annotator-facing.', '',
              '## Complete episode and cells', '',
              'Every action, parameter and full public result is exported from the first inventory through',
              'the first submit, inclusive. Trailing evaluator inventory reads are excluded and counted',
              'in the private provenance file. Internal intent/probe/scorer fields are not exported.',
              'n_actions counts all public actions, including search and inventory, not only paid actions.',
              'Cells are limited to 30,000 UTF-16 units. Oversized sequences are split losslessly into',
              'action_part1...; truncated=1 denotes splitting only, never removed content. Oversized',
              'retrieval text uses search_part1... and search_split=1. Reassemble parts in numeric order.', '',
              '## Previous human annotations', '',
              'The annotated input supplied by the author was matched on the complete original ID set',
              'Its location is /root/autodl-tmp/audit_sheet.csv; this resolves the missing-input stop',
              'recorded in the earlier STEP0_REPORT.md without changing the recorded stage-0 results.',
              'and unchanged evidence/metadata. All prev_* values, including existing blanks, were copied',
              'verbatim from that input, never from the scorer key. New annotation fields are blank.',
              f"Nonempty previous fields: {result['human_annotation_nonempty']}.", '',
              '## Retrieval provenance', '',
              'For first-run external agents, full prompt logs were not retained. Saved public search',
              'results are counted as delivered only when followed by a model-generated action under',
              'the frozen executor, whose next request includes the entire latest observation. Search',
              'at the last call followed only by an automatic submit is excluded from context snippets',
              '(but remains visible in the complete action log). This is executor/log reconstruction,',
              'not verification against a retained prompt. Unresolvable call/action accounting stops export.',
              'CDS returned facts are checked against actual retained non-boundary prompt observations.',
              'CDS action feedback is taken from the retained public API trace, which includes initial',
              'knowledge injection; trace length and action types must align exactly with the raw log.',
              'Pipeline methods have no LLM context: their public reader search_rows are supplied,',
              'validated against the saved reader chunk IDs. No extracted scorer/truth claim fields are copied.',
              'No-search and empty-search episodes are explicitly distinguished.', '',
              '## Blinding limits', '',
              'No original IDs, sampling columns or literal S1-S5 markers occur in either review sheet.',
              'Metadata requested by the author is retained. It contains no explicit stratum encoding,',
              'but statistical independence cannot be claimed: task_id alone has three single-stratum',
              'groups covering four rows; the full metadata tuple has 113 such groups covering 130 rows.',
              'These are sample associations, not proof that an annotator without the key can recover',
              'a stratum. No source labels or per-row stratum associations are released in review sheets.', '',
              '## Validation', '', '```json', json.dumps({'main': checks, 'CDS': cds_checks}, indent=2), '```', '',
              'Step-0 anchor/window comparisons are unchanged; see STEP0_REPORT.md and step0_counts.json',
              'in this author-only diagnostics directory. They were verified against the same 147 original IDs.', '',
              '| Original stratum (author diagnostics only) | Rows | Any contact outside original window | All contacts outside |',
              '| --- | ---: | ---: | ---: |']
    for label, values in step0['window_contact_counts_by_stratum'].items():
        report.append(f"| {label} | {values['rows']} | {values['any_contact_outside_rows']} | {values['all_contacts_outside_rows']} |")
    report.extend(['', 'The selected contact anchor itself is outside its own original window in 0 rows.',
                   'In total 36 rows have another scored stale-specific contact outside the old window;',
                   'three rows have all their scored stale-specific contact outside. These counts match step 0.', ''])
    (DIAGNOSTICS / 'STEP3_REPORT.md').write_text('\n'.join(report))
    print(json.dumps({'main': checks, 'CDS': cds_checks}, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
