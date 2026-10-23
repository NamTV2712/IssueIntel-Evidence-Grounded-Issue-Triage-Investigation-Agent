"""Real excerpt comparison UI, revision binding and separate source reports."""
import hashlib
import json
import os
from pathlib import Path

import pytest

from test_workbench_e2e import expect,pytestmark,visit,create_from_form,displayed
from test_versions import commit


@pytest.mark.parametrize('width,language,theme',[(1280,'en','light'),(390,'vi','dark')])
def test_cited_excerpt_moves_without_rebinding_old_report(browser_page,browser_backend,tmp_path,width,language,theme):
    page,backend=browser_page,browser_backend
    page.set_viewport_size({'width':width,'height':844})
    visit(page,backend,language=language,theme=theme)
    source=create_from_form(page,backend,'Synthetic parser.parse_list(None) fails in the caller.')
    assert backend.await_job(source)['status']=='completed'
    displayed(page,source)
    original=backend.wb.result_bytes(source)
    source_sha=hashlib.sha256(original).hexdigest()
    assert backend.transport.call_count==2
    parser=backend.repository/'parser.py'
    parser.write_text('# later revision shifts the same excerpt\n\n'+parser.read_text(encoding='utf-8'),encoding='utf-8')
    target=commit(backend.repository)
    expect(page.locator('#versions-panel')).to_be_visible()
    page.locator('#versions-panel > summary').click()
    page.locator('#versions-revision').fill(target)
    page.locator('#versions-reference').fill(backend.revision)
    with page.expect_response(lambda response:response.url==backend.base_url+'/api/versions'
                              and response.request.method=='POST') as acknowledgement:
        page.locator('#versions-submit').press('Enter')
    assert acknowledgement.value.status==202
    submitted=acknowledgement.value.request.post_data_json
    assert submitted['job_id']==source and submitted['source_pack_sha256']==source_sha
    identifier=acknowledgement.value.json()['id']
    assert backend.await_job(identifier)['status']=='completed'
    report=json.loads(backend.wb.versions_bytes(identifier));impact=report['evidence_impact']
    assert impact['counts']['moved']>=1 and impact['fix_status']=='unverified'
    expect(page.locator('#evidence-impact-summary')).to_be_visible()
    page.locator('#evidence-impact-filter').select_option('moved')
    entries=page.locator('#evidence-impact-items [data-evidence-impact-id]')
    expect(entries).to_have_count(impact['counts']['moved'])
    entry=entries.first
    entry.locator('summary').first.press('Enter')
    expect(entry).to_contain_text(backend.revision)
    expect(entry).to_contain_text(target)
    expect(entry).to_contain_text('def parse_list')
    moved=next(row for row in impact['entries'] if row['status']=='moved')
    for hypothesis in moved['hypotheses']['support_ids']:
        expect(entry).to_contain_text(hypothesis)
    for format in ('json','md'):
        with page.expect_download() as downloaded:
            page.locator('#evidence-impact-'+format).press('Enter')
        target_file=tmp_path/('impact.'+format)
        downloaded.value.save_as(target_file)
        if format=='json':assert json.loads(target_file.read_text(encoding='utf-8'))==impact
        else:
            text=target_file.read_text(encoding='utf-8')
            assert 'Fix status: unverified' in text and source_sha in text
    if os.environ.get('ISSUEINTEL_IMPACT_UI_QA')=='1':
        folder=Path(__file__).resolve().parents[2]/'artifacts/maintenance/evidence-impact-ui'
        folder.mkdir(parents=True,exist_ok=True)
        page.locator('#versions-panel').screenshot(path=str(folder/('impact-'+str(width)+'.png')))
    page.locator('#language').select_option('vi' if language=='en' else 'en')
    expect(page.locator('#evidence-impact-filter')).to_have_value('moved')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert backend.transport.call_count==2 and backend.wb.result_bytes(source)==original


def test_owned_followup_exposes_version_inspection_without_new_model_calls(browser_page,browser_backend):
    page,backend=browser_page,browser_backend
    parent=backend.submit(backend.local_request('Synthetic parser source parent.'))
    child=backend.await_job(backend.wb.submit({'mode':'followup','parent_job_id':parent['id'],
        'additional_context':'Synthetic caller observation, not independently verified.'})['id'])
    assert child['status']=='completed'
    assert backend.wb.intake(child['id'])['versions_available']
    before=backend.transport.call_count
    visit(page,backend)
    from test_workbench_e2e import open_saved
    open_saved(page,backend,child)
    expect(page.locator('#versions-panel')).to_be_visible()
    page.locator('#versions-panel > summary').click()
    page.locator('#versions-revision').fill(backend.revision)
    with page.expect_response(lambda response:response.url==backend.base_url+'/api/versions'
                              and response.request.method=='POST') as acknowledgement:
        page.locator('#versions-submit').click()
    identifier=acknowledgement.value.json()['id']
    assert backend.await_job(identifier)['status']=='completed'
    expect(page.locator('#evidence-impact-summary')).to_be_visible()
    assert json.loads(backend.wb.versions_bytes(identifier))['source']['job_id']==child['id']
    assert backend.transport.call_count==before
