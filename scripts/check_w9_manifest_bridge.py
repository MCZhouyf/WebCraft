#!/usr/bin/env python3
"""Check that release finalization left every replayed research input intact."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'reports/paperA/w9_release_fix'
PACKAGE = ROOT / 'release/package'
ALLOWED = {
    '.gitignore', 'README.md', 'reproduce_main.sh',
    'docs/REDISTRIBUTION_NOTES.md', 'docs/RESTRICTED_FILES.json',
    'corpus/encrypted/w9_replay_restricted.tar.gz.enc',
    'repro/public_observation_scoring.py',
    'tests/methods/test_public_observations.py',
}


def main():
    unchanged = []
    changed = []
    for line in (OUT / 'replay_input_manifest.sha256').read_text().splitlines():
        expected, name = line.split('  ', 1)
        path = PACKAGE / name
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual == expected:
            unchanged.append(name)
        else:
            changed.append({'path': name, 'before': expected, 'after': actual})
    unexpected = [row for row in changed if row['path'] not in ALLOWED]
    result = {'unchanged_files': len(unchanged), 'finalization_changes': changed,
              'unexpected_changes': unexpected,
              'all_replayed_method_runtime_data_and_evidence_unchanged': not unexpected}
    (OUT / 'final_manifest_bridge.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
    if unexpected:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
