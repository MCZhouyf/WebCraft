import json
from pathlib import Path

import pytest

from corpus.http_client import FANDOM_API_URL, CrawlError, WikiHttpClient


class FakeResponse:
    status_code = 200
    content = b'{"ok":true}'
    url = FANDOM_API_URL
    headers = {"Content-Type": "application/json", "X-Secret": "not persisted"}

    def raise_for_status(self):
        return None


def client(tmp_path: Path) -> WikiHttpClient:
    contact = tmp_path / "contact.txt"
    contact.write_text("research@example.invalid\n")
    return WikiHttpClient(tmp_path / "raw", contact, min_interval=1.0, max_retries=0)


def test_rejects_raw_action(tmp_path):
    c = client(tmp_path)
    with pytest.raises(ValueError, match="forbidden"):
        c.api(purpose="test", action="raw", title="Stone")


def test_api_adds_maxlag_and_reuses_cache(tmp_path, monkeypatch):
    c = client(tmp_path)
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse()

    monkeypatch.setattr(c.session, "get", fake_get)
    first = c.api(purpose="test", action="query", meta="siteinfo")
    second = c.api(purpose="test", action="query", meta="siteinfo")
    assert len(calls) == 1
    assert calls[0][0] == FANDOM_API_URL
    assert calls[0][1]["params"]["maxlag"] == "5"
    assert not first.cache_hit and second.cache_hit
    meta_path = next((tmp_path / "raw").glob("*.json"))
    assert "X-Secret" not in meta_path.read_text()
    rows = [json.loads(line) for line in (tmp_path / "raw/request_log.jsonl").read_text().splitlines()]
    assert len(rows) == 1


def test_contact_is_required(tmp_path):
    contact = tmp_path / "contact.txt"
    contact.write_text("  \n")
    with pytest.raises(ValueError, match="contact"):
        WikiHttpClient(tmp_path / "raw", contact)


def test_nonretryable_error_is_archived_once(tmp_path, monkeypatch):
    c = client(tmp_path)
    response = FakeResponse()
    response.status_code = 403
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return response

    monkeypatch.setattr(c.session, "get", fake_get)
    with pytest.raises(CrawlError, match="non-retryable HTTP 403"):
        c.get("https://example.invalid/robots.txt", purpose="policy")
    assert len(calls) == 1
    assert len(list((tmp_path / "raw/attempts").glob("*.body"))) == 1
    assert len(list((tmp_path / "raw/attempts").glob("*.json"))) == 1
