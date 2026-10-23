"""Durable paid-call slots for source-bound product artifacts, without IO replay."""
from copy import deepcopy
import json
import re

from issueintel.runtime.io import digest


LIMITS={'adaptive_questions':1,'check_plan':2,'claim_review':2}
FIELDS=('artifact_attempts','artifact_receipt_owners','usage_receipts')


def _hex(value,size=64):
    return type(value) is str and re.fullmatch('[a-f0-9]{'+str(size)+'}',value) is not None


def initialize_artifact_accounting(host,row,**changes):
    """One dispatch may initialize tracking once; a repeated worker cannot reset it."""
    with host.lock:
        saved=json.loads(host._job_path(row['id']).read_bytes())
        if saved.get('id')!=row['id']:raise ValueError('Artifact dispatch owner changed')
        if (any(key in saved for key in FIELDS[:2]) or saved.get('usage_receipts') or saved.get('usage_tracking') is True
                or saved.get('status') not in {'queued','running'} or
                digest({key:saved.get(key) for key in ('id','mode','provider','depth','request')})!=
                digest({key:row.get(key) for key in ('id','mode','provider','depth','request')})):
            row.clear();row.update(saved)
            raise ValueError('Artifact accounting was initialized already or dispatch authority changed')
        host._save(row,**changes,artifact_attempts=[],artifact_receipt_owners={},usage_tracking=True,usage_receipts={})


def validate_artifact_accounting(row,source=None):
    """Return owned attempt count, or None for an unchanged legacy record.

    A complete attempt may have no receipt when admission/transport stopped
    before reservation capture. Cost reporting preserves that uncertainty.
    """
    present={key for key in FIELDS[:2] if key in row}
    if not present:return None
    if present!=set(FIELDS[:2]) or row.get('mode') not in LIMITS or row.get('provider')!='groq':
        raise ValueError('Invalid source artifact accounting authority')
    request=row.get('request');identifier=row.get('id');maximum=LIMITS[row['mode']]
    if (type(request) is not dict or not _hex(identifier,24)
            or not _hex(request.get('source_job_id'),24) or not _hex(request.get('source_pack_sha256'))):
        raise ValueError('Invalid source artifact input binding')
    attempts=row['artifact_attempts'];owners=row['artifact_receipt_owners'];receipts=row.get('usage_receipts')
    if (type(attempts) is not list or len(attempts)>maximum or type(owners) is not dict
            or type(receipts) is not dict or set(owners)!=set(receipts) or len(receipts)>len(attempts)
            or row.get('usage_tracking') is not True):
        raise ValueError('Changed source artifact accounting inventory')
    source_sha=digest(source) if source is not None else None
    for index,attempt in enumerate(attempts,1):
        if type(attempt) is not dict or not _hex(attempt.get('source_sha256')):
            raise ValueError('Invalid source artifact attempt')
        if source_sha is None:source_sha=attempt['source_sha256']
        expected={'schema':'source_artifact_attempt_v1','owner_job_id':identifier,'mode':row['mode'],
            'source_job_id':request['source_job_id'],'source_pack_sha256':request['source_pack_sha256'],
            'source_sha256':source_sha,'call_index':index,'max_calls':maximum}
        if digest(attempt)!=digest(expected):raise ValueError('Changed source artifact attempt owner or bound')
    slots=set()
    for reservation,owner in owners.items():
        if (not _hex(reservation,32) or type(owner) is not dict
                or set(owner)!={'owner_job_id','call_index','source_sha256'}
                or owner.get('owner_job_id')!=identifier or type(owner.get('call_index')) is not int
                or not 1<=owner['call_index']<=len(attempts) or owner['call_index'] in slots
                or owner['source_sha256']!=source_sha or type(receipts[reservation]) is not dict
                or receipts[reservation].get('reservation_id')!=reservation
                or receipts[reservation].get('stage')!=row['mode']):
            raise ValueError('Changed source artifact reservation owner')
        slots.add(owner['call_index'])
    return len(attempts)


class ArtifactCalls:
    """Check fresh ledger bytes before each call and capture its own receipt.

    Receipts are saved from the fresh row, so an in-flight source/request change
    cannot be silently rolled back by an old worker object. Source guards decide
    whether output can be published after the terminal receipt has been kept.
    """
    def __init__(self,host,row,source):
        self.host=host;self.row=row;self.source=deepcopy(source)
        self.authority={key:deepcopy(row[key]) for key in ('id','mode','provider','depth','request')}
        validate_artifact_accounting(row,source)

    def fresh(self,*,authority=True):
        saved=json.loads(self.host._job_path(self.row['id']).read_bytes())
        if saved.get('id')!=self.row['id']:raise ValueError('Artifact owner identity changed')
        if authority and digest({key:saved.get(key) for key in self.authority})!=digest(self.authority):
            self.row.clear();self.row.update(saved)
            raise ValueError('Artifact execution authority changed')
        # Receipt publication retains a terminal result even if request metadata
        # changed in flight, but cannot overwrite a conflicting ledger update.
        projected=dict(saved,mode=self.authority['mode'],provider=self.authority['provider'],request=self.authority['request'])
        try:
            validate_artifact_accounting(projected,self.source)
            changed=any(digest(saved.get(key))!=digest(self.row.get(key)) for key in FIELDS)
        except (ValueError,KeyError,TypeError):
            self.row.clear();self.row.update(saved)
            raise
        if changed:
            self.row.clear();self.row.update(saved)
            raise ValueError('Artifact accounting changed while executing')
        return saved

    def begin(self):
        self.fresh()
        index=len(self.row['artifact_attempts'])+1;mode=self.row['mode']
        if index>LIMITS[mode]:raise ValueError('Source artifact provider-call bound exhausted')
        request=self.row['request']
        record={'schema':'source_artifact_attempt_v1','owner_job_id':self.row['id'],'mode':mode,
            'source_job_id':request['source_job_id'],'source_pack_sha256':request['source_pack_sha256'],
            'source_sha256':digest(self.source),'call_index':index,'max_calls':LIMITS[mode]}
        self.row['artifact_attempts'].append(record)
        self.host._save(self.row,artifact_attempts=self.row['artifact_attempts'])
        self.fresh()

    def capture(self,receipt):
        saved=self.fresh(authority=False)
        if type(receipt) is not dict or not _hex(receipt.get('reservation_id'),32) or not self.row['artifact_attempts']:
            raise ValueError('A source artifact receipt requires its owned call slot')
        reservation=receipt['reservation_id'];index=len(self.row['artifact_attempts'])
        owner={'owner_job_id':self.row['id'],'call_index':index,'source_sha256':digest(self.source)}
        previous=self.row['artifact_receipt_owners'].get(reservation)
        if previous is not None and previous!=owner:raise ValueError('A reservation cannot move between source artifact calls')
        wrapped=dict(receipt,stage=self.authority['mode']);prior=self.row['usage_receipts'].get(reservation)
        if prior is not None and prior.get('status')!='reserved' and digest(prior)!=digest(wrapped):
            raise ValueError('A terminal source artifact receipt cannot be overwritten')
        owners=deepcopy(self.row['artifact_receipt_owners']);owners[reservation]=owner
        receipts=deepcopy(self.row['usage_receipts']);receipts[reservation]=wrapped
        check=dict(self.row,artifact_receipt_owners=owners,usage_receipts=receipts)
        validate_artifact_accounting(check,self.source)
        self.host._save(saved,usage_tracking=True,artifact_receipt_owners=owners,usage_receipts=receipts)
        self.row.update(artifact_receipt_owners=owners,usage_receipts=receipts,usage_tracking=True)
        for key in ('mode','provider','depth','request'):
            self.row[key]=deepcopy(saved[key])
        if receipt.get('status')=='reserved':self.host._check_cancelled(self.row['id'])
