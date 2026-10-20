"""Owned product access to private memory; no implicit inference or API spend."""
from contextlib import contextmanager
from hashlib import sha256
import json
import re

from issueintel.repository.memory_store import MemoryStore
from issueintel.repository.snapshot import source_lines


PRODUCT_MODES = {'local','github','example','followup','clarification','check_followup','context',
                 'resume','finalization','planned_continuation'}


def _hash(value):
    if not isinstance(value,str) or re.fullmatch(r'[a-f0-9]{64}',value) is None:
        raise ValueError('Review the exact product source and memory receipt')
    return value


class ProductMemory:
    def __init__(self,host):
        self.host=host
        self.store=MemoryStore(host.root)

    def _source(self,identifier,expected=None):
        # Reject a benchmark job before reading its pack or preparing Git.
        saved=json.loads(self.host._job_path(identifier).read_bytes())
        if (saved.get('id')!=identifier or saved.get('status')!='completed'
                or saved.get('mode') not in PRODUCT_MODES or not isinstance(saved.get('request'),dict)
                or any(saved.get(key)!=saved['request'].get(key) for key in ('mode','provider','depth'))):
            raise ValueError('Repository memory requires an owned product investigation')
        pack,raw,_=self.host._load_pack(identifier)
        self.host._clarification_product(identifier,pack)
        # Reuse the origin authority shared by dossiers: local query/revision,
        # GitHub identity, fixed example text and mixed parent lineage all bind
        # back to the original canonical request, not just a matching pack hash.
        self.host.dossiers._origin(identifier,authenticate=True)
        actual=sha256(raw).hexdigest()
        if expected is not None and _hash(expected)!=actual:
            raise ValueError('Memory source report changed; review the selected run')
        binding={'source_job_id':identifier,'source_pack_sha256':actual,
                 'repository':pack['case']['repository'],'commit_sha':pack['snapshot']['commit_sha'],
                 'snapshot_sha256':pack['snapshot']['snapshot_sha256']}
        return pack,raw,binding

    def status(self,identifier):
        try:
            pack,_,binding=self._source(identifier)
            return dict(binding,available=True,can_capture_lesson=bool((pack.get('annotation') or {}).get('hypotheses')),
                        model_calls=0)
        except (ValueError,KeyError,TypeError,AttributeError,OSError):
            return {'source_job_id':identifier,'available':False,'reason':'requires_owned_product_source','model_calls':0}

    def _request(self,identifier,request,keys):
        if not isinstance(request,dict) or set(request)!=set(keys)|{'source_pack_sha256'}:
            raise ValueError('Choose an exact source-bound memory action')
        return self._source(identifier,request['source_pack_sha256'])

    def _capture(self,binding,request):
        value=self.store.read(request['capture_id'])
        receipt=value['receipt']
        if (receipt['repository']!=binding['repository']
                or receipt['sha256']!=_hash(request['receipt_sha256'])):
            raise ValueError('Memory capture belongs to another repository or receipt')
        return value

    @contextmanager
    def _snapshot(self,pack):
        if not self.host.capacity.acquire(blocking=False):
            raise RuntimeError('The local work queue is full')
        try:
            from .question_jobs import replay_snapshot
            yield replay_snapshot(pack)
        finally:
            self.host.capacity.release()

    def search(self,identifier,request):
        _,_,binding=self._request(identifier,request,{'query','limit'})
        result=self.store.list(binding['repository'],query=request['query'],limit=request['limit'])
        return dict(result,source=binding)

    def capture(self,identifier,request):
        if not isinstance(request,dict):raise ValueError('Choose a memory capture kind')
        kind=request.get('kind')
        keys=({'kind','max_facts'} if kind=='architecture' else
              {'kind','hypothesis_ids','note'} if kind=='lesson' else None)
        if keys is None:raise ValueError('Choose architecture or prior hypotheses')
        pack,raw,binding=self._request(identifier,request,keys)
        with self._snapshot(pack) as snapshot:
            if kind=='architecture':
                result=self.store.capture_architecture(snapshot,binding['repository'],max_facts=request['max_facts'],
                    source_job_id=identifier,source_raw=raw)
            else:
                result=self.store.capture_lesson(raw,snapshot,binding['repository'],source_job_id=identifier,
                    hypothesis_ids=request['hypothesis_ids'],note=request['note'])
        return dict(result,source=binding,model_calls=0)

    def open(self,identifier,request):
        _,_,binding=self._request(identifier,request,{'capture_id','receipt_sha256'})
        return dict(self._capture(binding,request),source=binding,model_calls=0)

    def assess(self,identifier,request):
        pack,_,binding=self._request(identifier,request,{'capture_id','receipt_sha256'})
        value=self._capture(binding,request)
        with self._snapshot(pack) as snapshot:
            result=self.store.assess(value['receipt']['id'],snapshot)
        return {'source':binding,'receipt_sha256':value['receipt']['sha256'],
                'capture_id':value['receipt']['id'],'assessment':result,'model_calls':0}

    def source(self,identifier,request):
        _,_,binding=self._request(identifier,request,{'capture_id','receipt_sha256','item_id','role'})
        value=self._capture(binding,request);receipt=value['receipt'];memory=value['memory']
        if request['role'] not in {'source','target'}:raise ValueError('Choose a source or target span')
        if receipt['kind']=='architecture':
            fact=next((row for row in memory['facts'] if row['id']==request['item_id']),None)
            if fact is None or fact[request['role']] is None:raise ValueError('No selected memory source span')
            node=fact[request['role']]
            snapshot=self.store.source_snapshot(receipt['id'])
            file=next(file for file in snapshot.files if file.path==node['path'])
            text=(file.text if node['kind']=='file' else
                  '\n'.join(source_lines(file.text)[node['start_line']-1:node['end_line']]))
            record={'id':fact['id'],'kind':'code','locator':f"{file.path}:{node['start_line']}-{node['end_line']}",
                    'text':text,'sha256':node['sha256'],'revision':snapshot.commit_sha}
        else:
            if request['role']!='source':raise ValueError('A prior hypothesis has evidence sources, not a static target')
            record=next((row for row in memory['evidence'] if row['id']==request['item_id']),None)
            if record is None:raise ValueError('No selected prior evidence source')
        # Preserve exact source bytes (including CRLF and form-feed) when the
        # full excerpt fits. Only LF defines the line bound, as in snapshots.
        excerpt='\n'.join(record['text'].split('\n')[:80])[:6000]
        return {'source':binding,'capture_id':receipt['id'],'receipt_sha256':receipt['sha256'],
                'item_id':request['item_id'],'role':request['role'],'kind':record['kind'],
                'locator':record['locator'],'revision':record['revision'],'source_text_sha256':record['sha256'],
                'excerpt':excerpt,'excerpt_sha256':sha256(excerpt.encode()).hexdigest(),
                'truncated':excerpt!=record['text'],'source_commit_sha':memory['source']['commit_sha'],
                'model_calls':0}
