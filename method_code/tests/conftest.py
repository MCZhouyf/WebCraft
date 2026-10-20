from pathlib import Path

import pytest

from arbiter.checkpoint import require_memory


@pytest.fixture(scope="session", autouse=True)
def resource_guard():
    # Author-authorized 2026-10-09 deterministic-backend exception.
    require_memory(512 * 1024**2, reserve_bytes=2 * 1024**3)


@pytest.fixture(scope="session")
def webcraft_root() -> Path:
    path = Path("/root/autodl-tmp/webcraft")
    if not path.exists():
        pytest.skip("webcraft sibling repository is unavailable")
    return path
