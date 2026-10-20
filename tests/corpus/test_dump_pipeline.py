import io
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import zstandard

from corpus.dump_pipeline import extract_snapshots, iter_pages, normalize_title, resolve_items


XML = b'''<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.11/">
<page><title>Block of Iron</title><ns>0</ns><id>1</id>
<revision><id>10</id><timestamp>2021-01-01T00:00:00Z</timestamp><text>old</text></revision>
<revision><id>20</id><timestamp>2022-01-01T00:00:00Z</timestamp><slots><slot role="main"><text>new</text></slot></slots></revision>
</page>
<page><title>Old Name</title><ns>0</ns><redirect title="New Name"/><revision><id>30</id><timestamp>2022-01-01T00:00:00Z</timestamp><text>#REDIRECT</text></revision></page>
<page><title>New Name</title><ns>0</ns><revision><id>40</id><timestamp>2022-01-01T00:00:00Z</timestamp><text>target</text></revision></page>
</mediawiki>'''


def test_iter_pages_reads_direct_and_slotted_text():
    pages = list(iter_pages(io.BytesIO(XML)))
    assert pages[0].title == "Block of Iron"
    assert [revision.text for revision in pages[0].revisions] == ["old", "new"]
    assert pages[1].redirect == "New Name"


def test_resolve_items_uses_overrides_and_redirects():
    manifest = {
        "items": [
            {"item_id": "iron_block", "candidate_title": "Iron Block"},
            {"item_id": "old_name", "candidate_title": "Old Name"},
            {"item_id": "missing", "candidate_title": "Missing"},
        ]
    }
    titles = {normalize_title(x): x for x in ("Block of Iron", "Old Name", "New Name")}
    rows = resolve_items(manifest, titles, {normalize_title("Old Name"): "New Name"})
    assert rows[0]["page_title"] == "Block of Iron"
    assert rows[1]["page_title"] == "New Name"
    assert rows[1]["redirect_from"] == "Old Name"
    assert rows[2]["verified_in_dump"] is False


def test_extract_snapshots_collapses_duplicate_page_batches(tmp_path):
    duplicate_xml = b'''<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.11/">
    <page><title>Stone</title><ns>0</ns><revision><id>1</id><timestamp>2021-01-01T00:00:00Z</timestamp><text>old</text></revision></page>
    <page><title>stone</title><ns>0</ns><revision><id>2</id><timestamp>2021-02-01T00:00:00Z</timestamp><text>new</text></revision></page>
    </mediawiki>'''
    dump = tmp_path / "history.xml.zst"
    dump.write_bytes(zstandard.ZstdCompressor().compress(duplicate_xml))
    records = extract_snapshots(
        dump,
        {"Stone"},
        {"v": datetime(2021, 3, 1, tzinfo=timezone.utc)},
        tmp_path / "out",
    )
    assert records["v"]["Stone"]["revid"] == 2
    assert list((tmp_path / "out/v").glob("*.wikitext"))[0].read_text() == "new"
    assert len(list((tmp_path / "out/v").glob("*.wikitext"))) == 1
