import csv
from pathlib import Path

import pytest

from corpus.redirect_audit import body_length, redirect_target, resolve_chain, title_key, validate_selected_pages


def test_history_fragments_cannot_overwrite_newer_eligible_revision():
    import sqlite3
    from corpus.redirect_audit import save_catalog_revision
    from corpus.dump_pipeline import Revision

    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE revisions (source TEXT,title TEXT,revid INTEGER,timestamp TEXT,text TEXT,PRIMARY KEY(source,title))')
        save_catalog_revision(db, 'T1', 'Log', Revision(20, '2021-01-01T00:00:00Z', 'new'))
        save_catalog_revision(db, 'T1', 'Log', Revision(10, '2012-01-01T00:00:00Z', 'old'))
        assert db.execute('SELECT revid,text FROM revisions').fetchone() == (20, 'new')


def test_streaming_selector_ignores_contributor_ids_and_future_revisions():
    from io import BytesIO
    from corpus.dump_pipeline import parse_timestamp
    from corpus.redirect_audit import scan_selected_stream

    stream = BytesIO(b'''<mediawiki><page><title>Log</title><ns>0</ns>
    <revision><id>20</id><timestamp>2021-01-01T00:00:00Z</timestamp><contributor><id>999</id></contributor><text>new</text></revision>
    <revision><id>30</id><timestamp>2022-01-01T00:00:00Z</timestamp><text>future</text></revision></page>
    <page><title>Log</title><ns>0</ns><revision><id>10</id><timestamp>2012-01-01T00:00:00Z</timestamp><text>old</text></revision></page>
    <page><title>Other</title><ns>0</ns><revision><id>21</id><timestamp>2021-01-01T00:00:00Z</timestamp><text>ignore</text></revision></page></mediawiki>''')
    best = scan_selected_stream(stream, {'Log'}, {'T1': parse_timestamp('2021-02-01T00:00:00Z')})
    assert list(best) == [('T1', 'Log')]
    assert best['T1', 'Log'].revid == 20
    assert best['T1', 'Log'].text == 'new'


def test_redirect_syntax_fragments_and_case_are_preserved():
    assert redirect_target('#redirect:[[Oak Planks#Obtaining]]\n{{Redr|caps}}') == 'Oak Planks'
    assert title_key('oak_planks') == 'Oak planks'
    assert title_key('Oak Planks') != title_key('Oak planks')
    assert redirect_target('<!-- comment -->\n#REDIRECT [[Log#Oak]]') == 'Log'


def test_chain_uses_selected_revision_body_not_latest_page_redirect():
    pages = {'Oak planks': {'text': '#REDIRECT [[Old Planks]]'},
             'Old Planks': {'text': '#REDIRECT [[Planks]]'},
             'Planks': {'text': 'A complete archived description. ' * 10}}
    title, record, status, chain = resolve_chain('Oak planks', pages.get)
    assert (title, status) == ('Planks', 'resolved')
    assert chain == ['Oak planks', 'Old Planks', 'Planks']
    assert body_length(record['text']) >= 200


def test_cycles_and_missing_targets_do_not_become_absence():
    pages = {'A': {'text': '#REDIRECT [[B]]'}, 'B': {'text': '#REDIRECT [[A]]'}}
    assert resolve_chain('A', pages.get)[2] == 'redirect_cycle'
    assert resolve_chain('Missing', pages.get)[2] == 'target_missing'


def test_template_only_page_is_a_shell():
    assert body_length('{{Block|text=' + 'x' * 500 + '}}') == 0


def test_author_export_uses_cutoff_and_latest_revision(tmp_path):
    import json
    from corpus.redirect_audit import import_redirect_exports

    (tmp_path / 'corpus/manifests').mkdir(parents=True)
    (tmp_path / 'corpus/release_times.json').write_text(json.dumps({'1.21.11': {'corpus_cutoff': '2026-01-08T12:23:30+00:00'}}))
    export = tmp_path / 'history.xml'
    export.write_text('''<mediawiki><page><title>Planks</title><ns>0</ns>
        <revision><id>10</id><timestamp>2026-01-08T10:00:00Z</timestamp><text>cutoff</text></revision>
        <revision><id>11</id><timestamp>2026-02-01T00:00:00Z</timestamp><text>current</text></revision>
        </page><page><title>Planks</title><ns>0</ns>
        <revision><id>9</id><timestamp>2025-01-01T00:00:00Z</timestamp><text>older fragment</text></revision>
        </page></mediawiki>''')
    assert import_redirect_exports(tmp_path, [export]) == {'T3': 1, 'current': 1}
    manifest = json.loads((tmp_path / 'corpus/manifests/redirect_export_snapshots.json').read_text())
    assert manifest['snapshots']['T3']['Planks']['revid'] == 10
    assert manifest['snapshots']['current']['Planks']['revid'] == 11


def test_local_selected_pages_are_resolved_or_proven_absent():
    root = Path(__file__).resolve().parents[2]
    catalog = root / 'corpus/raw/redirect_resolved/catalog.sqlite'
    if not catalog.exists():
        pytest.skip('Local WikiTeam catalog is not distributed')
    validate_selected_pages(root, {'T1', 'T2', 'Fandom2023'})


def test_pending_export_blocks_all_source_readiness():
    root = Path(__file__).resolve().parents[2]
    report = root / 'reports/redirect_audit.csv'
    if not report.exists():
        pytest.skip('Local redirect audit has not been run')
    rows = list(csv.DictReader(report.open()))
    unresolved = [r for r in rows if r['status'] not in {'resolved', 'absent_at_cutoff'}]
    if unresolved:
        with pytest.raises(ValueError, match='Unresolved selected pages'):
            validate_selected_pages(root)
    else:
        validate_selected_pages(root)
