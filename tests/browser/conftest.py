"""Backend fixtures only; Playwright/browser lifecycle belongs to browser tests."""
from pathlib import Path
import sys

import pytest


# Existing synthetic response helpers are test modules, never product/data input.
_TESTS = str(Path(__file__).resolve().parents[1])
if _TESTS not in sys.path:
    sys.path.insert(0, _TESTS)

from support import BrowserBackend


@pytest.fixture
def browser_backend(tmp_path, monkeypatch):
    backend = BrowserBackend(tmp_path / 'synthetic-browser-root', monkeypatch)
    try:
        yield backend
    finally:
        backend.close()
