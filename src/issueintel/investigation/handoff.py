"""Explicit, bounded exports of selected product investigations for offline use."""
from copy import deepcopy
import html
import io
import json
from pathlib import Path
import zipfile

from issueintel.runtime.io import digest
from . import portable_verify
from .report import render_markdown


def _json(value):
    return (json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode()


def _sealed(value):
    return dict(value,sha256=digest(value))


def _viewer(selection):
    esc=lambda value:html.escape(str(value),quote=True)
    body=['<!doctype html><html lang="en"><meta charset="utf-8">',
          '<meta name="viewport" content="width=device-width,initial-scale=1">',
          '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">',
          '<title>IssueIntel · Offline handoff</title>',
          '<style>body{font:15px/1.7 system-ui,sans-serif;max-width:1000px;margin:auto;padding:28px;background:#f6f8fa;color:#1f2328}h1,h2{line-height:1.3}article{background:white;border:1px solid #d1d9e0;border-radius:12px;padding:22px;margin:24px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f0f3f6;padding:14px;border-radius:6px}summary{cursor:pointer;font-weight:600}small{overflow-wrap:anywhere}a{color:#0969da}:focus-visible{outline:2px solid #0969da;outline-offset:3px}@media(prefers-color-scheme:dark){body{background:#0d1117;color:#e6edf3}article{background:#161b22;border-color:#3d444d}pre{background:#212830}a{color:#79c0ff}}</style>',
          '<h1>IssueIntel · Offline handoff / Hồ sơ bàn giao</h1>',
          '<p>Saved reports, excerpts and reported observations. No API, Git, model or internet required.</p>',
          '<p><strong>Integrity has not been checked by opening this page.</strong> Run <code>python verify.py bundle.zip --expected-sha256 SHA_FROM_SENDER</code> against the original ZIP. Hash consistency does not prove authorship, causal truth or that tests ran.</p>',
          '<p><strong>Mở trang không tự kiểm hash.</strong> Quan sát tự báo, diễn giải và nhận xét model vẫn chưa được xác minh.</p>',
          '<p>Source dossier SHA256: <small>'+esc(selection['source']['dossier_sha256'])+'</small></p>']
    for row in selection['runs']:
        body.append('<article><h2>'+esc(row['job_id'])+'</h2><p>'+esc(row['mode']+' · '+row['status'])+'</p>')
        if row['report'] is not None:
            body.append('<pre>'+esc(render_markdown(row['report']))+'</pre>')
            for provenance in row['report']['provenance']:
                if provenance['kind']=='followup':
                    details=json.loads(provenance['details'])
                    if 'selected_memory' in details:
                        body.append('<details><summary>Selected memory / Memory đã chọn</summary><pre>'+esc(_json(details['selected_memory']).decode())+'</pre><p>Declared selection and hashes only; no independent AST, original capture or historical availability proof.</p></details>')
            for evidence in row['report']['evidence']:
                body.append('<details><summary>'+esc(evidence['id']+' · '+evidence['locator'])+'</summary><small>'+esc(evidence['revision'])+'</small><pre>'+esc(evidence['text'])+'</pre></details>')
        else:body.append('<p>Source job metadata only; no completed report is asserted.</p>')
        body.append('</article>')
    for key in ('plans','observations','claim_reviews','transitions','timeline','hypothesis_links','warnings'):
        body.append('<details><summary>'+esc(key)+'</summary><pre>'+esc(_json(selection[key]).decode())+'</pre></details>')
    body.append('<p>Selection SHA256: <small>'+esc(selection['sha256'])+'</small></p></html>')
    return ''.join(body).encode()


class Handoffs:
    def __init__(self,host):self.host=host

    def prepare(self,identifier,request):
        if (type(request) is not dict or set(request)!={'source_pack_sha256','dossier_sha256','job_ids'}
                or type(request['job_ids']) is not list or not 1<=len(request['job_ids'])<=30
                or any(type(i) is not str for i in request['job_ids'])
                or len(set(request['job_ids']))!=len(request['job_ids']) or identifier not in request['job_ids']):
            raise ValueError('Choose up to 30 exact product runs including the viewed run')
        dossier=self.host.dossier(identifier)
        if (dossier['sha256']!=request['dossier_sha256']
                or dossier['source']['selected_pack_sha256']!=request['source_pack_sha256']):
            raise ValueError('Dossier changed; reload and review the handoff selection')
        runs={row['job_id']:row for row in dossier['runs']}
        included=set(request['job_ids']);pending=list(included)
        while pending:
            current=pending.pop();row=runs.get(current)
            if row is None or (row['status']=='completed' and row['report'] is None):
                raise ValueError('Selected handoff source is unavailable')
            parent=row['parent_job_id']
            if parent and parent not in included:included.add(parent);pending.append(parent)
            if len(included)>40:raise ValueError('Handoff ancestry exceeds the bound')
        chosen=[deepcopy(row) for row in dossier['runs'] if row['job_id'] in included]
        source=dict(dossier['source'],dossier_sha256=dossier['sha256'])
        selection=_sealed({'schema':'issueintel_handoff_selection_v1','source':source,'issue':dossier['issue'],
            'requested_job_ids':sorted(request['job_ids']),'runs':chosen,
            'plans':[r for r in dossier['plans'] if r['source_job_id'] in included],
            'claim_reviews':[r for r in dossier['claim_reviews'] if r['source_job_id'] in included],
            'observations':[r for r in dossier['observations'] if any(p['plan_job_id']==r['plan_job_id'] and p['source_job_id'] in included for p in dossier['plans'])],
            'transitions':[r for r in dossier['transitions'] if r['parent_job_id'] in included and r['child_job_id'] in included],
            'timeline':[r for r in dossier['timeline'] if r['job_id'] in included and (r['kind']!='hypothesis_link' or r['event']['relation']['parent_job_id'] in included)],
            'hypothesis_links':{'source_state_sha256':dossier['hypothesis_links']['sha256'],
                'selection_is_partial':True,'events':[e for e in dossier['hypothesis_links']['events']
                    if e['relation']['parent_job_id'] in included and e['relation']['child_job_id'] in included]},
            'warnings':[r for r in dossier['warnings'] if r['job_id'] in included],
            'limitations':dossier['limitations']+[
                'Only selected runs and required ancestors are included; this is not the full source dossier.',
                'Source replay was performed locally at export. Offline verification checks included bytes and declared bindings, not original Git or remote provenance.',
                'User content, repository paths, code excerpts and observations are private until explicitly shared by the author.'],
            'model_calls':0,'evaluation_performed':False})
        files={};kinds={}
        def add(name,value,kind):
            if name in files:raise ValueError('Duplicate handoff path')
            files[name]=value;kinds[name]=kind
        add('handoff.json',_json(selection),'selected_history')
        add('index.html',_viewer(selection),'offline_viewer')
        add('verify.py',Path(portable_verify.__file__).read_bytes(),'standalone_verifier')
        for row in chosen:
            job_id=row['job_id'];prefix='runs/'+job_id+'/'
            if row['report'] is not None:
                _,raw,_=self.host._load_pack(job_id)
                if portable_verify.sha(raw)!=row['binding']['pack_sha256']:raise ValueError('Handoff source changed')
                add(prefix+'pack.json',raw,'source_pack')
                add(prefix+'report.json',_json(row['report']),'report')
                add(prefix+'report.md',render_markdown(row['report']).encode(),'report_markdown')
                memory=(json.loads(raw).get('followup') or {}).get('memory_selection')
                if memory:add(prefix+'memory-selection.json',_json(memory),'selected_memory_context')
            # Only recovery checkpoints referenced by included children, never a
            # whole run directory, environment, SQLite DB or Git cache.
            for child in chosen:
                if child['parent_job_id']!=job_id or child['report'] is None:continue
                pack=json.loads(files['runs/'+child['job_id']+'/pack.json']) if 'runs/'+child['job_id']+'/pack.json' in files else json.loads(self.host._load_pack(child['job_id'])[1])
                recovery=pack.get('resumed_from') or pack.get('finalized_from')
                if pack.get('recovered_from'):
                    stage_source=pack['recovered_from']['source']
                    original,_,_,binding,segments=self.host.stage_recoveries._source_state(job_id)
                    if stage_source!=binding:raise ValueError('Stage recovery handoff source changed')
                    child_row=json.loads(self.host._job_path(child['job_id']).read_bytes())
                    witness={'schema':'product_stage_handoff_v1','source':binding,'source_request':original['request'],
                        'source_attempts':original['pipeline_attempts'],'new_attempts':child_row['pipeline_attempts'],
                        'source_usage':{'tracking':original.get('usage_tracking'),'receipts':original.get('usage_receipts',{})},
                        'requires_api':child_row['request']['requires_api']}
                    if binding['schema'] in {'product_stage_recovery_source_v2','product_stage_recovery_source_v3','product_stage_recovery_source_v4'}:
                        witness={'schema':'product_stage_handoff_v'+binding['schema'][-1],'source':binding,'source_chain':segments,
                            'new_attempts':child_row['pipeline_attempts'],'requires_api':child_row['request']['requires_api']}
                    add('runs/'+child['job_id']+'/stage-recovery.json',_json(witness),'stage_recovery_source')
                    for stage in binding['stages']:
                        owner=stage.get('owner_job_id',job_id);name='runs/'+owner+'/stages/'+stage['stage']+'.json'
                        if owner not in included:raise ValueError('Missing included recovery stage owner')
                        raw_stage=self.host.stage_bytes(owner,stage['stage'])
                        if portable_verify.sha(raw_stage)!=stage['sha256']:raise ValueError('Recovery checkpoint changed during handoff')
                        if name not in files:add(name,raw_stage,'recovery_checkpoint')
                    for stage in child_row.get('stage_checkpoints',{}):
                        name='runs/'+child['job_id']+'/stages/'+stage+'.json'
                        if name not in files:add(name,self.host.stage_bytes(child['job_id'],stage),'recovery_checkpoint')
                    continue
                if not recovery:continue
                stages=('verification_input','verification') if pack.get('finalized_from') else ('verification_input',)
                for stage in stages:
                    name=prefix+'stages/'+stage+'.json'
                    if name not in files:add(name,self.host.stage_bytes(job_id,stage),'recovery_checkpoint')
        instructions=('IssueIntel private offline handoff\n\n'
            '1. Keep the original ZIP and obtain its SHA256 from the sender through a trusted channel.\n'
            '2. Verify using an installed IssueIntel: python -m issueintel verify-handoff bundle.zip --expected-sha256 SHA\n'
            '   Or inspect verify.py, then run it with Python 3.11+: python verify.py bundle.zip --expected-sha256 SHA\n'
            '3. Open index.html locally after extracting with your usual trusted archive tool. No server or API is needed.\n'
            '4. Report JSON/Markdown and original source packs are under runs/. Selected journals/plans are in handoff.json.\n\n'
            'The archive has private issue text, code, paths and observations. Review before sharing.\n'
            'Hashes check consistency, not authorship or semantic truth. The HTML page does not run verification.\n'
            'User reports/model judgments are unverified; this is not a benchmark evaluation or proof of a fix.\n')
        add('README.txt',instructions.encode(),'instructions')
        entries=[{'path':name,'kind':kinds[name],'bytes':len(raw),'sha256':portable_verify.sha(raw)} for name,raw in sorted(files.items())]
        manifest=_sealed({'schema':portable_verify.SCHEMA,'source':source,'selection_sha256':selection['sha256'],
            'requested_job_ids':sorted(request['job_ids']),'included_job_ids':sorted(included),
            'entries':entries,'model_calls':0,'evaluation_performed':False})
        files['manifest.json']=_json(manifest)
        if len(files)>portable_verify.MAX_FILES or sum(map(len,files.values()))>portable_verify.MAX_BYTES:
            raise ValueError('Handoff exceeds 512 files or 100 MB; select fewer runs')
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
            for name,raw in sorted(files.items()):
                info=zipfile.ZipInfo(name,date_time=(2020,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
                info.external_attr=0o100644<<16;archive.writestr(info,raw)
        raw=buffer.getvalue()
        if len(raw)>portable_verify.MAX_BYTES:raise ValueError('Compressed handoff exceeds 100 MB')
        preview=_sealed({'schema':'issueintel_handoff_preview_v1','source':source,
            'requested_job_ids':sorted(request['job_ids']),'included_job_ids':sorted(included),
            'automatic_ancestor_job_ids':sorted(included-set(request['job_ids'])),
            'entries':entries+[{'path':'manifest.json','kind':'manifest','bytes':len(files['manifest.json']),'sha256':portable_verify.sha(files['manifest.json'])}],
            'archive_bytes':len(raw),'uncompressed_bytes':sum(map(len,files.values())),
            'archive_sha256':portable_verify.sha(raw),'manifest_sha256':manifest['sha256'],
            'contains_private_content':True,'model_calls':0,'evaluation_performed':False})
        return preview,raw

    def preview(self,identifier,request):return self.prepare(identifier,request)[0]

    def export(self,identifier,request):
        if (type(request) is not dict or set(request)!={'selection','preview_sha256','confirmation'}
                or request['confirmation']!='export_selected_private_handoff'):
            raise ValueError('Preview and explicitly confirm the private handoff first')
        preview,raw=self.prepare(identifier,request['selection'])
        if preview['sha256']!=request['preview_sha256']:raise ValueError('Handoff preview changed; preview again')
        return preview,raw
