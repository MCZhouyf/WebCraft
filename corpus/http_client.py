"""Policy-aware, rate-limited HTTP client for Minecraft Wiki acquisition."""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE = ROOT / "corpus/raw"
FANDOM_API_URL = "https://minecraft.fandom.com/api.php"
FANDOM_ROBOTS_URL = "https://minecraft.fandom.com/robots.txt"


class CrawlError(RuntimeError):
    """A request failed after the configured retries."""


@dataclass(frozen=True)
class CachedResponse:
    url: str
    status_code: int
    headers: dict[str, str]
    body: bytes
    elapsed_seconds: float
    cache_hit: bool
    fetched_at: str

    def json(self) -> Any:
        return json.loads(self.body)


class WikiHttpClient:
    def __init__(
        self,
        cache_dir: Path = DEFAULT_CACHE,
        contact_path: Path = ROOT / "env/crawl_contact.txt",
        api_url: str = FANDOM_API_URL,
        min_interval: float = 1.0,
        max_retries: int = 4,
        timeout: float = 30.0,
    ) -> None:
        contact = contact_path.read_text(encoding="utf-8").strip()
        if not contact:
            raise ValueError("crawl contact is empty")
        self.user_agent = (
            "WebCraftResearch/0.1 (academic research; contact: " + contact + ")"
        )
        self.api_url = api_url
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.cache_dir / "request_log.jsonl"
        self.min_interval = max(1.0, float(min_interval))
        self.max_retries = max_retries
        self.timeout = timeout
        self._last_request_wall = 0.0
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.user_agent})

    @staticmethod
    def _canonical_url(url: str, params: dict[str, Any] | None) -> str:
        if not params:
            return url
        pairs = []
        for key, value in sorted(params.items()):
            if isinstance(value, (list, tuple)):
                pairs.extend((key, str(part)) for part in value)
            else:
                pairs.append((key, str(value)))
        return f"{url}?{urlencode(pairs)}"

    def _paths(self, canonical_url: str) -> tuple[Path, Path]:
        digest = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.body", self.cache_dir / f"{digest}.json"

    @staticmethod
    def _assert_allowed(url: str, params: dict[str, Any] | None) -> None:
        action = str((params or {}).get("action", "")).lower()
        if action == "raw" or "action=raw" in url.lower():
            raise ValueError("action=raw is forbidden by crawl policy")

    def _read_cache(self, canonical_url: str) -> CachedResponse | None:
        body_path, meta_path = self._paths(canonical_url)
        if not body_path.exists() or not meta_path.exists():
            return None
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return CachedResponse(
            url=canonical_url,
            status_code=int(meta["status_code"]),
            headers=dict(meta.get("headers", {})),
            body=body_path.read_bytes(),
            elapsed_seconds=float(meta.get("elapsed_seconds", 0.0)),
            cache_hit=True,
            fetched_at=meta["fetched_at"],
        )

    def _wait(self) -> None:
        delay = self.min_interval - (time.time() - self._last_request_wall)
        if delay > 0:
            time.sleep(delay)

    def _record(self, row: dict[str, Any]) -> None:
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")

    def _archive_response(
        self,
        canonical_url: str,
        response: requests.Response,
        elapsed: float,
        purpose: str,
        attempt: int,
    ) -> dict[str, Any]:
        digest = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()
        fetched_at = datetime.now(timezone.utc).isoformat()
        stamp = fetched_at.replace(":", "").replace("+", "_")
        attempts_dir = self.cache_dir / "attempts"
        attempts_dir.mkdir(parents=True, exist_ok=True)
        archive_stem = attempts_dir / f"{digest}-{stamp}-a{attempt}"
        archive_stem.with_suffix(".body").write_bytes(response.content)
        metadata = {
            "url": canonical_url,
            "final_url": response.url,
            "status_code": response.status_code,
            "headers": {
                key: value
                for key, value in response.headers.items()
                if key.lower() in {"content-type", "etag", "last-modified"}
            },
            "elapsed_seconds": elapsed,
            "fetched_at": fetched_at,
            "purpose": purpose,
            "sha256": hashlib.sha256(response.content).hexdigest(),
            "attempt": attempt,
        }
        archive_stem.with_suffix(".json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return metadata

    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        *,
        purpose: str,
    ) -> CachedResponse:
        self._assert_allowed(url, params)
        effective = dict(params or {})
        if url == self.api_url:
            effective.setdefault("format", "json")
            effective.setdefault("formatversion", "2")
            effective.setdefault("maxlag", "5")
        canonical_url = self._canonical_url(url, effective)
        cached = self._read_cache(canonical_url)
        if cached is not None:
            return cached

        for attempt in range(self.max_retries + 1):
            self._wait()
            started = time.time()
            try:
                response = self.session.get(
                    url,
                    params=effective,
                    timeout=self.timeout,
                    allow_redirects=True,
                )
                elapsed = time.time() - started
                self._last_request_wall = started
                metadata = self._archive_response(
                    canonical_url, response, elapsed, purpose, attempt + 1
                )
                self._record({**metadata, "cache_hit": False})
                retryable = response.status_code in {429, 500, 502, 503, 504}
                if retryable:
                    raise requests.HTTPError(f"retryable HTTP {response.status_code}")
                if response.status_code >= 400:
                    raise CrawlError(
                        f"non-retryable HTTP {response.status_code}: {canonical_url}"
                    )
                body_path, meta_path = self._paths(canonical_url)
                body_path.write_bytes(response.content)
                meta_path.write_text(
                    json.dumps(metadata, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                return CachedResponse(
                    canonical_url,
                    response.status_code,
                    metadata["headers"],
                    response.content,
                    elapsed,
                    False,
                    metadata["fetched_at"],
                )
            except CrawlError:
                raise
            except (requests.RequestException, OSError) as error:
                self._last_request_wall = started
                if not isinstance(error, requests.HTTPError):
                    self._record(
                        {
                            "url": canonical_url,
                            "purpose": purpose,
                            "attempt": attempt + 1,
                            "cache_hit": False,
                            "ok": False,
                            "error_type": type(error).__name__,
                            "elapsed_seconds": time.time() - started,
                            "fetched_at": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                if attempt >= self.max_retries:
                    raise CrawlError(
                        f"request failed after {attempt + 1} attempts: {canonical_url}"
                    ) from error
                time.sleep(2**attempt)
        raise AssertionError("unreachable")

    def api(self, *, purpose: str, **params: Any) -> CachedResponse:
        return self.get(self.api_url, params, purpose=purpose)


def configuration_present() -> bool:
    required = {
        "WC_LLM_MODEL",
        "WC_LLM_BASE_URL",
        "WC_LLM_API_KEY",
        "WC_LLM_CURRENCY",
        "WC_LLM_PRICE_IN_PER_MTOK",
        "WC_LLM_PRICE_OUT_PER_MTOK",
        "WC_LLM_PRICE_CACHED_IN_PER_MTOK",
        "WC_LLM_BUDGET",
    }
    return all(os.environ.get(name) for name in required)
