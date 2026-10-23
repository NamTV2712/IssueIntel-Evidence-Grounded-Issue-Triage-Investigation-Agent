"""Shared isolated backend and Chromium lifecycle for all browser test modules."""
import os
from pathlib import Path
import re
import sys
import uuid

import pytest


# Existing synthetic response helpers are test modules, never product/data input.
_TESTS = str(Path(__file__).resolve().parents[1])
if _TESTS not in sys.path:
    sys.path.insert(0, _TESTS)

from support import BrowserBackend


@pytest.fixture(scope='session')
def chromium():
    try:
        import playwright.sync_api as pw
    except ImportError as error:
        raise RuntimeError('Enabled browser E2E requires pip install -e ".[dev,browser]"') from error
    with pytest.MonkeyPatch.context() as environment:
        for name in list(os.environ):
            if re.search('TOKEN|SECRET|API_KEY|PRIVATE_KEY', name, re.I):
                environment.delenv(name)
        with pw.sync_playwright() as driver:
            browser = driver.chromium.launch(headless=True, args=['--disable-background-networking',
                '--disable-component-update', '--no-first-run'])
            try:
                yield browser
            finally:
                browser.close()


@pytest.fixture
def browser_page(chromium, browser_backend, request):
    context = chromium.new_context(accept_downloads=True, locale='en-US',
        viewport={'width': 1280, 'height': 800}, color_scheme='light')
    rejected, errors = [], []
    def network(route):
        if route.request.url.startswith(browser_backend.base_url + '/'):
            route.continue_()
        else:
            rejected.append(route.request.url)
            route.abort('blockedbyclient')
    context.route('**/*', network)
    page = context.new_page()
    page.set_default_timeout(15_000)
    page.on('pageerror', lambda error: errors.append(str(error)))
    context.tracing.start(screenshots=True, snapshots=True, sources=False)
    failed_before = request.session.testsfailed
    try:
        yield page
    finally:
        if request.session.testsfailed > failed_before:
            folder = Path(__file__).resolve().parents[2] / 'artifacts/browser-tests' / (
                re.sub(r'[^a-zA-Z0-9_-]', '_', request.node.name) + '-' + uuid.uuid4().hex[:8])
            folder.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(folder / 'failure.png'), full_page=True)
            context.tracing.stop(path=str(folder / 'trace.zip'))
        else:
            context.tracing.stop()
        context.close()
        assert not rejected, 'The browser attempted outbound requests: ' + repr(rejected)
        assert not errors, 'Uncaught browser errors: ' + repr(errors)


@pytest.fixture
def browser_backend(tmp_path, monkeypatch):
    backend = BrowserBackend(tmp_path / 'synthetic-browser-root', monkeypatch)
    try:
        yield backend
    finally:
        backend.close()
