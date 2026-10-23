"""Explicit metadata inspection preserves the viewed investigation and receipts."""
import os
from pathlib import Path

import pytest

from test_workbench_e2e import expect,pytestmark,visit,create_from_form,displayed


def test_repeated_browser_asset_bursts_initialize_preferences_before_product_modules(browser_page,browser_backend):
    page,backend=browser_page,browser_backend
    for _ in range(5):
        visit(page,backend,language='en',theme='light')
        assert page.evaluate('typeof window.IssueIntelUI.t')=='function'
        assert page.locator('#accepted-count').inner_text()=='0'
    assert backend.transport.call_count==0


@pytest.mark.parametrize('width,language,theme',[(1280,'en','light'),(390,'vi','dark')])
def test_system_inspection_is_lazy_local_and_preserves_the_viewed_report(browser_page,browser_backend,monkeypatch,width,language,theme):
    page,backend=browser_page,browser_backend
    page.set_viewport_size({'width':width,'height':844})
    root=backend.root
    (root/'configs').mkdir(exist_ok=True)
    (root/'configs/runtime.toml').write_text('''provider = "groq"
model = "openai/gpt-oss-120b"
max_output_tokens = 8192
daily_total_tokens = 10000000
monthly_usd = "100"
price_file = "synthetic-prices.json"
ledger = "synthetic-budget.sqlite"
''',encoding='utf-8')
    (root/'configs/typesafe.toml').write_text('''provider = "typesafe"
model = "jev-1.13.0"
enabled = false
daily_requests = 0
daily_usd = "0"
monthly_usd = "0"
price_file = "missing-disabled-price.json"
ledger = "artifacts/typesafe.sqlite"
''',encoding='utf-8')
    web_source=root/'src/issueintel/interfaces/web.py'
    web_source.parent.mkdir(parents=True,exist_ok=True)
    web_source.write_text('# Synthetic source-completeness marker only\n',encoding='utf-8')
    preserved=root/'data/private-source/receipt.json'
    preserved.parent.mkdir(parents=True,exist_ok=True)
    preserved.write_bytes(b'{"source_receipt":"synthetic private canary"}')
    bytecode=root/'tests/__pycache__/synthetic.cpython-312.pyc'
    bytecode.parent.mkdir(parents=True,exist_ok=True)
    bytecode.write_bytes(b'synthetic disposable bytecode')
    inspection=[]
    page.on('request',lambda request:inspection.append(request.url) if request.url.endswith(('/api/readiness','/api/storage/preview')) else None)
    visit(page,backend,language=language,theme=theme)
    assert inspection==[]
    identifier=create_from_form(page,backend,'Synthetic parser issue before system inspection.')
    assert backend.await_job(identifier)['status']=='completed'
    displayed(page,identifier)
    original=backend.wb.result_bytes(identifier)
    calls=backend.transport.call_count
    page.locator('#additional-context').fill('Synthetic unsent draft remains in place.')
    with page.expect_response(lambda response:response.url==backend.base_url+'/api/readiness') as readiness:
        page.locator('#system-readiness > summary').press('Enter')
    assert readiness.value.status==200
    value=readiness.value.json()
    assert value['schema']=='product_readiness_v1' and value['live_services_verified'] is False
    expect(page.locator('#readiness-content')).to_contain_text('Groq')
    if language=='en':
        expect(page.locator('#readiness-content')).to_contain_text('Saved reports')
        expect(page.locator('#readiness-content')).to_contain_text('Compare revisions')
    assert all(not url.endswith('/api/storage/preview') for url in inspection)
    with page.expect_response(lambda response:response.url==backend.base_url+'/api/storage/preview') as preview:
        page.locator('#storage-preview').press('Enter')
    assert preview.value.status==200
    storage=preview.value.json()
    assert storage['schema']=='product_storage_preview_v1' and storage['deletion_supported'] is False
    assert any(row['path']=='tests/__pycache__' for row in storage['candidates'])
    assert not any(row['path'].startswith(('data/','artifacts/')) for row in storage['candidates'])
    expect(page.locator('#storage-content')).to_contain_text('tests/__pycache__')
    if language=='en':expect(page.locator('#storage-content')).to_contain_text('Other')
    expect(page.locator('#additional-context')).to_have_value('Synthetic unsent draft remains in place.')
    displayed(page,identifier)
    assert backend.transport.call_count==calls and backend.wb.result_bytes(identifier)==original
    assert preserved.read_bytes()==b'{"source_receipt":"synthetic private canary"}' and bytecode.is_file()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    if os.environ.get('ISSUEINTEL_READINESS_UI_QA')=='1':
        folder=Path(__file__).resolve().parents[2]/'artifacts/maintenance/readiness-ui'
        folder.mkdir(parents=True,exist_ok=True)
        page.locator('#system-readiness').screenshot(path=str(folder/('readiness-'+str(width)+'.png')))
    count=len(inspection)
    page.locator('#language').select_option('vi' if language=='en' else 'en')
    assert len(inspection)==count
    def unavailable(*args,**kwargs):raise ValueError('Synthetic inspection failure')
    monkeypatch.setattr(backend.wb,'system_readiness',unavailable)
    with page.expect_response(lambda response:response.url==backend.base_url+'/api/readiness') as failed:
        page.locator('#readiness-refresh').press('Enter')
    assert failed.value.status==409
    expect(page.locator('#readiness-content')).to_be_empty()
    expect(page.locator('#readiness-message')).not_to_be_empty()
    monkeypatch.setattr(backend.wb,'storage_preview',unavailable)
    with page.expect_response(lambda response:response.url==backend.base_url+'/api/storage/preview') as failed:
        page.locator('#storage-preview').press('Enter')
    assert failed.value.status==400
    expect(page.locator('#storage-content')).to_be_empty()
    expect(page.locator('#storage-message')).not_to_be_empty()
    expect(page.locator('#additional-context')).to_have_value('Synthetic unsent draft remains in place.')
    assert backend.transport.call_count==calls and backend.wb.result_bytes(identifier)==original
