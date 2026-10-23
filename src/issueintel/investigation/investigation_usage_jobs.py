"""Bounded, read-only capture of the costs owned by one investigation chain.

This module never opens a provider ledger, repository, configuration or model.
Requests and input text are inspected privately to authenticate typed links;
only accounting metadata and their hashes leave the capture operation.
"""
from copy import deepcopy
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3

from issueintel.runtime.io import canonical_json, digest


MAX_NODES = 128
MAX_EDGES = 256
MAX_ANCESTRY = 16
MAX_SCAN_JOBS = 4096
MAX_SINGLE_FILE_BYTES = 16_000_000
MAX_TOTAL_READ_BYTES = 64_000_000
MAX_CAPTURE_ATTEMPTS = 3
SIDECARS = {
    'adaptive_questions': ('adaptive_questions.sqlite', 'generate_questions_with_api_cost', 'questions'),
    'check_plan': ('check_plans.sqlite', 'generate_check_plan_with_api_cost', 'checks'),
    'claim_review': ('claim_reviews.sqlite', 'generate_claim_review_with_api_cost', 'claims'),
    'issue_relation': ('issue_relations.sqlite', 'compare_issue_pair_with_api_cost', 'relation'),
}
ORIGINAL = {'local', 'github', 'example', 'main_dev', 'repository'}
FORKS = {'followup', 'clarification', 'check_followup', 'memory_followup', 'context'}
DERIVED = FORKS | {'planned_continuation', 'stage_resume', 'resume', 'finalization'}
MODES = ORIGINAL | DERIVED | set(SIDECARS)
GROQ_RECEIPT_FIELDS = {
    'reservation_id', 'created_at', 'model', 'input_bound', 'output_bound',
    'tokens_charged_or_reserved', 'micro_usd_charged_or_reserved', 'status',
    'price_date', 'observed_input_tokens', 'observed_output_tokens', 'stage',
}
TYPESAFE_RECEIPT_FIELDS = {
    'schema', 'provider', 'reservation_id', 'created_at', 'model', 'binding',
    'request_sha256', 'response_sha256', 'status', 'input_bound', 'input_tokens',
    'output_tokens', 'micro_usd_charged_or_reserved', 'price_date', 'price_source',
    'currency', 'is_provider_invoice', 'sha256',
}


def _hex(value, size=64):
    return type(value) is str and re.fullmatch('[a-f0-9]{' + str(size) + '}', value) is not None


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON accounting key')
        result[key] = value
    return result


def _json(raw):
    try:
        value=json.loads(raw, object_pairs_hook=_object,
                         parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite accounting JSON')))
    except RecursionError:
        raise ValueError('Saved accounting JSON exceeds its nesting bound') from None
    if type(value) is not dict:
        raise ValueError('Saved accounting JSON is not an object')
    return value


def _sql_hash(rows):
    def public(value):
        if isinstance(value, bytes):
            return {'bytes_sha256': sha256(value).hexdigest(), 'size': len(value)}
        if isinstance(value, (tuple, list)):
            return [public(item) for item in value]
        return value
    return digest(public(rows))


class _Bound(ValueError):
    pass


class _Capture:
    def __init__(self, host, selected):
        self.host = host
        self.selected = selected
        self.files = {}
        self.missing = set()
        self.queries = {}
        self.read_bytes = 0
        self.rows = {}
        self.packs = {}
        self.nodes = {}
        self.edges = []
        self.gaps = []
        self.listing = None
        self.discovery_metadata = {}

    def gap(self, code, job=None, provider=None, detail='Saved local accounting proof is unavailable.'):
        item = {'code': code, 'job_id': job, 'provider': provider, 'detail': detail}
        if item not in self.gaps and len(self.gaps) < 512:
            self.gaps.append(item)

    def read(self, path):
        path = Path(path).resolve()
        if not path.is_relative_to(self.host.directory.resolve()):
            raise ValueError('Accounting source is outside the workbench')
        if path in self.files:
            return self.files[path]
        if not path.is_file():
            self.missing.add(path)
            raise FileNotFoundError('Saved accounting source is missing')
        if path.stat().st_size > MAX_SINGLE_FILE_BYTES:
            raise _Bound('Accounting input exceeds the file bound')
        with path.open('rb') as stream:
            raw=stream.read(MAX_SINGLE_FILE_BYTES+1)
        self.read_bytes += len(raw)
        if len(raw) > MAX_SINGLE_FILE_BYTES or self.read_bytes > MAX_TOTAL_READ_BYTES:
            raise _Bound('Accounting inputs exceed the operation bound')
        self.files[path] = raw
        return raw

    def query(self, name, sql, params=()):
        path = (self.host.directory / name).resolve()
        key = (path, sql, tuple(params))
        if key in self.queries:
            return self.queries[key]
        if not path.is_file():
            self.missing.add(path)
            raise FileNotFoundError('Saved accounting registry is missing')
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=.25)) as db:
            db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,MAX_SINGLE_FILE_BYTES)
            db.execute('PRAGMA query_only=ON')
            rows = db.execute(sql, params).fetchmany(MAX_NODES + 1)
        if len(rows) > MAX_NODES:
            raise _Bound('Accounting registry exceeds the selected scope bound')
        self.read_bytes += sum(len(v) if isinstance(v, (str, bytes)) else 32 for row in rows for v in row)
        if self.read_bytes > MAX_TOTAL_READ_BYTES:
            raise _Bound('Accounting inputs exceed the operation bound')
        self.queries[key] = rows
        return rows

    def row(self, identifier):
        if not _hex(identifier, 24):
            raise ValueError('Invalid accounting job identity')
        if identifier not in self.rows:
            raw = self.read(self.host._job_path(identifier))
            row = _json(raw)
            request = row.get('request') if isinstance(row, dict) else None
            if (type(request) is not dict or row.get('id') != identifier or row.get('mode') not in MODES
                    or any(row.get(k) != request.get(k) for k in ('mode', 'provider', 'depth'))
                    or row.get('provider') not in {'none', 'groq'}
                    or row.get('status') not in {'queued', 'running', 'completed', 'failed', 'cancelled', 'interrupted'}):
                raise ValueError('Saved accounting job authority changed')
            self.rows[identifier] = row
        return self.rows[identifier]

    def artifact(self, row, ref, expected):
        if type(ref) is not dict or not _hex(ref.get('sha256')) or type(ref.get('path')) is not str:
            raise ValueError('Invalid saved accounting source reference')
        path = (self.host.root / ref['path']).resolve()
        if path != expected.resolve():
            raise ValueError('Saved accounting source path changed')
        raw = self.read(path)
        if sha256(raw).hexdigest() != ref['sha256']:
            raise ValueError('Saved accounting source bytes changed')
        return _json(raw)

    def pack(self, row):
        identifier = row['id']
        if identifier not in self.packs:
            refs=row.get('result',{})
            if type(refs) is not dict:raise ValueError('Invalid saved pack inventory')
            ref = refs.get('pack.json')
            self.packs[identifier] = (self.artifact(row, ref, self.host.directory/'runs'/identifier/'pack.json')
                                      if ref is not None else None)
        return self.packs[identifier]

    def stage(self, row, name):
        return self.artifact(row, row.get('stage_checkpoints', {}).get(name),
                             self.host.directory/'runs'/row['id']/'stages'/(name+'.json'))

    def receipts(self, row, provider):
        key = 'usage_receipts' if provider == 'groq' else 'typesafe_receipts'
        values = row.get(key, {})
        if type(values) is not dict or len(values) > 2048:
            self.gap('receipt_inventory_invalid', row['id'], provider)
            return []
        allowed = GROQ_RECEIPT_FIELDS if provider == 'groq' else TYPESAFE_RECEIPT_FIELDS
        result = []
        for identifier, receipt in sorted(values.items()):
            if (type(receipt) is not dict or set(receipt) - allowed
                    or receipt.get('reservation_id') != identifier):
                self.gap('receipt_public_fields_invalid', row['id'], provider,
                         'A captured receipt has invalid identity or nonpublic fields and was not exported.')
                continue
            if any(type(value) not in (str,int,bool,type(None)) or type(value) is str and len(value)>512
                   for key,value in receipt.items() if key!='binding'):
                self.gap('receipt_public_fields_invalid',row['id'],provider)
                continue
            if provider == 'typesafe':
                binding = receipt.get('binding')
                if (type(binding) is not dict or set(binding) - {
                        'job_id', 'decision_index', 'query_sha256', 'snapshot_sha256', 'commit_sha',
                        'annotation_sha256', 'evidence_sha256'}):
                    self.gap('receipt_public_fields_invalid', row['id'], provider)
                    continue
                if any(type(value) not in (str,int,bool,type(None)) or type(value) is str and len(value)>512
                       for value in binding.values()):
                    self.gap('receipt_public_fields_invalid',row['id'],provider)
                    continue
                if binding.get('job_id') != row['id']:
                    self.gap('receipt_owner_mismatch', row['id'], provider)
                    continue
            result.append({'receipt_sha256': digest(receipt), 'receipt': deepcopy(receipt)})
        return result

    def attempt_count(self,row,provider):
        if provider=='groq' and row['mode'] in {'adaptive_questions','check_plan','claim_review'}:
            from .artifact_accounting import validate_artifact_accounting
            try:return validate_artifact_accounting(row)
            except (ValueError,TypeError,KeyError):
                self.gap('attempt_inventory_invalid',row['id'],provider)
                return None
        field = ('relation_attempts' if row['mode']=='issue_relation' else 'pipeline_attempts') if provider=='groq' else 'controller_attempts'
        attempts = row.get(field)
        if attempts is None:
            return None
        try:
            if type(attempts) is not list or len(attempts)>32:
                raise ValueError('Invalid attempt inventory')
            previous = None
            decision_slots = set()
            for index,attempt in enumerate(attempts,1):
                if type(attempt) is not dict: raise ValueError('Invalid attempt')
                if field=='relation_attempts':
                    expected={'schema':'issue_relation_attempt_v1','owner_job_id':row['id'],
                        'source_sha256':row['request']['comparison_source_sha256'],
                        'policy_sha256':row['request']['policy_sha256'],'call_index':index,'max_calls':2}
                    if digest(attempt)!=digest(expected) or index>2: raise ValueError('Changed comparison attempt')
                else:
                    count_key='provider_calls' if provider=='groq' else 'controller_calls'
                    maximum_key='max_provider_calls' if provider=='groq' else 'max_controller_calls'
                    required={'schema','context_sha256','stage',count_key,maximum_key}
                    if provider=='typesafe': required|={'decision_index','owner_job_id'}
                    expected_schema='product_pipeline_call_attempt_v1' if provider=='groq' else 'product_typesafe_call_attempt_v1'
                    count=attempt.get(count_key);maximum=attempt.get(maximum_key)
                    if (set(attempt)!=required or attempt['schema']!=expected_schema
                            or not _hex(row.get('pipeline_context_sha256'))
                            or attempt['context_sha256']!=row['pipeline_context_sha256']
                            or type(count) is not int or type(maximum) is not int
                            or not 1<=count<=maximum<=9 or previous is not None and count!=previous+1
                            or type(attempt['stage']) is not str):
                        raise ValueError('Changed owned provider attempt')
                    if provider=='typesafe' and (maximum!=4 or attempt['owner_job_id']!=row['id']
                            or type(attempt['decision_index']) is not int or not 1<=attempt['decision_index']<=4
                            or attempt['stage']!='planned_decision_'+str(attempt['decision_index'])
                            or attempt['decision_index'] in decision_slots):
                        raise ValueError('Changed owned controller attempt')
                    if provider=='typesafe':decision_slots.add(attempt['decision_index'])
                    previous=count
            if provider=='typesafe':
                receipts=row.get('typesafe_receipts',{})
                if type(receipts) is not dict:raise ValueError('Changed controller receipt inventory')
                owned_slots=set()
                for receipt in receipts.values():
                    binding=receipt.get('binding') if type(receipt) is dict else None
                    index=binding.get('decision_index') if type(binding) is dict else None
                    if type(index) is not int or index not in decision_slots or index in owned_slots:
                        raise ValueError('Changed controller reservation slot')
                    owned_slots.add(index)
            if field=='relation_attempts':
                owners=row.get('relation_receipt_owners',{});receipts=row.get('usage_receipts',{})
                if type(owners) is not dict or type(receipts) is not dict or set(owners)!=set(receipts):
                    raise ValueError('Changed comparison receipt inventory')
                slots=set()
                for identifier,owner in owners.items():
                    if (type(owner) is not dict or set(owner)!={'owner_job_id','call_index','source_sha256'}
                            or owner['owner_job_id']!=row['id'] or type(owner['call_index']) is not int
                            or not 1<=owner['call_index']<=len(attempts) or owner['call_index'] in slots
                            or owner['source_sha256']!=row['request']['comparison_source_sha256']):
                        raise ValueError('Changed comparison receipt owner')
                    slots.add(owner['call_index'])
            return len(attempts)
        except (ValueError,KeyError,TypeError):
            self.gap('attempt_inventory_invalid',row['id'],provider)
            return None

    def node(self, row):
        if row['id'] in self.nodes:
            return
        if len(self.nodes) >= MAX_NODES:
            raise _Bound('Accounting chain exceeds the owner bound')
        try:
            pack = self.pack(row)
        except (ValueError, OSError):
            pack = None
            self.gap('source_pack_unavailable', row['id'])
        captures = {}
        for provider in ('groq', 'typesafe'):
            receipts = self.receipts(row, provider)
            applicable = (row['provider'] == 'groq' or bool(receipts)) if provider == 'groq' else (
                row['request'].get('controller') == 'jev' or row.get('controller') == 'jev'
                or any(k in row for k in ('controller_attempts', 'typesafe_receipts', 'typesafe_decisions', 'typesafe_recovery'))
                or bool((pack or {}).get('controller_decisions')))
            attempts = self.attempt_count(row,provider)
            if not applicable:
                captures[provider] = {'applicable': False, 'tracking_available': True,
                                      'attempted_calls': 0, 'receipts': []}
            else:
                captures[provider] = {'applicable': True,
                    'tracking_available': (row.get('usage_tracking') is True if provider == 'groq'
                                           else type(row.get('typesafe_receipts')) is dict),
                    'attempted_calls': attempts,
                    'receipts': receipts}
        # Finalization has a separately authenticated zero-call authority. A
        # source receipt accidentally copied into it remains visible as a gap.
        refs=row.get('result',{})
        ref=refs.get('pack.json') if isinstance(refs,dict) else None
        self.nodes[row['id']] = {'job_id': row['id'], 'mode': row['mode'], 'job_status': row['status'],
            'provider': row['provider'], 'request_sha256': digest(row['request']),
            'job_sha256': sha256(self.files[self.host._job_path(row['id']).resolve()]).hexdigest(),
            'pack_sha256': ref['sha256'] if pack is not None and ref is not None else None,
            'providers': captures}

    def _registry(self, name, sql, params):
        rows = self.query(name, sql, params)
        if len(rows) != 1:
            raise ValueError('Missing or ambiguous saved accounting intent')
        return rows[0]

    def _edge(self, child, parent):
        request = child['request']; mode = child['mode']; identifier = child['id']
        child_pack = self.pack(child); parent_pack = self.pack(parent)
        parent_ref = parent.get('result', {}).get('pack.json')
        parent_sha = parent_ref['sha256'] if parent_pack is not None else None
        proof = {'child_request_sha256': digest(request), 'parent_request_sha256': digest(parent['request']),
                 'parent_job_id': parent['id'], 'parent_pack_sha256': parent_sha}
        if mode in FORKS:
            from .fork_origins import canonical_request
            if digest(canonical_request(request)) != digest(request):
                raise ValueError('Noncanonical saved fork request')
            if parent_sha is None:
                raise ValueError('Missing fork parent pack')
            link = (child_pack or {}).get('context_selection' if mode == 'context' else 'followup')
            if link is None and 'execution_input' in child.get('stage_checkpoints', {}):
                context = self.stage(child, 'execution_input')['payload']['context']
                if context.get('fork', {}).get('origin') != {'job_id': identifier, 'request': request}:
                    raise ValueError('Fork journal authority changed')
                link = context['fork'].get('context_selection' if mode == 'context' else 'followup')
            expected = request.get('parent_pack_sha256')
            if link is not None:
                if link.get('parent_job_id') != parent['id'] or link.get('parent_pack_sha256') != parent_sha:
                    raise ValueError('Fork source binding changed')
                proof['link_sha256'] = digest(link)
            elif expected is None:
                raise ValueError('Legacy fork lacks frozen source bytes')
            if expected is not None and expected != parent_sha:
                raise ValueError('Fork source pack changed')
            if mode == 'check_followup':
                from .check_followups import CheckFollowups
                saved = self._registry('check_followups.sqlite',
                    'SELECT target_job FROM intents WHERE request_sha=?', (CheckFollowups.request_sha(request),))
                if saved != (identifier,): raise ValueError('Check follow-up intent changed')
                proof['intent_sha256'] = _sql_hash(saved)
            if mode == 'memory_followup':
                key = digest({k: request[k] for k in ('parent_job_id','parent_pack_sha256','selection_sha256')})
                saved = self._registry('memory_followups.sqlite',
                    'SELECT request_sha,target_job,selection FROM intents WHERE preview_key=?', (key,))
                if saved[:2] != (digest(request), identifier): raise ValueError('Memory follow-up intent changed')
                selection = _json(saved[2])
                if selection.get('sha256') != request['selection_sha256']:
                    raise ValueError('Memory selection binding changed')
                proof['intent_sha256'] = _sql_hash(saved)
        elif mode == 'planned_continuation':
            from .planned_continuations import validate_request
            validate_request(request, saved=True)
            if parent_sha != request['source_pack_sha256']:
                raise ValueError('Planned source pack changed')
            saved = self._registry('planned_continuations.sqlite',
                'SELECT request_sha,target_job FROM intents WHERE source_job=?', (parent['id'],))
            if saved != (digest(request), identifier): raise ValueError('Planned continuation intent changed')
            plan = (parent_pack or {}).get('workflow_plan', {})
            waiting = plan.get('waiting') or {}
            if plan.get('sha256') != request['workflow_plan_sha256'] or waiting.get('sha256') != request['waiting_sha256']:
                raise ValueError('Planned waiting source changed')
            link = (child_pack or {}).get('continued_from')
            if link is not None and (link.get('parent_job_id') != parent['id'] or link.get('parent_pack_sha256') != parent_sha):
                raise ValueError('Planned child source changed')
            proof['intent_sha256'] = _sql_hash(saved)
        elif mode == 'stage_resume':
            saved = self._registry('stage_recovery.sqlite',
                'SELECT binding_sha,target_job,binding,requires_api FROM intents WHERE source_job=?', (parent['id'],))
            binding = _json(saved[2])
            expected = {'mode':'stage_resume','provider':binding['provider'],'depth':binding['depth'],
                'source_job_id':parent['id'],'binding_sha256':binding['sha256'],'requires_api':bool(saved[3])}
            if (request != expected or type(request.get('requires_api')) is not bool
                    or saved[:2] != (binding.get('sha256'), identifier)
                    or binding.get('sha256') != digest({k:v for k,v in binding.items() if k!='sha256'})
                    or binding.get('source_request_sha256') != digest(parent['request'])):
                raise ValueError('Recovery source binding changed')
            chain = binding.get('source_chain')
            if chain is not None:
                if not isinstance(chain, list) or not 1 <= len(chain) <= 8 or chain[-1]['job_id'] != parent['id']:
                    raise ValueError('Recovery source owner chain changed')
                for entry in chain:
                    owner = self.row(entry['job_id'])
                    usage = {'tracking':owner.get('usage_tracking'),'receipts':owner.get('usage_receipts',{})}
                    if (entry['request_sha256'] != digest(owner['request'])
                            or entry['attempts_sha256'] != digest(owner.get('pipeline_attempts'))
                            or entry['usage_sha256'] != digest(usage)):
                        raise ValueError('Recovery source accounting changed')
            else:
                usage = {'tracking':parent.get('usage_tracking'),'receipts':parent.get('usage_receipts',{})}
                if binding.get('usage_sha256') != digest(usage): raise ValueError('Recovery source usage changed')
            if child_pack is not None and (child_pack.get('recovered_from') or {}).get('source') != binding:
                raise ValueError('Recovery child binding changed')
            proof['intent_sha256'] = _sql_hash(saved)
        elif mode in {'resume', 'finalization'}:
            names = ('verification_input',) if mode == 'resume' else ('verification_input','verification')
            refs = {name: parent.get('stage_checkpoints', {}).get(name) for name in names}
            for name in names: self.stage(parent, name)
            if mode == 'resume':
                saved = self._registry('resumes.sqlite',
                    'SELECT checkpoint_sha256,target_job FROM intents WHERE source_job=?', (parent['id'],))
                if (saved != (request.get('checkpoint_sha256'),identifier)
                        or request['checkpoint_sha256'] != refs['verification_input']['sha256']):
                    raise ValueError('Verifier continuation source changed')
                link = (child_pack or {}).get('resumed_from')
            else:
                saved = self._registry('finalizations.sqlite',
                    'SELECT request_sha,input_sha,verification_sha,target_job FROM intents WHERE source_job=?', (parent['id'],))
                if (saved != (digest(parent['request']), refs['verification_input']['sha256'], refs['verification']['sha256'],identifier)
                        or request.get('source_request_sha256') != saved[0]
                        or request.get('verification_input_sha256') != saved[1]
                        or request.get('verification_sha256') != saved[2]
                        or request.get('provider') != 'none'):
                    raise ValueError('Finalization source changed')
                link = (child_pack or {}).get('finalized_from')
            if child_pack is not None and (type(link) is not dict or link.get('source_job_id') != parent['id']):
                raise ValueError('Saved export source differs from its intent')
            proof['intent_sha256'] = _sql_hash(saved)
        elif mode in SIDECARS:
            self._sidecar_intent(child, parent)
            if request.get('source_pack_sha256') != parent_sha or parent_sha is None:
                raise ValueError('Sidecar source pack changed')
            proof['source_request_sha256'] = digest(request)
        else:
            raise ValueError('Unsupported cost lineage edge')
        return {'child_job_id':identifier,'parent_job_id':parent['id'],'kind':mode,
                'proof_sha256':digest(proof),'source_pack_sha256':parent_sha}

    def _sidecar_intent(self, row, source):
        mode = row['mode']; request = row['request']; name, confirmation, depth = SIDECARS[mode]
        if (request.get('confirmation') != confirmation or row['provider'] != 'groq' or row['depth'] != depth
                or request.get('source_job_id') != source['id']):
            raise ValueError('Sidecar source authority changed')
        if mode != 'issue_relation':
            if set(request) != {'mode','provider','depth','source_job_id','source_pack_sha256','confirmation'}:
                raise ValueError('Sidecar request fields changed')
            saved = self._registry(name, 'SELECT target_job FROM intents WHERE source_job=? AND source_sha=?',
                                   (source['id'], request['source_pack_sha256']))
            if saved != (row['id'],): raise ValueError('Sidecar intent changed')
        else:
            saved = self._registry(name,
                'SELECT source_job,source_sha,request,inputs FROM intents WHERE target_job=?', (row['id'],))
            witness = _json(saved[3]); frozen = _json(saved[2])
            if (saved[:2] != (source['id'],request['source_pack_sha256']) or frozen != request
                    or witness.get('schema') != 'issue_relation_inputs_v1' or witness.get('owner_job_id') != row['id']
                    or witness.get('request') != request or witness.get('sha256') != digest({k:v for k,v in witness.items() if k!='sha256'})
                    or witness.get('source',{}).get('left',{}).get('reference',{}).get('job_id') != source['id']
                    or witness['source']['left']['reference'].get('pack_sha256') != request['source_pack_sha256']):
                raise ValueError('Issue comparison frozen intent changed')
        return saved

    def root_authority(self,row):
        """A mode label alone cannot erase a saved child's billed ancestry."""
        mode=row['mode'];request=row['request']
        try:
            pack=self.pack(row)
            derived={'followup','context_selection','continued_from','recovered_from','resumed_from','finalized_from'}
            if pack is not None and any(pack.get(key) for key in derived):
                raise ValueError('An original cost owner carries derived provenance')
            if mode=='repository':
                allowed={'mode','provider','depth','repository','revision','max_requests','refresh','automatic'}
                if (set(request)-allowed or request.get('provider')!='none' or request.get('depth')!='index'
                        or type(request.get('repository')) is not str or type(request.get('revision')) is not str
                        or pack is not None or row.get('usage_receipts') or row.get('typesafe_receipts')
                        or row.get('pipeline_attempts') or row.get('controller_attempts')):
                    raise ValueError('Repository sync cost authority changed')
                return
            from .workbench import validate_request
            if digest(validate_request(request))!=digest(request):
                raise ValueError('Original cost request is not canonical')
            case=(pack or {}).get('case')
            if case is None:
                if 'execution_input' in row.get('stage_checkpoints',{}):
                    context=self.stage(row,'execution_input')['payload']['context']
                    if context.get('fork') or context.get('continuation'):
                        raise ValueError('A root cannot inherit a recorded fork context')
                    case=context.get('case')
                elif 'verification_input' in row.get('stage_checkpoints',{}):
                    case=self.stage(row,'verification_input')['payload']['prepared']['case']
            if type(case) is not dict:
                raise ValueError('Original source identity is unavailable')
            if mode=='local':
                expected={'case_id':'local:'+digest(request)[:16],'repository':request['repository'].strip(),
                    'revision':request['revision'].strip(),'problem_statement':request['issue_text'],'issue_created_at':None}
                if digest(case)!=digest(expected):raise ValueError('Local cost source identity changed')
            elif mode=='github':
                from issueintel.integrations.github import github_input
                wanted=github_input(request['issue_url']);actual=github_input(case.get('issue_url',''))
                if (wanted.repository.casefold()!=actual.repository.casefold() or wanted.issue_number!=actual.issue_number
                        or case.get('case_id')!=actual.issue_url or case.get('repository','').casefold()!=actual.repository.casefold()
                        or case.get('revision')!=request['revision']):
                    raise ValueError('GitHub cost source identity changed')
            elif mode=='main_dev':
                if case.get('case_id')!=request['case_id']:raise ValueError('Main Dev cost source identity changed')
            elif (case.get('case_id')!='synthetic:parse-list' or case.get('repository')!='synthetic/example'
                    or case.get('problem_statement')!='parser.parse_list fails on None input. Inspect how missing values are handled.'):
                raise ValueError('Synthetic cost source identity changed')
        except (ValueError,KeyError,TypeError,OSError):
            self.gap('root_authority_unverified',row['id'],None,
                     'The declared original owner could not be bound to a canonical root request and recorded source identity.')

    def walk(self, identifier, path=()):
        if identifier in path:
            self.gap('lineage_cycle', identifier)
            return
        row = self.row(identifier); self.node(row)
        if row['mode'] in ORIGINAL:
            self.root_authority(row)
            return
        if len(path) >= MAX_ANCESTRY:
            self.gap('lineage_depth_bound', identifier)
            return
        request = row['request']
        parent_id = request.get('parent_job_id') if row['mode'] in FORKS else request.get('source_job_id')
        if parent_id in path or parent_id == identifier:
            self.gap('lineage_cycle', identifier)
            return
        try:
            parent = self.row(parent_id)
            edge = self._edge(row, parent)
        except _Bound:
            self.gap('capture_read_bound', identifier)
            return
        except (ValueError, KeyError, TypeError, OSError, sqlite3.Error):
            self.gap('lineage_source_unavailable', identifier,
                     detail='A typed source edge could not be checked against its recorded bytes and intent.')
            return
        if len(self.edges) >= MAX_EDGES or parent_id not in self.nodes and len(self.nodes)>=MAX_NODES:
            self.gap('lineage_edge_bound', identifier)
            return
        self.edges.append(edge)
        self.walk(parent_id, path+(identifier,))

    def discover(self):
        # Inspect metadata only to locate typed sidecars, never group by issue
        # identity. Missing optional DBs are benign when no recorded owner names
        # that service; a deleted registry with an existing job remains a gap.
        listing = sorted(p.name for p in self.host.jobs.glob('*.json') if _hex(p.stem,24))
        self.listing = listing
        if len(listing) > MAX_SCAN_JOBS:
            self.gap('sidecar_discovery_bound', self.selected)
        sources = {key:value for key,value in self.nodes.items() if value['mode'] not in SIDECARS and value['pack_sha256']}
        hints = {}
        confirmations={value[1] for value in SIDECARS.values()}
        for name in listing[:MAX_SCAN_JOBS]:
            identifier = name[:-5]
            if identifier in self.nodes:
                continue
            try:
                path=self.host._job_path(identifier).resolve()
                row=_json(self.read(path));request=row.get('request')
                request=request if type(request) is dict else {}
                matched=False
                artifact_hint=(row.get('mode') in SIDECARS or request.get('mode') in SIDECARS
                               or request.get('confirmation') in confirmations)
                if artifact_hint and request.get('source_job_id') in sources:
                    source_id = request['source_job_id']
                    if request.get('source_pack_sha256') == sources[source_id]['pack_sha256']:
                        hints[identifier] = source_id
                        matched=True
                if not matched:
                    self.discovery_metadata[path]=self._discovery_identity(row)
                    self.files.pop(path,None)
            except _Bound:
                self.gap('sidecar_discovery_bound', self.selected)
                break
            except (ValueError,KeyError,TypeError,OSError):
                self.gap('sidecar_discovery_bound',self.selected,'groq',
                         'A saved metadata record could not be inspected for exact-source artifact ownership.')
                continue
        targets = dict(hints)
        for source_id, node in sources.items():
            for mode, (name, _, _) in SIDECARS.items():
                try:
                    rows = self.query(name,
                        'SELECT target_job FROM intents WHERE source_job=? AND source_sha=? ORDER BY target_job',
                        (source_id,node['pack_sha256']))
                    for (target,) in rows:
                        if not _hex(target,24): raise ValueError('Invalid sidecar target identity')
                        targets[target] = source_id
                except FileNotFoundError:
                    pass
                except _Bound:
                    self.gap('sidecar_discovery_bound', source_id,'groq')
                except (ValueError, sqlite3.Error):
                    self.gap('sidecar_registry_unavailable', source_id,'groq')
        for identifier, source_id in sorted(targets.items()):
            if identifier in self.nodes:
                continue
            try:
                row = self.row(identifier)
                if row['mode'] not in SIDECARS:
                    raise ValueError('A sidecar registry points at an investigation')
                source = self.row(source_id)
                edge = self._edge(row,source)
                self.node(row)
                self.edges.append(edge)
            except _Bound:
                self.gap('sidecar_discovery_bound', source_id,'groq')
                break
            except (ValueError,KeyError,TypeError,OSError,sqlite3.Error):
                self.gap('sidecar_source_unavailable', identifier,'groq',
                         'A named sidecar owner could not be checked against its exact source pack and durable intent.')

    @staticmethod
    def _discovery_identity(row):
        request=row.get('request') if type(row) is dict else None
        request=request if type(request) is dict else {}
        return digest({'id':row.get('id'),'mode':row.get('mode'),
                       'source_job_id':request.get('source_job_id'),
                       'source_pack_sha256':request.get('source_pack_sha256')})

    def stable(self):
        for path, raw in self.files.items():
            try:
                if path.stat().st_size != len(raw):
                    return False
                with path.open('rb') as stream:
                    if stream.read(len(raw)+1)!=raw:return False
            except OSError:
                return False
        if any(path.exists() for path in self.missing):
            return False
        if self.listing is not None:
            if sorted(p.name for p in self.host.jobs.glob('*.json') if _hex(p.stem,24)) != self.listing:
                return False
        for path,identity in self.discovery_metadata.items():
            try:
                if path.stat().st_size>MAX_SINGLE_FILE_BYTES:return False
                with path.open('rb') as stream:
                    raw=stream.read(MAX_SINGLE_FILE_BYTES+1)
                if len(raw)>MAX_SINGLE_FILE_BYTES or self._discovery_identity(_json(raw))!=identity:
                    return False
            except (ValueError,OSError):
                return False
        for (path, sql, params), rows in self.queries.items():
            try:
                with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.25)) as db:
                    db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,MAX_SINGLE_FILE_BYTES)
                    db.execute('PRAGMA query_only=ON')
                    current = db.execute(sql, params).fetchmany(MAX_NODES+1)
                if _sql_hash(current) != _sql_hash(rows): return False
            except sqlite3.Error:
                return False
        return True

    def snapshot(self):
        self.walk(self.selected)
        self.discover()
        value = {'schema':'investigation_usage_snapshot_v1','selected_job_id':self.selected,
                 'nodes':sorted(self.nodes.values(),key=lambda r:r['job_id']),
                 'edges':sorted(self.edges,key=lambda r:(r['child_job_id'],r['parent_job_id'],r['kind'])),
                 'gaps':sorted(self.gaps,key=lambda r:(r['job_id'] or '',r['provider'] or '',r['code'],r['detail']))}
        return dict(value,sha256=digest(value))


class InvestigationUsageJobs:
    def __init__(self, host):
        self.host = host

    def capture(self, identifier):
        if not _hex(identifier,24):
            raise ValueError('Choose an exact investigation job identity')
        for _ in range(MAX_CAPTURE_ATTEMPTS):
            reader = _Capture(self.host,identifier)
            snapshot = reader.snapshot()
            if reader.stable():
                return snapshot
        raise ValueError('Investigation accounting changed during the bounded capture; retry the read')

    def bundle(self, identifier):
        from .investigation_usage import build
        return build(self.capture(identifier))
