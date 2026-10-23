"""Batch UI -> bound HTTP intent -> the shared synthetic product pipeline."""
import json
import os
from pathlib import Path
import time

import pytest

from test_workbench_e2e import expect, pytestmark, visit


def manifest(backend, count, *, provider='none', run_key='browser-batch'):
    return {'schema': 'product_batch_manifest_v1', 'run_key': run_key, 'items': [
        {'key': 'item-'+str(index), 'request': {'mode': 'local', 'repository': str(backend.repository),
            'revision': 'HEAD', 'provider': provider, 'depth': 'quick',
            'issue_text': 'Synthetic parser.parse_list crashes for None input '+str(index)}}
        for index in range(1, count+1)]}


def preview(page, backend, value):
    page.locator('#batch-manifest').fill(json.dumps(value))
    with page.expect_response(lambda response: response.url == backend.base_url+'/api/batches/preview'
                              and response.request.method == 'POST') as response:
        page.locator('#batch-preview').press('Enter')
    captured = response.value.json()
    assert response.value.status == 200 and captured['can_start'] and captured['model_calls'] == 0, captured
    return captured


def start(page, backend):
    with page.expect_response(lambda response: response.url == backend.base_url+'/api/batches'
                              and response.request.method == 'POST') as response:
        page.locator('#batch-start').press('Enter')
    value = response.value.json()
    assert response.value.status == 202, value
    return value


def finished(backend, identifier):
    deadline = time.monotonic()+30
    while time.monotonic() < deadline:
        value = backend.wb.batch(identifier)
        if all(item['status'] in {'completed', 'failed', 'cancelled', 'interrupted'} for item in value['items']):
            return value
        time.sleep(.02)
    pytest.fail('Synthetic browser batch did not reach terminal items')


@pytest.mark.parametrize('width,language,theme', [(1280, 'en', 'light'), (390, 'vi', 'dark')])
def test_preview_edit_serial_batch_and_open_child(browser_page, browser_backend, width, language, theme):
    page, backend = browser_page, browser_backend
    page.set_viewport_size({'width': width, 'height': 844})
    visit(page, backend, language=language, theme=theme)
    page.locator('#batch-panel > summary').click()
    value = manifest(backend, 6)
    captured = preview(page, backend, value)
    assert not backend.wb.history() and backend.transport.call_count == 0
    assert all(item['source']['revision'] == backend.revision for item in captured['items'])
    expect(page.locator('#batch-preview-content')).to_contain_text(backend.revision)
    value['items'][0]['request']['issue_text'] += ' Edited before approval.'
    page.locator('#batch-manifest').fill(json.dumps(value))
    page.wait_for_function('!document.getElementById("batch-start") || document.getElementById("batch-start").disabled')
    assert not backend.wb.history() and backend.transport.call_count == 0
    preview(page, backend, value)
    accepted = start(page, backend)
    saved = finished(backend, accepted['id'])
    assert len(saved['items']) == 6 and all(item['status'] == 'completed' for item in saved['items']), saved
    assert len({item['job_id'] for item in saved['items']}) == 6
    expect(page.locator('#batch-detail [data-batch-key]')).to_have_count(6)
    expect(page.locator('#batch-detail')).to_have_attribute('data-batch-status', 'completed')
    expect(page.locator('#batch-history-list [data-batch-id="'+saved['id']+'"]')).to_have_attribute('data-batch-status', 'completed')
    expect(page.locator('#batch-approved-preview')).to_be_visible()
    assert page.locator('#batch-approved-preview').get_attribute('open') is None
    child = saved['items'][-1]
    expect(page.locator('#batch-item-open-'+child['key'])).to_be_enabled()
    page.locator('#batch-item-open-'+child['key']).press('Enter')
    expect(page.locator('#viewed-job')).to_contain_text(child['job_id'])
    expect(page.locator('#exports')).to_be_visible()
    if os.environ.get('ISSUEINTEL_BATCH_UI_QA') == '1':
        folder = Path(__file__).resolve().parents[2]/'artifacts/maintenance/batch-ui'
        folder.mkdir(parents=True, exist_ok=True)
        page.locator('#batch-panel').screenshot(path=str(folder/('batch-'+str(width)+'.png')))
    # Changing locale renders the saved batch without a second execution.
    page.locator('#language').select_option('vi' if language == 'en' else 'en')
    expect(page.locator('#viewed-job')).to_contain_text(child['job_id'])
    assert len(backend.wb.history()) == 6 and backend.transport.call_count == 0
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


def test_paid_batch_requires_consent_and_retains_partial_success(browser_page, browser_backend):
    page, backend = browser_page, browser_backend
    visit(page, backend)
    page.locator('#batch-panel > summary').click()
    captured = preview(page, backend, manifest(backend, 2, provider='groq', run_key='paid-partial'))
    assert captured['requires_api'] and captured['cost_bound_micro_usd'] is None
    expect(page.locator('#batch-start')).to_be_disabled()
    assert backend.transport.call_count == 0 and not backend.wb.history()
    backend.transport.fail_next('generation')
    page.locator('#batch-groq-consent').focus()
    page.keyboard.press('Space')
    expect(page.locator('#batch-groq-consent')).to_be_checked()
    accepted = start(page, backend)
    saved = finished(backend, accepted['id'])
    assert [item['status'] for item in saved['items']] == ['failed', 'completed'], saved
    assert backend.transport.call_count == 3
    failed, succeeded = [backend.wb.job(item['job_id']) for item in saved['items']]
    assert failed['accounting']['reservations'] == 1 and succeeded['accounting']['reservations'] == 2
    identifiers = [item['job_id'] for item in saved['items']]
    page.locator('#batch-history-refresh').click()
    expect(page.locator('#batch-item-open-item-2')).to_be_enabled()
    page.locator('#batch-item-open-item-2').click()
    expect(page.locator('#viewed-job')).to_contain_text(identifiers[1])
    assert backend.transport.call_count == 3 and len(backend.wb.history()) == 2


def test_cancel_pending_item_does_not_spend_its_calls(browser_page, browser_backend):
    page, backend = browser_page, browser_backend
    visit(page, backend)
    page.locator('#batch-panel > summary').click()
    preview(page, backend, manifest(backend, 3, provider='groq', run_key='cancel-pending'))
    backend.transport.block_next('generation')
    page.locator('#batch-groq-consent').check()
    try:
        accepted = start(page, backend)
        backend.transport.wait_blocked()
        with page.expect_response(lambda response: response.url.endswith('/items/item-2/cancel')
                                  and response.request.method == 'POST') as cancellation:
            page.locator('#batch-item-cancel-item-2').press('Enter')
        assert cancellation.value.status == 200
        backend.transport.release()
        saved = finished(backend, accepted['id'])
        assert [item['status'] for item in saved['items']] == ['completed', 'cancelled', 'completed'], saved
        assert backend.transport.call_count == 4
        cancelled = saved['items'][1]
        # A cancellation before preparation may reserve only its fixed ID;
        # there need not be a materialized job or a usage receipt.
        if backend.wb._job_path(cancelled['job_id']).is_file():
            row = backend.wb.job(cancelled['job_id'])
            assert not row.get('accounting', {}).get('reservations', 0)
        expect(page.locator('#batch-item-open-item-3')).to_be_enabled()
    finally: backend.transport.release()


def test_delayed_batch_read_cannot_replace_newer_selection(browser_page, browser_backend):
    page, backend = browser_page, browser_backend
    batches = []
    for count, run_key in ((2, 'saved-batch-A'), (1, 'saved-batch-B')):
        captured = backend.wb.batch_preview(manifest(backend, count, run_key=run_key))
        accepted = backend.wb.batch_submit({'manifest': captured['manifest'], 'preview': captured,
            'confirmed': True, 'allow_api_calls': False})
        batches.append(finished(backend, accepted['id']))
    first, second = batches
    visit(page, backend)
    page.locator('#batch-panel > summary').click()
    expect(page.locator('#batch-history-list [data-batch-id]')).to_have_count(2)
    held = {}
    first_url = backend.base_url+'/api/batches/'+first['id']
    def hold(route):
        # Capture the real response and let the later request complete first.
        held['response'] = route.fetch()
        held['route'] = route
    page.route(first_url, hold)
    try:
        with page.expect_request(first_url):
            page.locator('#batch-history-list [data-batch-id="'+first['id']+'"]').click()
        with page.expect_response(backend.base_url+'/api/batches/'+second['id']):
            page.locator('#batch-history-list [data-batch-id="'+second['id']+'"]').click()
        expect(page.locator('#batch-detail')).to_have_attribute('data-batch-id', second['id'])
        expect(page.locator('#batch-detail [data-batch-key]')).to_have_count(1)
        assert 'route' in held
        held['route'].fulfill(response=held['response'])
        held.clear()
        page.wait_for_load_state('networkidle')
        expect(page.locator('#batch-detail')).to_have_attribute('data-batch-id', second['id'])
        expect(page.locator('#batch-detail')).to_contain_text(second['id'])
        expect(page.locator('#batch-detail [data-batch-key]')).to_have_count(1)
        page.locator('#batch-item-open-item-1').click()
        expect(page.locator('#viewed-job')).to_contain_text(second['items'][0]['job_id'])
        assert backend.transport.call_count == 0 and len(backend.wb.history()) == 3
    finally:
        if 'route' in held: held['route'].fulfill(response=held['response'])
        page.unroute(first_url, hold)
