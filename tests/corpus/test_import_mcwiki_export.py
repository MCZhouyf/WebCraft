import io
from pathlib import Path

from corpus.import_mcwiki_export import import_exports


def export_xml(title: str, revisions: list[tuple[int, str, str]]) -> bytes:
    body = "".join(
        f"<revision><id>{rid}</id><timestamp>{timestamp}</timestamp>"
        f"<text bytes='{len(text)}'>{text}</text></revision>"
        for rid, timestamp, text in revisions
    )
    return (
        "<mediawiki xmlns='http://www.mediawiki.org/xml/export-0.11/'>"
        f"<page><title>{title}</title><ns>0</ns><id>1</id>{body}</page></mediawiki>"
    ).encode()


def test_import_selects_latest_history_before_cutoff(tmp_path: Path):
    current = tmp_path / "current.xml"
    history = tmp_path / "history.xml"
    current.write_bytes(export_xml("Stone", [(3, "2026-02-01T00:00:00Z", "new")]))
    history.write_bytes(
        export_xml(
            "Stone",
            [
                (1, "2025-12-01T00:00:00Z", "old"),
                (2, "2026-01-08T12:00:00Z", "target"),
                (3, "2026-02-01T00:00:00Z", "new"),
            ],
        )
    )
    manifest, manual = import_exports(
        [current], [history], ["Stone"], "2026-01-08T12:23:30+00:00", tmp_path / "raw"
    )
    assert manual == []
    assert manifest["t3"]["Stone"]["revid"] == 2
    assert (tmp_path / "raw/t3/2.wikitext").read_text() == "target"


def test_import_uses_unchanged_current_revision(tmp_path: Path):
    current = tmp_path / "current.xml"
    current.write_bytes(export_xml("Coal", [(5, "2025-01-01T00:00:00Z", "same")]))
    manifest, manual = import_exports(
        [current], [], ["Coal"], "2026-01-08T12:23:30+00:00", tmp_path / "raw"
    )
    assert manual == []
    assert manifest["t3"]["Coal"]["t3_equals_current"] is True


def test_import_reports_changed_page_without_history(tmp_path: Path):
    current = tmp_path / "current.xml"
    current.write_bytes(export_xml("Iron", [(8, "2026-03-01T00:00:00Z", "new")]))
    manifest, manual = import_exports(
        [current], [], ["Iron"], "2026-01-08T12:23:30+00:00", tmp_path / "raw"
    )
    assert manifest["t3"] == {}
    assert manual[0]["page_title"] == "Iron"
