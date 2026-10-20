"""Private immutable memory captures with deduplicated source witnesses.

Only capture methods create records. Reads replay captured bytes, not Git or a
model. This store binds caller-supplied product sources; Workbench separately
authenticates its job lineage before calling it.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import gzip
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
from tempfile import NamedTemporaryFile

from issueintel.runtime.io import canonical_json, digest
from .cache import load_snapshot, _read_gzip_bounded
from .memory import build_architecture_memory, validate_architecture_memory, assess_architecture_memory


MAX_OBJECT_BYTES = 8_000_000
LESSON_SCHEMA = 'repository_investigation_lesson_v1'
RECEIPT_SCHEMA = 'repository_memory_capture_v1'
_SHA = re.compile(r'[a-f0-9]{64}')
LESSON_LIMITATIONS = [
    'Prior model hypotheses and optional author notes are unverified context, not established lessons or fixes.',
    'Matching code dependencies do not verify a hypothesis or transfer its applicability to another issue.',
    'Capture time is not historical availability proof; investigation lessons cannot enter strict inference.',
    'Source packs are private witnesses. Source-job authenticity is the caller responsibility.',
]


def _hash(raw):
    return sha256(raw).hexdigest()


def _hex(value):
    if not isinstance(value,str) or _SHA.fullmatch(value) is None:
        raise ValueError('Expected an exact memory SHA256')
    return value


def _repository(value):
    if (not isinstance(value,str) or not value.strip() or len(value)>2000
            or any(ord(char)<32 or ord(char)==127 for char in value)):
        raise ValueError('Choose a bounded repository identity')
    return value


def _job(value):
    if value is not None and (not isinstance(value,str) or not re.fullmatch(r'[a-f0-9]{24}',value)):
        raise ValueError('Invalid memory source job identity')
    return value


def _json(raw):
    if not isinstance(raw,bytes) or len(raw)>MAX_OBJECT_BYTES:
        raise ValueError('Memory witness exceeds the private object bound')
    def reject(value):raise ValueError('Non-finite memory JSON is not allowed')
    return json.loads(raw,parse_constant=reject)


def _descriptor(snapshot):
    value=snapshot.to_dict()
    value['files']=[{key:item for key,item in file.to_dict().items() if key!='text'} for file in snapshot.files]
    return value


def product_memory_pack(raw):
    """Reject benchmark/strict sources before a caller reads Git objects."""
    from issueintel.investigation.comparison import compare_packs
    from issueintel.investigation.planner import validate_planned
    try:
        pack=_json(raw);case=pack['case'];audit=pack['leakage_audit']
        if ('split' in case or 'instance_id' in case
                or case['case_id'].startswith(('verified:','live:','main_dev:','main_test:'))
                or pack['snapshot']['protocol']!='benchmark_base' or pack['snapshot']['cutoff'] is not None
                or audit.get('strict_issue_time_verified') is not False or audit.get('gold_in_inference') is not False):
            raise ValueError('Investigation memory requires a product source outside benchmark splits')
        compare_packs(pack,pack)
        if 'workflow_plan' in pack:validate_planned(pack)
        return pack
    except (KeyError,TypeError,AttributeError,IndexError):
        raise ValueError('Invalid product memory source structure') from None


def _product_source(raw,snapshot,repository):
    from issueintel.investigation.pipeline import validate_prepared_evidence
    pack=product_memory_pack(raw);case=pack['case']
    if case['repository']!=repository or digest(pack['snapshot'])!=digest(_descriptor(snapshot)):
        raise ValueError('Investigation memory source/snapshot/repository binding differs')
    validate_prepared_evidence(case,[row for row in pack['evidence'] if row['kind']!='repository_context'],snapshot)
    return pack


def investigation_lesson(raw,snapshot,repository,*,source_job_id=None,hypothesis_ids,note=''):
    """Select prior hypotheses verbatim, retaining their unverified truth level."""
    from issueintel.annotations.public_inputs import model_hypotheses
    _repository(repository);_job(source_job_id)
    if (not isinstance(hypothesis_ids,list) or not 1<=len(hypothesis_ids)<=5
            or any(not isinstance(item,str) or not item for item in hypothesis_ids)
            or len(set(hypothesis_ids))!=len(hypothesis_ids)
            or not isinstance(note,str) or len(note)>2000 or '\0' in note):
        raise ValueError('Choose 1–5 distinct prior hypotheses and an optional bounded note')
    pack=_product_source(raw,snapshot,repository)
    annotation=pack.get('annotation')
    if annotation is None:raise ValueError('The source has no hypotheses to retain')
    selected=[row for row in model_hypotheses(annotation['hypotheses']) if row['id'] in hypothesis_ids]
    if [row['id'] for row in selected]!=hypothesis_ids:
        raise ValueError('Choose existing hypotheses in their source order')
    wanted={identifier for row in selected for identifier in row['support_ids']+row['counter_ids']}
    evidence=[deepcopy(row) for row in pack['evidence'] if row['id'] in wanted]
    files={file.path:file for file in snapshot.files}
    paths=sorted({row['locator'].rsplit(':',1)[0] for row in evidence if row['kind']=='code'})
    text=pack['case']['problem_statement']
    value={'schema':LESSON_SCHEMA,'source':{'repository':repository,'source_job_id':source_job_id,
        'pack_sha256':_hash(raw),'case_id':pack['case']['case_id'],'issue_url':pack['case'].get('issue_url'),
        'query_sha256':_hash(text.encode()),'annotation_sha256':digest(annotation),
        'commit_sha':snapshot.commit_sha,'snapshot_sha256':snapshot.snapshot_sha256},
        'issue_excerpt':text[:1000],'issue_excerpt_truncated':len(text)>1000,
        'hypotheses':selected,'evidence':evidence,'author_note':note,
        'dependencies':[{'path':path,'sha256':files[path].sha256} for path in paths],
        'truth_level':'unverified_prior_investigation','available_at':None,'historical_eligible':False,
        'runtime_verified':False,'causal_verified':False,'independent_human':False,
        'model_calls':0,'limitations':list(LESSON_LIMITATIONS)}
    return dict(value,sha256=digest(value))


class MemoryStore:
    def __init__(self,root):
        self.root=Path(root).resolve()
        self.directory=self.root/'artifacts'/'repository_memory'
        self.cache=self.directory/'cache'

    @contextmanager
    def _db(self,*,write=False):
        self.directory.mkdir(parents=True,exist_ok=True)
        try:
            db=sqlite3.connect(self.directory/'memory.sqlite',timeout=30)
        except sqlite3.Error as error:
            raise ValueError('Private memory database is unavailable') from error
        db.row_factory=sqlite3.Row
        try:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS objects(sha TEXT PRIMARY KEY,payload BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS captures(id TEXT PRIMARY KEY,repository TEXT NOT NULL,
                    kind TEXT NOT NULL,receipt_sha TEXT NOT NULL,receipt BLOB NOT NULL);
                CREATE INDEX IF NOT EXISTS captures_repository ON captures(repository,id);
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_search USING fts5(id UNINDEXED,repository UNINDEXED,text);
            ''')
            if write:db.execute('BEGIN IMMEDIATE')
            yield db
            if write:db.commit()
        except (sqlite3.Error,KeyError,TypeError,AttributeError,IndexError) as error:
            if write:db.rollback()
            raise ValueError('Private memory database or capture structure is invalid') from error
        except BaseException:
            if write:db.rollback()
            raise
        finally:db.close()

    def _pack(self,raw,*,write=False):
        _json(raw);sha=_hash(raw)
        path=self.directory/'packs'/sha[:2]/(sha+'.json.gz')
        if write:self._publish_gzip(path,raw,MAX_OBJECT_BYTES)
        saved=_read_gzip_bounded(path,MAX_OBJECT_BYTES)
        if _hash(saved)!=sha:raise ValueError('Private memory pack witness is corrupt')
        return sha

    def _publish_gzip(self,path,content,limit):
        """Publish a complete immutable object; interrupted writes stay hidden."""
        if len(content)>limit:raise ValueError('Private memory source exceeds its object bound')
        if not path.resolve().is_relative_to(self.directory.resolve()):
            raise ValueError('Private memory object escapes its store')
        if path.exists():
            if _read_gzip_bounded(path,limit)!=content:raise ValueError('Private memory object has conflicting bytes')
            return
        path.parent.mkdir(parents=True,exist_ok=True)
        temporary=None
        try:
            with NamedTemporaryFile(dir=path.parent,prefix='.memory-',delete=False) as handle:
                temporary=Path(handle.name)
                handle.write(gzip.compress(content,mtime=0));handle.flush();os.fsync(handle.fileno())
            try:
                # link is an atomic create, never a replace of an existing hash.
                # It requires no Windows symbolic-link privilege on NTFS.
                os.link(temporary,path)
            except FileExistsError:
                if _read_gzip_bounded(path,limit)!=content:
                    raise ValueError('Private memory object has conflicting bytes') from None
        finally:
            if temporary is not None:temporary.unlink(missing_ok=True)

    def _save_snapshot(self,snapshot):
        for file in snapshot.files:
            target=self.cache/'blobs'/file.sha256[:2]/(file.sha256+'.txt.gz')
            self._publish_gzip(target,file.text.encode(),4_000_000)
        self._publish_gzip(self.cache/'snapshots'/(snapshot.snapshot_sha256+'.json.gz'),
                           canonical_json(_descriptor(snapshot)).encode(),64_000_000)

    @staticmethod
    def _search_text(payload):
        if payload['schema']==LESSON_SCHEMA:
            parts=[payload['issue_excerpt'],payload['author_note']]+[
                row['statement']+' '+row['falsification_test'] for row in payload['hypotheses']]
        else:
            parts=[row['kind']+' '+row['source']['path']+' '+row['source']['qualname']+' '+
                   (row['reference'] or '') for row in payload['facts']]
        return '\n'.join(parts)[:16000]

    def _capture(self,kind,repository,payload,snapshot,source_job_id,source_raw):
        _repository(repository);_job(source_job_id);snapshot.verify()
        encoded=canonical_json(payload).encode();_json(encoded)
        pack_sha=_hash(source_raw) if source_raw is not None else None
        authority={'kind':kind,'repository':repository,'memory_sha256':payload['sha256'],
                   'snapshot_sha256':snapshot.snapshot_sha256,'source_job_id':source_job_id,
                   'source_pack_sha256':pack_sha}
        identifier='capture_'+digest(authority)[:32]
        with self._db(write=True) as db:
            existing=db.execute('SELECT id FROM captures WHERE id=?',(identifier,)).fetchone()
            if existing:
                return self._read(db,identifier)
            self._save_snapshot(snapshot)
            if source_raw is not None:self._pack(source_raw,write=True)
            object_sha=_hash(encoded)
            old=db.execute('SELECT payload FROM objects WHERE sha=?',(object_sha,)).fetchone()
            if old and old['payload']!=encoded:raise ValueError('Memory object digest conflict')
            db.execute('INSERT OR IGNORE INTO objects VALUES (?,?)',(object_sha,encoded))
            receipt=dict(authority,schema=RECEIPT_SCHEMA,id=identifier,object_sha256=object_sha,
                         recorded_at=datetime.now(timezone.utc).isoformat(),source_authenticity='caller_supplied_source',
                         historical_claim_from_capture_time=False)
            receipt=dict(receipt,sha256=digest(receipt))
            db.execute('INSERT INTO captures VALUES (?,?,?,?,?)',
                       (identifier,repository,kind,receipt['sha256'],canonical_json(receipt).encode()))
            db.execute('INSERT INTO memory_search(id,repository,text) VALUES (?,?,?)',
                       (identifier,repository,self._search_text(payload)))
            return self._read(db,identifier)

    def capture_architecture(self,snapshot,repository,*,max_facts=500,source_job_id=None,source_raw=None):
        if source_job_id is not None and source_raw is None:
            raise ValueError('A source job requires its exact product pack witness')
        if source_raw is not None:_product_source(source_raw,snapshot,repository)
        payload=build_architecture_memory(snapshot,max_facts=max_facts)
        return self._capture('architecture',repository,payload,snapshot,source_job_id,source_raw)

    def capture_lesson(self,raw,snapshot,repository,*,source_job_id=None,hypothesis_ids,note=''):
        payload=investigation_lesson(raw,snapshot,repository,source_job_id=source_job_id,
                                     hypothesis_ids=hypothesis_ids,note=note)
        return self._capture('investigation_lesson',repository,payload,snapshot,source_job_id,raw)

    def _read(self,db,identifier):
        if not isinstance(identifier,str) or not re.fullmatch('capture_[a-f0-9]{32}',identifier):
            raise ValueError('Invalid memory capture identity')
        row=db.execute('SELECT * FROM captures WHERE id=?',(identifier,)).fetchone()
        if row is None:raise ValueError('No such private memory capture')
        receipt=_json(row['receipt']);sha=receipt.get('sha256')
        if (set(receipt)!={'kind','repository','memory_sha256','snapshot_sha256','source_job_id','source_pack_sha256',
                'schema','id','object_sha256','recorded_at','source_authenticity','historical_claim_from_capture_time','sha256'}
                or sha!=row['receipt_sha'] or sha!=digest({k:v for k,v in receipt.items() if k!='sha256'})
                or receipt.get('schema')!=RECEIPT_SCHEMA or receipt.get('id')!=identifier
                or receipt.get('repository')!=row['repository'] or receipt.get('kind')!=row['kind']
                or receipt.get('source_authenticity')!='caller_supplied_source'
                or receipt.get('historical_claim_from_capture_time') is not False):
            raise ValueError('Memory capture receipt binding differs')
        recorded=datetime.fromisoformat(receipt['recorded_at'])
        if recorded.tzinfo is None:raise ValueError('Memory capture time requires a timezone')
        authority={key:receipt[key] for key in ('kind','repository','memory_sha256','snapshot_sha256',
                                               'source_job_id','source_pack_sha256')}
        if identifier!='capture_'+digest(authority)[:32]:raise ValueError('Memory capture authority changed')
        _repository(receipt['repository']);_job(receipt['source_job_id']);_hex(receipt['object_sha256'])
        source_sha=_hex(receipt['snapshot_sha256'])
        obj=db.execute('SELECT payload FROM objects WHERE sha=?',(receipt['object_sha256'],)).fetchone()
        if obj is None or _hash(obj['payload'])!=receipt['object_sha256']:
            raise ValueError('Memory object witness is corrupt')
        payload=_json(obj['payload'])
        if payload.get('sha256')!=receipt['memory_sha256']:raise ValueError('Memory object/receipt mismatch')
        snapshot=load_snapshot(self.cache/'snapshots'/(source_sha+'.json.gz'),self.cache)
        raw=None
        if receipt['source_pack_sha256'] is not None:
            pack_sha=_hex(receipt['source_pack_sha256'])
            raw=_read_gzip_bounded(self.directory/'packs'/pack_sha[:2]/(pack_sha+'.json.gz'),MAX_OBJECT_BYTES)
            if _hash(raw)!=pack_sha:raise ValueError('Private memory pack witness is corrupt')
            _product_source(raw,snapshot,receipt['repository'])
        elif receipt['source_job_id'] is not None:
            raise ValueError('Memory source job has no pack witness')
        if receipt['kind']=='architecture':
            validate_architecture_memory(payload,snapshot)
        elif receipt['kind']=='investigation_lesson':
            if raw is None:raise ValueError('Investigation lesson lost its source pack')
            expected=investigation_lesson(raw,snapshot,receipt['repository'],source_job_id=receipt['source_job_id'],
                hypothesis_ids=[row['id'] for row in payload['hypotheses']],note=payload['author_note'])
            if digest(payload)!=digest(expected):raise ValueError('Prior lesson differs from its source hypotheses')
        else:raise ValueError('Unknown memory capture kind')
        return {'receipt':receipt,'memory':payload}

    def read(self,identifier):
        with self._db() as db:return self._read(db,identifier)

    def list(self,repository,*,query='',limit=20):
        _repository(repository)
        if (not isinstance(query,str) or len(query)>500 or '\0' in query
                or type(limit) is not int or not 1<=limit<=50):
            raise ValueError('Choose a bounded memory search')
        terms=list(dict.fromkeys(re.findall(r'\w+',query.casefold())))[:16]
        with self._db() as db:
            if terms:
                expression=' OR '.join('"'+term.replace('"','""')+'"' for term in terms)
                candidates=db.execute('SELECT id,text FROM memory_search WHERE memory_search MATCH ? '
                    'AND repository=? ORDER BY rank,id LIMIT ?',(expression,repository,limit)).fetchall()
            else:
                candidates=db.execute('SELECT c.id,s.text FROM captures c LEFT JOIN memory_search s ON s.id=c.id '
                    'WHERE c.repository=? ORDER BY c.rowid DESC LIMIT ?',(repository,limit)).fetchall()
            results=[]
            for candidate in candidates:
                value=self._read(db,candidate['id']);payload=value['memory'];receipt=value['receipt']
                text=self._search_text(payload)
                if receipt['repository']!=repository or text!=candidate['text']:
                    raise ValueError('Memory search index/source binding changed')
                results.append({'id':receipt['id'],'receipt_sha256':receipt['sha256'],
                    'kind':receipt['kind'],'recorded_at':receipt['recorded_at'],
                    'source':deepcopy(payload['source']),'matched_terms':[term for term in terms if term in text.casefold()],
                    'retained_count':len(payload.get('facts',payload.get('hypotheses',[]))),
                    'facts_omitted':payload.get('facts_omitted',0)})
            return {'schema':'repository_memory_list_v1','repository':repository,'query':query,
                    'scope':'bounded_local_captures','search_text_limit_per_capture':16000,
                    'limit':limit,'results':results,'model_calls':0}

    def assess(self,identifier,current_snapshot,*,cutoff=None):
        value=self.read(identifier);receipt=value['receipt'];payload=value['memory']
        snapshot=load_snapshot(self.cache/'snapshots'/(receipt['snapshot_sha256']+'.json.gz'),self.cache)
        if receipt['kind']=='architecture':
            return assess_architecture_memory(payload,snapshot,current_snapshot,cutoff=cutoff)
        current_snapshot.verify()
        if current_snapshot.repository!=snapshot.repository:
            raise ValueError('Lesson assessment requires the same repository')
        files={file.path:file for file in current_snapshot.files}
        skipped={row['path'] for row in current_snapshot.skipped if 'path' in row}
        dependencies=[]
        for dependency in payload['dependencies']:
            path=dependency['path'];file=files.get(path)
            status=('unavailable' if path in skipped else 'missing' if file is None else
                    'unchanged' if file.sha256==dependency['sha256'] else 'changed')
            dependencies.append(dict(dependency,status=status,current_sha256=file.sha256 if file else None))
        result={'schema':'repository_lesson_assessment_v1','memory_sha256':payload['sha256'],
                'source':payload['source'],'current':{'commit_sha':current_snapshot.commit_sha,
                    'snapshot_sha256':current_snapshot.snapshot_sha256},'dependencies':dependencies,
                'truth_level':'unverified_prior_investigation','hypothesis_verified':False,
                'historical_eligible':False,'historical_reason':'no_independent_lesson_availability_proof',
                'original_memory_unchanged':True,'model_calls':0}
        result['dependency_status']=('no_code_dependencies' if not dependencies else
            'source_unavailable' if any(row['status']=='unavailable' for row in dependencies) else
            'source_missing' if any(row['status']=='missing' for row in dependencies) else
            'source_changed' if any(row['status']=='changed' for row in dependencies) else
            'source_unchanged_hypothesis_unverified')
        return dict(result,sha256=digest(result))
