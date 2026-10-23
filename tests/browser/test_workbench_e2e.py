"""Real Chromium -> local HTTP -> shared pipeline, with synthetic native receipts."""
from pathlib import Path
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
import zipfile

import pytest

BROWSER_ENABLED = os.environ.get('ISSUEINTEL_BROWSER_TESTS') == '1'
pw = None
if BROWSER_ENABLED:
    try:
        import playwright.sync_api as pw
    except ImportError as error:
        raise RuntimeError('Enabled browser E2E requires pip install -e ".[dev,browser]"') from error
pytestmark = [pytest.mark.browser, pytest.mark.skipif(not BROWSER_ENABLED,
    reason='Set ISSUEINTEL_BROWSER_TESTS=1 for synthetic Chromium E2E')]


def expect(actual):
    return pw.expect(actual)


@pytest.fixture(scope='session')
def chromium():
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
        url = route.request.url
        if url.startswith(browser_backend.base_url + '/'):
            route.continue_()
        else:
            rejected.append(url)
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
            folder.mkdir(parents=True)
            page.screenshot(path=str(folder / 'failure.png'), full_page=True)
            context.tracing.stop(path=str(folder / 'trace.zip'))
        else:
            context.tracing.stop()
        context.close()
        assert not rejected, 'The browser attempted outbound requests: ' + repr(rejected)
        assert not errors, 'Uncaught browser errors: ' + repr(errors)


def visit(page, backend, *, language='en', theme='light'):
    page.goto(backend.base_url + '/')
    page.locator('#language').select_option(language)
    page.locator('#theme').select_option(theme)
    expect(page.locator('#accepted-count')).to_have_text('0')


def create_from_form(page, backend, text, *, generated=True, depth='quick', keyboard=False):
    page.locator('#mode').select_option('local')
    page.locator('#repository').fill(str(backend.repository))
    page.locator('#revision').fill(backend.revision)
    page.locator('#issue-text').fill(text)
    page.locator('#depth').select_option(depth)
    page.locator('#use-groq').set_checked(generated)
    with page.expect_response(lambda response: response.url == backend.base_url + '/api/investigations'
                              and response.request.method == 'POST') as acknowledgement:
        if keyboard:
            page.locator('#use-groq').focus()
            page.keyboard.press('Tab')
            expect(page.locator('#run-button')).to_be_focused()
            page.keyboard.press('Enter')
        else:
            page.locator('#run-button').click()
    row = acknowledgement.value.json()
    assert re.fullmatch('[a-f0-9]{24}', row['id'])
    return row['id']


def displayed(page, identifier):
    expect(page.locator('#viewed-job')).to_contain_text(identifier)
    expect(page.locator('#exports')).to_be_visible()


def open_saved(page, backend, row):
    matching = page.locator('#history-list > button').filter(has_text=row.get('case_id') or row['id'])
    if matching.count() == 1:
        matching.click()
        if row['status'] == 'completed':
            displayed(page, row['id'])
        return
    rows = backend.wb.history()
    index = next(index for index, item in enumerate(rows) if item['id'] == row['id'])
    expect(page.locator('#history-list > button')).to_have_count(len(rows))
    page.locator('#history-list > button').nth(index).click()
    if row['status'] == 'completed':
        displayed(page, row['id'])


@pytest.mark.parametrize('width,language,theme', [(1280, 'en', 'light'), (390, 'vi', 'dark')])
def test_create_followup_and_portable_handoff(browser_page, browser_backend, tmp_path, width, language, theme):
    page, backend = browser_page, browser_backend
    page.set_viewport_size({'width': width, 'height': 844})
    visit(page, backend, language=language, theme=theme)
    parent_id = create_from_form(page, backend, 'Synthetic parser.parse_list crashes for None input.', keyboard=True)
    parent = backend.await_job(parent_id)
    assert parent['status'] == 'completed'
    displayed(page, parent_id)
    original = backend.wb.result_bytes(parent_id)
    assert backend.transport.call_count == 2
    page.locator('[data-tab="evidence"]').click()
    expect(page.locator('#tab-content')).to_contain_text('parser.py')
    page.locator('#additional-context').fill('Synthetic caller supplies None; this observation is unverified.')
    with page.expect_response(lambda response: response.url == backend.base_url + '/api/investigations'
                              and response.request.method == 'POST') as followup:
        page.locator('#followup-button').press('Enter')
    child_id = followup.value.json()['id']
    child = backend.await_job(child_id)
    assert child['status'] == 'completed'
    displayed(page, child_id)
    pack = json.loads(backend.wb.result_bytes(child_id))
    assert pack['followup']['parent_job_id'] == parent_id
    assert pack['snapshot']['commit_sha'] == backend.revision
    assert backend.transport.call_count == 2 and backend.wb.result_bytes(parent_id) == original
    page.locator('[data-tab="dossier"]').click()
    page.get_by_text(re.compile('^(Offline handoff bundle|Gói bàn giao ngoại tuyến)$')).click()
    page.get_by_role('button', name=re.compile('Preview contents|Xem trước nội dung')).click()
    digest_text = page.get_by_text(re.compile('^ZIP SHA256:')).inner_text()
    expected = re.search('[a-f0-9]{64}', digest_text).group()
    with page.expect_download() as downloaded:
        page.get_by_role('button', name=re.compile('Confirm export|Xác nhận xuất')).press('Enter')
    archive = tmp_path / 'recipient.zip'
    downloaded.value.save_as(archive)
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == expected
    with zipfile.ZipFile(archive) as bundle:
        selection = json.loads(bundle.read('handoff.json'))
        assert {run['job_id'] for run in selection['runs']} == {parent_id, child_id}
        assert selection['accounting'][0]['bundle']['lineage']['providers']['groq']['reservations'] == 2
        verifier = tmp_path / 'verify.py'
        verifier.write_bytes(bundle.read('verify.py'))
    verified = subprocess.run([sys.executable, '-I', str(verifier), str(archive), '--expected-sha256', expected],
        cwd=tmp_path, capture_output=True, text=True)
    assert verified.returncode == 0, verified.stderr
    assert json.loads(verified.stdout)['integrity'] == 'consistent'
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert backend.transport.call_count == 2


def test_stage_recovery_reuses_saved_generation_and_keyboard_consent(browser_page, browser_backend):
    page, backend = browser_page, browser_backend
    visit(page, backend)
    backend.fail_after_stage('seed_generation')
    source_id = create_from_form(page, backend, 'Synthetic stage interruption after parser hypotheses.')
    source = backend.await_job(source_id)
    assert source['status'] == 'failed' and backend.transport.call_count == 1
    original = backend.wb._job_path(source_id).read_bytes()
    expect(page.locator('#run-button')).to_be_enabled()
    page.locator('#stage-recovery-panel > summary').click()
    page.get_by_role('button', name='Check stages for recovery', exact=True).press('Enter')
    start = page.get_by_role('button', name='Continue missing work', exact=True)
    expect(start).to_be_disabled()
    expect(page.locator('#stage-recovery-content')).to_contain_text('Initial hypotheses')
    expect(page.locator('#stage-recovery-content')).to_contain_text('Next missing step: Verification')
    page.locator('#stage-recovery-consent').press('Space')
    expect(page.locator('#stage-recovery-consent')).to_be_focused()
    expect(start).to_be_enabled()
    with page.expect_response(lambda response: response.url.endswith('/stage-recovery')
                              and response.request.method == 'POST') as recovery:
        start.press('Enter')
    child_id = recovery.value.json()['id']
    assert backend.await_job(child_id)['status'] == 'completed'
    displayed(page, child_id)
    assert backend.transport.calls == [{'kind': 'generation', 'index': 1}, {'kind': 'verifier', 'index': 2}]
    assert backend.wb._job_path(source_id).read_bytes() == original
    accounting = backend.wb.investigation_usage(child_id)
    assert accounting['current']['providers']['groq']['reservations'] == 1
    assert accounting['lineage']['providers']['groq']['reservations'] == 2


def test_export_finalization_keeps_paid_parent_without_new_calls(browser_page, browser_backend):
    page, backend = browser_page, browser_backend
    visit(page, backend)
    backend.fail_next_export()
    source_id = create_from_form(page, backend, 'Synthetic parser.parse_list report export interruption.')
    assert backend.await_job(source_id)['status'] == 'failed'
    expect(page.locator('#run-button')).to_be_enabled()
    calls = backend.transport.call_count
    assert calls == 2
    page.locator('#finalization-panel > summary').click()
    page.get_by_role('button', name='Check checkpoints for finalization', exact=True).press('Enter')
    with page.expect_response(lambda response: response.url.endswith('/finalization')
                              and response.request.method == 'POST') as finalization:
        page.get_by_role('button', name='Finalize report from saved checkpoints', exact=True).press('Enter')
    child_id = finalization.value.json()['id']
    assert backend.await_job(child_id)['status'] == 'completed'
    displayed(page, child_id)
    page.locator('[data-tab="usage"]').click()
    expect(page.locator('#tab-content .investigation-usage')).to_contain_text('Full investigation chain')
    projection = backend.wb.investigation_usage(child_id)
    assert projection['current']['complete_micro_usd_charged_or_reserved'] == 0
    assert projection['lineage']['complete_micro_usd_charged_or_reserved'] > 0
    assert backend.transport.call_count == calls


def test_delayed_real_pack_response_cannot_replace_selected_run(browser_page, browser_backend):
    page, backend = browser_page, browser_backend
    first = backend.submit(backend.local_request('Synthetic first parser issue.'))
    second = backend.submit(backend.local_request('Synthetic second parser issue.'))
    visit(page, backend)
    held = []
    def delay(route):
        held.append(route)
    page.route(backend.base_url + '/api/jobs/' + first['id'] + '/pack.json', delay)
    rows = backend.wb.history()
    index = next(index for index, row in enumerate(rows) if row['id'] == first['id'])
    expect(page.locator('#history-list > button')).to_have_count(2)
    with page.expect_request(backend.base_url + '/api/jobs/' + first['id'] + '/pack.json'):
        page.locator('#history-list > button').nth(index).click()
    assert held, 'The real first pack response must be held before changing selection'
    open_saved(page, backend, second)
    route = held.pop()
    finished = {}
    paths = {'intake', 'plan', 'usage', 'investigation-report.json', 'hypothesis-review.json', 'check-journal.json'}
    prefix = backend.base_url + '/api/jobs/' + first['id'] + '/'
    def all_stale_reads(response):
        if response.url.startswith(prefix) and response.url[len(prefix):] in paths:
            finished[response.url] = response
        return len(finished) == len(paths)
    with page.expect_response(all_stale_reads):
        route.fulfill(response=route.fetch())
    for response in finished.values():
        response.finished()
    page.unroute(backend.base_url + '/api/jobs/' + first['id'] + '/pack.json', delay)
    # There are no active jobs/pollers in this fixture. Wait for all stale body
    # handlers and their Promise microtasks before inspecting the retained view.
    page.wait_for_load_state('networkidle')
    expect(page.locator('#viewed-job')).to_contain_text(second['id'])
    expect(page.locator('#result-title')).to_have_text(second['case_id'])
    page.locator('[data-tab="evidence"]').click()
    expect(page.locator('#tab-content')).to_contain_text('Synthetic second parser issue.')
    page.locator('[data-tab="usage"]').click()
    expect(page.locator('#tab-content .investigation-usage')).to_contain_text(second['id'])
    assert backend.transport.call_count == 4


def test_running_job_progress_does_not_relabel_open_saved_report(browser_page, browser_backend):
    page, backend = browser_page, browser_backend
    saved = backend.submit(backend.local_request('Synthetic saved parser issue.'))
    visit(page, backend)
    backend.transport.block_next()
    active_id = create_from_form(page, backend, 'Synthetic blocked parser investigation.')
    backend.transport.wait_blocked()
    open_saved(page, backend, saved)
    expect(page.locator('#message')).to_contain_text(saved['id'])
    selected_message = page.locator('#message').inner_text()
    backend.transport.release()
    assert backend.await_job(active_id)['status'] == 'completed'
    expect(page.locator('#run-button')).to_be_enabled()
    expect(page.locator('#viewed-job')).to_contain_text(saved['id'])
    expect(page.locator('#message')).to_have_text(selected_message)
    assert backend.transport.call_count == 4


def test_unsaved_checklist_and_followup_drafts_are_source_bound(browser_page, browser_backend):
    page, backend = browser_page, browser_backend
    first = backend.submit(backend.local_request('Synthetic draft owner one parser issue.'))
    second = backend.submit(backend.local_request('Synthetic draft owner two parser issue.'))
    visit(page, backend)
    open_saved(page, backend, first)
    page.locator('#additional-context').fill('Private synthetic draft A, not submitted.')
    page.locator('#followup-depth').select_option('deep')
    page.locator('#followup-groq').check()
    page.locator('[data-tab="plan"]').click()
    page.locator('.journal-checklist > summary').click()
    note = page.locator('form[data-plan-step] textarea').first
    note.fill('Synthetic unsaved checklist note A.')
    page.locator('[data-tab="evidence"]').click()
    page.locator('[data-tab="plan"]').click()
    expect(page.locator('form[data-plan-step] textarea').first).to_be_visible()
    expect(page.locator('form[data-plan-step] textarea').first).to_have_value('Synthetic unsaved checklist note A.')
    page.locator('#language').select_option('vi')
    expect(page.locator('form[data-plan-step] textarea').first).to_be_visible()
    expect(page.locator('form[data-plan-step] textarea').first).to_have_value('Synthetic unsaved checklist note A.')
    open_saved(page, backend, second)
    expect(page.locator('#additional-context')).to_have_value('')
    expect(page.locator('#followup-groq')).not_to_be_checked()
    expect(page.locator('form[data-plan-step] textarea').first).to_have_value('')
    page.locator('#additional-context').fill('Synthetic draft B.')
    open_saved(page, backend, first)
    expect(page.locator('#additional-context')).to_have_value('Private synthetic draft A, not submitted.')
    expect(page.locator('#followup-depth')).to_have_value('deep')
    expect(page.locator('#followup-groq')).to_be_checked()
    expect(page.locator('form[data-plan-step] textarea').first).to_have_value('Synthetic unsaved checklist note A.')
    assert backend.transport.call_count == 4
    assert len(backend.wb.history()) == 2


def test_clarification_answers_return_to_exact_source_without_cost_consent(browser_page, browser_backend):
    page, backend = browser_page, browser_backend
    first = backend.submit(backend.local_request('Synthetic parser issue needs more details A.'))
    second = backend.submit(backend.local_request('Synthetic parser issue needs more details B.'))
    visit(page, backend)
    open_saved(page, backend, first)
    page.locator('#clarifications-panel > summary').click()
    answer = page.locator('#clarifications-questions textarea').first
    field_id = answer.get_attribute('id')
    answer.fill('Synthetic answer owned only by issue A.')
    page.locator('#clarifications-depth').select_option('deep')
    page.locator('#clarifications-groq').check()
    page.locator('#language').select_option('vi')
    expect(page.locator('#' + field_id)).to_have_value('Synthetic answer owned only by issue A.')
    expect(page.locator('#clarifications-groq')).not_to_be_checked()
    open_saved(page, backend, second)
    expect(page.locator('#' + field_id)).to_have_value('')
    page.locator('#' + field_id).fill('Synthetic answer B.')
    open_saved(page, backend, first)
    expect(page.locator('#' + field_id)).to_have_value('Synthetic answer owned only by issue A.')
    expect(page.locator('#clarifications-depth')).to_have_value('deep')
    expect(page.locator('#clarifications-groq')).not_to_be_checked()
    assert backend.transport.call_count == 4 and len(backend.wb.history()) == 2


def test_observation_draft_survives_tabs_and_plan_owner_changes(browser_page, browser_backend):
    from test_check_plan_jobs import plan_request
    page, backend = browser_page, browser_backend
    first = backend.submit(backend.local_request('Synthetic parser observation source A.'))
    second = backend.submit(backend.local_request('Synthetic parser observation source B.'))
    plans = {}
    for row in (first, second):
        plan = backend.await_job(backend.wb.submit_check_plan(plan_request(backend.wb, row))['id'])
        assert plan['status'] == 'completed', plan
        plans[row['id']] = plan
    visit(page, backend)
    def open_plan(row):
        open_saved(page, backend, row)
        page.locator('[data-tab="plan"]').click()
        page.get_by_role('button', name=re.compile('^(Open saved check plans|Mở các kế hoạch kiểm tra đã lưu)$')).click()
        page.get_by_role('button', name=re.compile('^(Open check plan|Mở kế hoạch) ' + plans[row['id']]['id'] + '$')).click()
        expect(page.locator('#check-observation-interpretation')).to_be_visible()
    open_plan(first)
    page.locator('#check-observation-interpretation').fill('Synthetic observation draft A, not run or submitted.')
    page.locator('#check-observation-depth').select_option('deep')
    page.locator('[data-tab="evidence"]').click()
    page.locator('[data-tab="plan"]').click()
    expect(page.locator('#check-observation-interpretation')).to_have_value('Synthetic observation draft A, not run or submitted.')
    open_plan(second)
    expect(page.locator('#check-observation-interpretation')).to_have_value('')
    page.locator('#check-observation-interpretation').fill('Synthetic observation draft B.')
    open_plan(first)
    expect(page.locator('#check-observation-interpretation')).to_have_value('Synthetic observation draft A, not run or submitted.')
    expect(page.locator('#check-observation-depth')).to_have_value('deep')
    page.locator('#language').select_option('vi')
    expect(page.locator('#check-observation-interpretation')).to_have_value('Synthetic observation draft A, not run or submitted.')
    assert all(backend.wb.check_observations(plan['id'])['revision'] == 0 for plan in plans.values())
    assert backend.transport.call_count == 6
