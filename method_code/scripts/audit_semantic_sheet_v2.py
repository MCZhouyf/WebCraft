"""Read-only step-0 reconstruction of the existing A6 sample, never resampling it."""
from __future__ import annotations

import csv
import hashlib
import json
import random
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from scripts import run_review_v13_audit_20261015 as original
from scripts.run_measurement_v3_20261014 import EXTERNAL_PATHS, jsonl
from scripts.run_measurement_v3d_20261015 import PIPELINE

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'reports/main/post_review/review_v13_audit'
OUT = SOURCE / 'reexport_v2_diagnostics'
ANNOTATION_FIELDS = ('contact_type', 'propagation', 'terminal_depends_on_stale', 'notes')


def csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline='') as handle:
        return list(csv.DictReader(handle))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_records() -> tuple[dict, dict, set]:
    external = {}
    for path in EXTERNAL_PATHS.values():
        for record in jsonl(path):
            key = ('first_run', record['method'], record['condition'], record['world'],
                   record['task_id'], int(record['seed']))
            if key in external:
                raise RuntimeError('duplicate external record')
            external[key] = record
    common = {(k[2], k[3], k[4]) for k in external if k[1] == 'direct_llm'}
    if len(common) != 110:
        raise RuntimeError('first-run common matrix changed')
    pipeline = {}
    for rel in ('artifacts/main/all_public/episodes.jsonl',
                'artifacts/post_run_probe_then_rag/all_public/episodes.jsonl'):
        with (ROOT / rel).open() as handle:
            for line in handle:
                record = json.loads(line)
                if (record['method'] not in PIPELINE or int(record['seed']) != 20261006
                        or (record['condition'], record['world'], record['task_id']) not in common):
                    continue
                key = ('all_public', record['method'], record['condition'], record['world'],
                       record['task_id'], int(record['seed']))
                if key in pipeline:
                    raise RuntimeError('duplicate pipeline record')
                pipeline[key] = record
    return external, pipeline, common


def original_selected(actions: list[dict], family: str, rng: random.Random) -> list[dict]:
    """Recover anchors using the exact original algorithm and RNG state."""
    replay = random.Random()
    replay.setstate(rng.getstate())
    selected = [row for row in actions if int(row.get('v3d_propagation', 0))]
    candidates = (
        ([row for row in actions if int(row.get('discriminative', 0)) and row['action_method'] == 'check_name'], 30),
        ([row for row in actions if int(row.get('discriminative', 0)) and row['action_method'] != 'check_name'], 30),
        ([row for row in actions if int(row.get('mismatch', 0)) and not int(row.get('discriminative', 0))], 40),
    )
    for rows, cap in candidates:
        replay.shuffle(rows)
        selected.extend(rows[:cap])
    seen = set()
    output = []
    for row in selected:
        key = (family, row['method'], row['condition'], row['world'], row['task_id'],
               row['seed'], row['event_index'])
        if key not in seen:
            seen.add(key)
            output.append(row)
    return output


def recover_sample() -> tuple[list[dict], list[dict], dict, dict, dict]:
    external, pipeline, common = source_records()
    ext_actions, pipe_actions = original.all_v3d_actions(common, external, pipeline)
    rng = random.Random(original.SEED)
    anchors = {}
    sheets, keys = [], []
    for family, actions, records in (
            ('external', ext_actions, external), ('pipeline', pipe_actions, pipeline)):
        selected = original_selected(actions, family, rng)
        sheet, key = original.audit_samples(actions, records, family, rng)
        if len(sheet) != len(selected):
            raise RuntimeError('anchor reconstruction count differs')
        for shown, action in zip(sheet, selected):
            anchors[shown['trajectory_id']] = (family, action)
        sheets.extend(sheet)
        keys.extend(key)
    sheet, key = original.abstention_samples(ext_actions, external, 'external', rng)
    sheets.extend(sheet)
    keys.extend(key)
    sheets, keys = original.cap_global_sample(sheets, keys, rng)
    return sheets, keys, anchors, {'external': external, 'pipeline': pipeline}, {
        'external': ext_actions, 'pipeline': pipe_actions,
    }


def main() -> int:
    saved = csv_rows(SOURCE / 'audit_sheet.csv')
    saved_keys = csv_rows(SOURCE / 'audit_key.csv')
    reconstructed, replay_keys, anchors, records_by, actions_by = recover_sample()
    if len(saved) != 147 or len({r['trajectory_id'] for r in saved}) != 147:
        raise RuntimeError('source sample coverage differs from 147')
    if saved_keys != replay_keys:
        raise RuntimeError('reconstructed sample key differs; STOP without exports')
    for old, replay in zip(saved, reconstructed):
        if any(old[field] != replay[field] for field in replay if field not in ANNOTATION_FIELDS):
            raise RuntimeError('original sheet reconstruction differs; STOP without exports')
    key_by = {row['trajectory_id']: row for row in saved_keys}
    counts = Counter(row['stratum'] for row in saved_keys)
    rows = []
    for old in saved:
        identifier = old['trajectory_id']
        key = key_by[identifier]
        family = old['agent_family']
        actions = actions_by[family]
        if identifier in anchors:
            _, anchor = anchors[identifier]
            record = records_by[family][original.record_key(anchor, family)]
            index = int(anchor['event_index'])
        else:
            matching = [record for record in records_by[family].values()
                        if all(str(record[f]) == old[f] for f in ('method', 'condition', 'world', 'task_id'))]
            if len(matching) != 1:
                raise RuntimeError('abstention trajectory source ambiguous')
            record = matching[0]
            index = None
        events = original.raw_events(record, family == 'pipeline')
        start = max(0, index - 1) if index is not None else max(0, len(events) - 4)
        stop = min(len(events), index + 3) if index is not None else len(events)
        matching_actions = [a for a in actions if all(str(a[f]) == str(record[f])
                            for f in ('method', 'condition', 'world', 'task_id', 'seed'))]
        contacts = sorted({int(a['event_index']) for a in matching_actions if int(a.get('discriminative', 0))})
        outside = [position for position in contacts if not start <= position < stop]
        selected_contact = index if index is not None and any(
            int(a['event_index']) == index and int(a.get('discriminative', 0)) for a in matching_actions) else None
        window = ' -> '.join(original.compact_event(e) for e in events[start:stop])
        if window != old['action_sequence_and_feedback']:
            raise RuntimeError('reconstructed window differs from original')
        rows.append({
            'trajectory_id': identifier, 'stratum': key['stratum'], 'window_start_0based': start,
            'window_stop_exclusive': stop, 'anchor_event_index_0based': index,
            'selected_contact_event_index_0based': selected_contact,
            'selected_contact_outside': bool(selected_contact is not None and not start <= selected_contact < stop),
            'all_contact_event_indices_0based': contacts, 'outside_contact_event_indices_0based': outside,
            'any_contact_outside': bool(outside), 'all_contacts_outside': bool(contacts and len(outside) == len(contacts)),
            'raw_episode_events': len(events), 'window_events': stop - start,
            'window_has_submit': any(e.get('method') == 'submit' for e in events[start:stop]),
        })
    revisions = {}
    path = str((SOURCE / 'audit_sheet.csv').relative_to(ROOT))
    for revision in ('3202363', 'ecc7eb3', '1e81ab9', 'HEAD'):
        revisions[revision] = subprocess.check_output(
            ['git', 'rev-parse', f'{revision}:{path}'], cwd=ROOT, text=True).strip()
    annotations = {field: sum(bool(row[field].strip()) for row in saved) for field in ANNOTATION_FIELDS}
    by_stratum = {}
    for stratum in counts:
        values = [row for row in rows if row['stratum'] == stratum]
        by_stratum[stratum] = {
            'rows': len(values),
            'selected_contact_outside_rows': sum(row['selected_contact_outside'] for row in values),
            'any_contact_outside_rows': sum(row['any_contact_outside'] for row in values),
            'all_contacts_outside_rows': sum(row['all_contacts_outside'] for row in values),
        }
    stats = {
        'rows': len(rows), 'strata': dict(counts), 'source_git_blobs': revisions,
        'window_contact_counts_by_stratum': by_stratum,
        'source_sha256': digest(SOURCE / 'audit_sheet.csv'), 'key_sha256': digest(SOURCE / 'audit_key.csv'),
        'reconstructed_exactly': True, 'selected_contact_outside_rows': sum(r['selected_contact_outside'] for r in rows),
        'any_contact_outside_rows': sum(r['any_contact_outside'] for r in rows),
        'all_contacts_outside_rows': sum(r['all_contacts_outside'] for r in rows),
        'contact_episode_rows': sum(bool(r['all_contact_event_indices_0based']) for r in rows),
        'window_submit_visible_rows': sum(r['window_has_submit'] for r in rows),
        'annotation_nonempty_counts': annotations, 'llm_calls': 0, 'new_episodes': 0,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'step0_counts.json').write_text(json.dumps(stats, indent=2, sort_keys=True) + '\n')
    (OUT / 'step0_window_contacts.json').write_text(json.dumps(rows, indent=2, sort_keys=True) + '\n')
    report = ['# Semantic-audit re-export: step 0', '',
              'No model calls or environment episodes. WebCraft is read-only.',
              'This report contains sampling diagnostics; it is not an annotator-facing review file.', '',
              '## Original window', '',
              '`scripts/run_review_v13_audit_20261015.py:191`:', '',
              '```python',
              'events[max(0, index - 1):min(len(events), index + 3)]',
              '```', '',
              'The anchor is the sampled scored action event_index, not the first craft failure.',
              'It includes one preceding and two following raw public events. No submit boundary is applied.',
              'Abstention rows instead use events[-4:] at line 213.',
              'compact_event additionally slices each serialized event to 700 characters at line 150.', '',
              '## Sampling provenance', '',
              f'Strata in the actual key: {dict(counts)}.',
              'The guide numbering S1/S2/S3/S4/S5 conflates descriptive numbering with code identifiers:',
              'the code uses S1=propagation, S2_query and S2_execution, S3_nonspecific,',
              'and S4_no_contact_abstention. The five actual counts are 27/30/30/40/20.',
              'The current sheet was last changed in commit 3202363. ecc7eb3 and 1e81ab9',
              'contain the same sheet blob; neither is a separate sampling version.',
              f'Git blobs: {revisions}.',
              'Replayed original RNG state and anchors exactly; all 147 identity/content rows and key rows match.',
              'This reconstructs existing selections and does not replace or change the sample.', '',
              '## Contact positions', '',
              f"Sampled contact anchors outside their own window: {stats['selected_contact_outside_rows']}.",
              f"Rows with any scored stale-specific contact outside the window: {stats['any_contact_outside_rows']}.",
              f"Rows with all such contacts outside: {stats['all_contacts_outside_rows']}.",
              f"Rows with a scored stale-specific contact anywhere in the episode: {stats['contact_episode_rows']}.",
              f"Original-window submit visible rows: {stats['window_submit_visible_rows']}/147.",
              'Event positions use raw-log zero-based indices. The per-row detail distinguishes selected',
              'anchors from other stale-specific contacts in that same complete episode.', '',
              'Per-stratum window counts (diagnostic file only, never a review-sheet column):',
              '```json', json.dumps(by_stratum, indent=2, sort_keys=True), '```', '',
              '## Annotation gate', '',
              f'Nonempty human-annotation fields in the supplied server sheet: {annotations}.',
              'STOP before step 1: the completed human annotation file has not been located.',
              'The requested prev_* columns cannot be populated from an all-blank original sheet.',
              'The scorer key is not a substitute for human annotations. No v2 review sheet is released.',
              'The audit keys and original sheets are unchanged.', '']
    (OUT / 'STEP0_REPORT.md').write_text('\n'.join(report))
    print(json.dumps(stats, sort_keys=True))
    return 2 if not any(annotations.values()) else 0


if __name__ == '__main__':
    raise SystemExit(main())
