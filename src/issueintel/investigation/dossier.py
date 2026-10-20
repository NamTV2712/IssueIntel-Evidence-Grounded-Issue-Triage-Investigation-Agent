"""Read-only product case timelines and explicit, unverified hypothesis links.

All findings retain their original report and snapshot. A/B comparisons compare
reported labels with saved model predictions; neither is an executed test.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re
import sqlite3
import unicodedata

from issueintel.integrations.github import github_input
from issueintel.runtime.io import digest
from .comparison import compare_packs
from .report import build_report


SCHEMA = 'investigation_dossier_v1'
MODES = {'local','github','example','followup','context','clarification','check_followup','resume','finalization','planned_continuation'}
ORIGINAL = {'local','github','example'}
FLAGS = {'provenance':'user_report_unverified','causal_verified':False,
         'fix_verified':False,'independent_human':False,'evaluation_performed':False}
LIMITATIONS = [
    'Reported observations, model predictions and manual hypothesis links remain unverified.',
    'A matching or conflicting A/B label is not semantic support, falsification or causal proof.',
    'Source and child hypotheses are not identified by equal local IDs; cross-run links require explicit user rationale.',
    'A dossier describes saved product investigations, not benchmark eligibility or a quality evaluation.',
]


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _seal(value):
    return dict(value,sha256=digest(value))


def prediction_delta(receipt, *, superseded=()):
    """Deterministic label comparison, never an interpretation of source text."""
    checks={row['id']:row for row in receipt['checks']}
    result=[]
    for event in receipt['observations']:
        observed=event['observation'];check=checks[observed['check_id']]
        actual=observed['outcome'];tested=observed['tested_commit_sha']
        applicability=('unknown_commit' if tested is None else 'same_analysis_commit'
                       if tested==receipt['source']['commit_sha'] else 'different_commit')
        for prediction in check['predictions']:
            predicted=prediction['outcome']
            comparison=('undetermined' if actual not in {'A','B'} or predicted not in {'A','B'}
                        else 'matches_prediction' if actual==predicted else 'conflicts_with_prediction')
            result.append({'observation_id':event['observation_id'],'observation_sha256':event['sha256'],
                'check_id':check['id'],'check_sha256':digest(check),'hypothesis_id':prediction['hypothesis_id'],
                'predicted_outcome':predicted,'prediction_reason':prediction['reason'],
                'reported_outcome':actual,'comparison':comparison,'revision_applicability':applicability,
                'tested_commit_sha':tested,'analysis_commit_sha':receipt['source']['commit_sha'],
                'superseded_later':event['observation_id'] in superseded,
                'scope':'reported_label_vs_saved_model_prediction','conditions_verified':False,**FLAGS})
    return result


class Dossiers:
    def __init__(self,host):
        self.host=host

    def _raw(self,identifier):
        value=json.loads(self.host._job_path(identifier).read_bytes())
        if type(value) is not dict:raise ValueError('Invalid dossier product record')
        request=value.get('request')
        if (value.get('id')!=identifier or type(request) is not dict
                or value.get('mode') not in MODES
                or any(value.get(key)!=request.get(key) for key in ('mode','provider','depth'))):
            raise ValueError('Dossier product job authority changed')
        return value

    def _origin(self,identifier,*,authenticate=False):
        from .workbench import validate_request
        seen=set()
        for _ in range(11):
            if identifier in seen:raise ValueError('Dossier source cycle')
            seen.add(identifier)
            row=self._raw(identifier);request=row['request'];mode=request['mode']
            if mode in ORIGINAL:
                if digest(validate_request(request))!=digest(request):
                    raise ValueError('Dossier original request changed')
                if authenticate:
                    if self.host.job(identifier)['status']=='completed':
                        original,_,_=self.host._load_pack(identifier)
                    else:
                        original=json.loads(self.host.stage_bytes(identifier,'verification_input'))['payload']['prepared']
                    case=original['case']
                    if mode=='local':
                        expected={'case_id':'local:'+digest(request)[:16],'repository':request['repository'].strip(),
                                  'revision':request['revision'].strip(),'problem_statement':request['issue_text'],
                                  'issue_created_at':None}
                        if digest(case)!=digest(expected):raise ValueError('Original local dossier request differs from its report')
                    elif mode=='github':
                        requested=github_input(request['issue_url']);saved=github_input(case['issue_url'])
                        if (requested.repository.casefold()!=saved.repository.casefold()
                                or requested.issue_number!=saved.issue_number or case['case_id']!=saved.issue_url
                                or case['repository'].casefold()!=saved.repository.casefold()
                                or case['revision']!=request['revision']):
                            raise ValueError('Original GitHub dossier identity changed')
                    elif (case['case_id']!='synthetic:parse-list' or case['repository']!='synthetic/example'
                            or case['problem_statement']!='parser.parse_list fails on None input. Inspect how missing values are handled.'):
                        raise ValueError('Original example dossier identity changed')
                if mode=='github':
                    source=github_input(request['issue_url'])
                    identity={'kind':'github','repository':source.repository.casefold(),
                              'issue_url':source.issue_url.casefold(),'query_sha256':None}
                elif mode=='local':
                    identity={'kind':'local','repository':request['repository'].strip(),
                              'issue_url':None,'query_sha256':_sha(request['issue_text'].encode())}
                else:
                    identity={'kind':'synthetic','repository':'synthetic/example',
                              'issue_url':None,'query_sha256':None}
                return identifier,dict(identity,sha256=digest(identity))
            identifier=request.get('source_job_id') if mode in {'resume','finalization','planned_continuation'} else request.get('parent_job_id')
        raise ValueError('Dossier ancestry exceeds the product bound')

    def _db(self):
        db=sqlite3.connect(self.host.directory/'dossiers.sqlite',timeout=30)
        db.execute('CREATE TABLE IF NOT EXISTS links (identity TEXT NOT NULL, revision INTEGER NOT NULL, '
                   'value TEXT NOT NULL, PRIMARY KEY(identity,revision))')
        return db

    @staticmethod
    def _initial(identity):
        return _seal({'schema':'dossier_hypothesis_links_v1','identity_sha256':identity,
                      'revision':0,'events':[],**FLAGS})

    def _links(self,identity,db=None):
        owned=db is None
        if owned:db=self._db()
        try:
            state=self._initial(identity)
            for revision,raw in db.execute('SELECT revision,value FROM links WHERE identity=? ORDER BY revision',(identity,)):
                event=json.loads(raw)
                expected={'schema','identity_sha256','revision','parent_sha256','link_id','relation',
                          'bindings','recorded_at','sha256'}|set(FLAGS)
                if (type(event) is not dict or set(event)!=expected or type(revision) is not int
                        or type(event['revision']) is not int or revision!=state['revision']+1 or event['revision']!=revision
                        or revision>200 or event['identity_sha256']!=identity or event['schema']!='dossier_hypothesis_link_v1'
                        or event['parent_sha256']!=state['sha256'] or event['link_id']!='link_'+str(revision)
                        or digest({k:event[k] for k in FLAGS})!=digest(FLAGS)
                        or event['sha256']!=digest({k:v for k,v in event.items() if k!='sha256'})
                        or type(event['recorded_at']) is not str):
                    raise ValueError('Dossier hypothesis-link history changed')
                timestamp=datetime.fromisoformat(event['recorded_at'])
                if timestamp.utcoffset()!=timezone.utc.utcoffset(timestamp):
                    raise ValueError('Dossier relation timestamp must have UTC offset')
                state=_seal({'schema':state['schema'],'identity_sha256':identity,'revision':revision,
                             'events':state['events']+[event],**FLAGS})
            return state
        finally:
            if owned:db.close()

    @staticmethod
    def _relation(value,runs,events):
        keys={'parent_job_id','child_job_id','parent_hypothesis_id','child_hypothesis_id',
              'kind','reason','evidence_refs','supersedes'}
        if type(value) is not dict or set(value)!=keys:
            raise ValueError('Choose an exact hypothesis relation')
        if any(type(value[k]) is not str or not re.fullmatch('[a-f0-9]{24}',value[k])
               for k in ('parent_job_id','child_job_id')):
            raise ValueError('Choose exact parent and child job IDs')
        parent=runs.get(value['parent_job_id']);child=runs.get(value['child_job_id'])
        if (not parent or not child or not parent['report'] or not child['report']
                or child['parent_job_id']!=parent['job_id']):
            raise ValueError('Choose an authenticated parent and child report')
        ids=lambda row:{h['id'] for h in row['report']['hypotheses']}
        before,after=value['parent_hypothesis_id'],value['child_hypothesis_id']
        kind=value['kind']
        if (type(kind) is not str or any(v is not None and type(v) is not str for v in (before,after))
                or kind not in {'retained','refined','new','not_carried'}
                or (kind in {'retained','refined'} and (before not in ids(parent) or after not in ids(child)))
                or (kind=='new' and (before is not None or after not in ids(child)))
                or (kind=='not_carried' and (after is not None or before not in ids(parent)))
                or type(value['reason']) is not str or not value['reason'].strip() or len(value['reason'])>4000
                or any(unicodedata.category(c) in {'Cc','Cf','Cs'} and c not in '\t\r\n' for c in value['reason'])):
            raise ValueError('Invalid source-bound hypothesis relation')
        refs=value['evidence_refs']
        if type(refs) is not list or not 1<=len(refs)<=12:
            raise ValueError('A hypothesis relation needs explicit source references')
        known={(row['job_id'],e['id']) for row in (parent,child) for e in row['report']['evidence']}
        seen=set()
        for ref in refs:
            if type(ref) is not dict or set(ref)!={'job_id','evidence_id'}:
                raise ValueError('Invalid hypothesis source reference')
            if any(type(ref[k]) is not str for k in ('job_id','evidence_id')):
                raise ValueError('Invalid hypothesis source reference identity')
            key=(ref['job_id'],ref['evidence_id'])
            if key not in known or key in seen:raise ValueError('Unknown or duplicate hypothesis source reference')
            seen.add(key)
        supersedes=value['supersedes']
        if supersedes is not None:
            if type(supersedes) is not str:raise ValueError('Invalid superseded relation ID')
            target=next((e for e in events if e['link_id']==supersedes),None)
            replaced={e['relation']['supersedes'] for e in events}
            if (not target or supersedes in replaced
                    or any(target['relation'][k]!=value[k] for k in ('parent_job_id','child_job_id'))):
                raise ValueError('Choose an active relation from this parent-child transition')
        return deepcopy(value),{'parent':deepcopy(parent['binding']),'child':deepcopy(child['binding'])}

    def _validate_links(self,state,runs):
        previous=[]
        for event in state['events']:
            relation,bindings=self._relation(event['relation'],runs,previous)
            if digest(bindings)!=digest(event['bindings']):raise ValueError('Hypothesis relation source changed')
            previous.append(event)
        return state

    def build(self,identifier):
        host=self.host
        selected,selected_raw,_=host._load_pack(identifier)
        host._clarification_product(identifier,selected)
        _,identity=self._origin(identifier,authenticate=True)
        selected_sha=_sha(selected_raw)
        packs={};runs=[];plans=[];observations=[];claim_reviews=[];timeline=[];warnings=[]
        for path in sorted(host.jobs.glob('*.json')):
            job_id=path.stem
            try:
                raw=self._raw(job_id)
                root,candidate=self._origin(job_id)
            except (ValueError,KeyError,TypeError,OSError):
                continue
            if digest(candidate)!=digest(identity):continue
            row=host.job(job_id);request=raw['request'];mode=row['mode']
            parent=(request.get('source_job_id') if mode in {'resume','finalization','planned_continuation'} else request.get('parent_job_id'))
            run={'job_id':job_id,'root_job_id':root,'parent_job_id':parent,'status':row['status'],
                 'mode':mode,'provider':row['provider'],'depth':row['depth'],
                 'created_at':row['created_at'],'updated_at':row.get('updated_at',row['created_at']),
                 'request_sha256':digest(request),'binding':None,'report':None,'integrity':'request_metadata_only'}
            if row['status']=='completed':
                try:
                    pack,data,_=host._load_pack(job_id);host._clarification_product(job_id,pack)
                    self._origin(job_id,authenticate=True)
                    report=build_report(pack,source_pack_sha256=_sha(data),job_id=job_id)
                    run.update(binding={'job_id':job_id,'pack_sha256':_sha(data),'report_sha256':report['sha256'],
                        'query_sha256':report['source']['query_sha256'],'snapshot_sha256':pack['snapshot']['snapshot_sha256'],
                        'commit_sha':pack['snapshot']['commit_sha']},report=report,integrity='source_replayed')
                    packs[job_id]=pack
                except (ValueError,KeyError,TypeError,OSError):
                    if job_id==identifier:raise
                    run['integrity']='unavailable';warnings.append({'job_id':job_id,'artifact':'pack','reason':'source_replay_failed'})
            runs.append(run)
            timeline.append({'event_id':job_id+':run','kind':'investigation','job_id':job_id,
                'occurred_at':run['created_at'],'sha256':(run['binding'] or {}).get('pack_sha256',run['request_sha256']),
                'hash_kind':'serialized_pack_bytes' if run['binding'] else 'canonical_request'})
        by_id={row['job_id']:row for row in runs}
        for run in runs:
            job_id=run['job_id']
            if run['report'] is None:continue
            try:
                rows=host.claim_review_history(job_id)['review_jobs']
                for review_job in rows:
                    event={'event_id':review_job['id']+':claim_review','kind':'claim_review','job_id':job_id,
                           'review_job_id':review_job['id'],'occurred_at':review_job['created_at'],'status':review_job['status']}
                    if review_job['status']=='completed':
                        bundle=host.claim_review_bundle(review_job['id'])
                        claim_reviews.append({'review_job_id':review_job['id'],'source_job_id':job_id,'bundle':bundle})
                        event['sha256']=bundle['sha256']
                    timeline.append(event)
            except (ValueError,KeyError,TypeError,OSError,sqlite3.DatabaseError):
                warnings.append({'job_id':job_id,'artifact':'claim_review','reason':'source_replay_failed'})
            try:
                checklist=host.plan(job_id)
                journal=host.check_journal(job_id)
                run['checklist']=checklist;run['check_journal']=journal
                for event in journal['events']:
                    timeline.append({'event_id':job_id+':report:'+event['observation_id'],'kind':'report_observation',
                        'job_id':job_id,'occurred_at':event['recorded_at'],'sha256':event['sha256'],'event':event})
                plan_rows=host.check_plan_history(job_id)['plan_jobs']
            except (ValueError,KeyError,TypeError,OSError,sqlite3.DatabaseError):
                warnings.append({'job_id':job_id,'artifact':'check_history','reason':'source_replay_failed'})
                plan_rows=[]
            for row in plan_rows:
                plan_id=row['id']
                timeline.append({'event_id':plan_id+':plan','kind':'check_plan','job_id':job_id,
                    'plan_job_id':plan_id,'occurred_at':row['created_at'],'status':row['status']})
                if row['status']!='completed':continue
                try:
                    plan=host.check_plan_bundle(plan_id);journal=host.observation_journals.load(plan,plan_id)
                    plans.append({'plan_job_id':plan_id,'source_job_id':job_id,'plan':plan})
                    observations.append({'plan_job_id':plan_id,'journal':journal})
                    for event in journal['events']:
                        timeline.append({'event_id':plan_id+':'+event['observation_id'],'kind':'check_observation',
                            'job_id':job_id,'plan_job_id':plan_id,'occurred_at':event['recorded_at'],
                            'sha256':event['sha256'],'event':event})
                except (ValueError,KeyError,TypeError,OSError,sqlite3.DatabaseError):
                    warnings.append({'job_id':plan_id,'artifact':'check_plan','reason':'source_replay_failed'})
        transitions=[]
        journal_by_plan={row['plan_job_id']:row['journal'] for row in observations}
        for run in runs:
            parent_id=run['parent_job_id'];job_id=run['job_id']
            if job_id not in packs or parent_id not in packs:continue
            parent,child=packs[parent_id],packs[job_id]
            views=deepcopy([parent,child])
            for view in views:
                if view.get('followup'):view['case']['case_id']=view['followup']['source_case_id']
            comparison=compare_packs(*views)
            receipt=(child.get('followup') or {}).get('check_observations')
            transition={'parent_job_id':parent_id,'child_job_id':job_id,
                'parent_binding':by_id[parent_id]['binding'],'child_binding':run['binding'],
                'comparison':comparison,'observation_source':None,'prediction_delta':[],
                'reason':'user_context' if child.get('followup') or child.get('continued_from') else 'selected_repository_context',
                'semantic_hypothesis_correspondence':'not_inferred'}
            if receipt:
                journal=journal_by_plan.get(receipt['source']['plan_job_id'])
                superseded={e['observation']['supersedes'] for e in (journal or {}).get('events',[])}
                transition.update(reason='selected_reported_observations',observation_source=deepcopy(receipt),
                                  prediction_delta=prediction_delta(receipt,superseded=superseded))
            transitions.append(transition)
        links=self._validate_links(self._links(identity['sha256']),by_id)
        for event in links['events']:
            timeline.append({'event_id':identity['sha256']+':'+event['link_id'],'kind':'hypothesis_link',
                'job_id':event['relation']['child_job_id'],'occurred_at':event['recorded_at'],
                'sha256':event['sha256'],'event':event})
        timeline.sort(key=lambda row:(row['occurred_at'],row['event_id']))
        runs.sort(key=lambda row:(row['created_at'],row['job_id']))
        source={'selected_job_id':identifier,'selected_pack_sha256':selected_sha,'identity_sha256':identity['sha256']}
        return _seal({'schema':SCHEMA,'source':source,'issue':identity,'runs':runs,'plans':plans,
                      'observations':observations,'claim_reviews':claim_reviews,'transitions':transitions,'timeline':timeline,
                      'hypothesis_links':links,'warnings':warnings,'limitations':list(LIMITATIONS),
                      'model_calls':0,'evaluation_performed':False})

    def append_link(self,identifier,request):
        if (type(request) is not dict or set(request)!={'source_pack_sha256','expected_sha256','relation'}
                or any(type(request[k]) is not str or not re.fullmatch('[a-f0-9]{64}',request[k])
                       for k in ('source_pack_sha256','expected_sha256'))):
            raise ValueError('An exact source-bound relation request is required')
        dossier=self.build(identifier)
        if dossier['source']['selected_pack_sha256']!=request['source_pack_sha256']:
            raise ValueError('Dossier selection changed')
        runs={row['job_id']:row for row in dossier['runs']}
        identity=dossier['issue']['sha256']
        db=self._db()
        try:
            db.execute('BEGIN IMMEDIATE')
            state=self._validate_links(self._links(identity,db),runs)
            if state['sha256']!=request['expected_sha256']:raise ValueError('Hypothesis links changed; reload before saving')
            if state['revision']>=200:raise ValueError('Dossier hypothesis-link limit reached')
            relation,bindings=self._relation(request['relation'],runs,state['events'])
            # Check only already-authenticated sources again, without relying on
            # cached UI metadata or holding any provider-facing transaction.
            selected,raw,_=self.host._load_pack(identifier)
            if _sha(raw)!=request['source_pack_sha256']:raise ValueError('Selected source changed')
            for binding in bindings.values():
                pack,raw,_=self.host._load_pack(binding['job_id'])
                self.host._clarification_product(binding['job_id'],pack)
                report=build_report(pack,source_pack_sha256=_sha(raw),job_id=binding['job_id'])
                if _sha(raw)!=binding['pack_sha256'] or report['sha256']!=binding['report_sha256']:
                    raise ValueError('Hypothesis relation source changed before save')
            revision=state['revision']+1
            event=_seal({'schema':'dossier_hypothesis_link_v1','identity_sha256':identity,'revision':revision,
                'parent_sha256':state['sha256'],'link_id':'link_'+str(revision),'relation':relation,'bindings':bindings,
                'recorded_at':datetime.now(timezone.utc).isoformat(),**FLAGS})
            db.execute('INSERT INTO links VALUES (?,?,?)',(identity,revision,json.dumps(event,ensure_ascii=False,sort_keys=True)))
            db.commit()
        except BaseException:
            db.rollback();raise
        finally:db.close()
        return self.build(identifier)
