from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from io import BytesIO
import json
import sqlite3

import pytest

from issueintel.runtime.io import digest
from issueintel.runtime.typesafe import (MODEL, ENDPOINT, PRICE_SOURCE, TypesafeConfig,
    TypesafeClient, TypesafeError, build_request, validate_response, validate_receipt,
    _Ledger, _NoRedirect)


def configured(tmp_path, *, requests=30, daily='1', monthly='2', enabled=True):
    folder=tmp_path/'configs'
    folder.mkdir(exist_ok=True)
    (folder/'typesafe.toml').write_text(
        f'provider="typesafe"\nmodel="{MODEL}"\nenabled={str(enabled).lower()}\n'
        f'daily_requests={requests}\ndaily_usd="{daily}"\nmonthly_usd="{monthly}"\n'
        'price_file="configs/typesafe_prices.json"\nledger="data/runtime/typesafe.sqlite"\n',encoding='utf-8')
    (folder/'typesafe_prices.json').write_text(json.dumps({
        'checked_at':datetime.now(timezone.utc).date().isoformat(),'currency':'USD','source':PRICE_SOURCE,
        'models':{MODEL:{'input_per_million':'0.042','output_per_million':'0',
                         'per_request_context':64000,'per_question_context':32000}}}),encoding='utf-8')
    return TypesafeConfig.load(tmp_path)


def binding(index=1,job='a'*24):
    return {'job_id':job,'decision_index':index,'query_sha256':'b'*64,'snapshot_sha256':'c'*64,
            'commit_sha':'d'*40,'annotation_sha256':'e'*64,'evidence_sha256':'f'*64}


def answer(**updates):
    value={'model':MODEL,'answers':{'next_action':{'type':'choice','choice':'retrieve',
           'confidence':0.8,'probabilities':{'retrieve':0.8,'verify':0.2,'stop':0.0}}},
           'usage':{'input_tokens':1000,'output_tokens':35}}
    value.update(updates)
    return value


ACTIONS=['retrieve','verify','stop']
STATE={'evidence_count':2,'hypotheses_count':3,'missing_information':['counter evidence'],'rounds_remaining':2}


def records(config):
    with sqlite3.connect(config.ledger_file) as db:
        return [json.loads(row[0]) for row in db.execute('SELECT receipt FROM typesafe_calls ORDER BY rowid')]


def test_disabled_default_and_key_never_enables_calls(tmp_path,monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY','synthetic-key')
    config=configured(tmp_path,requests=0,daily='0',monthly='0',enabled=False)
    assert config.status()=={'ready':False,'enabled':False,'key_configured':True,'reason':'disabled',
        'model':MODEL,'max_calls_per_run':4,'per_call_usd_bound':'0.002688',
        'daily_requests':0,'daily_usd':'0','monthly_usd':'0'}
    with pytest.raises(TypesafeError,match='disabled'):
        TypesafeClient(config,transport=lambda *a,**k:pytest.fail('network')).decide(STATE,ACTIONS,binding())
    assert not config.ledger_file.exists()


def test_missing_key_and_config_deny(tmp_path,monkeypatch):
    with pytest.raises(TypesafeError,match='invalid_configuration'):
        TypesafeConfig.load(tmp_path)
    config=configured(tmp_path)
    monkeypatch.delenv('TYPESAFE_API_KEY',raising=False)
    with pytest.raises(TypesafeError,match='missing_api_key'):
        TypesafeClient(config).decide(STATE,ACTIONS,binding())
    assert not config.ledger_file.exists()


@pytest.mark.parametrize('change',[
    {'requests':0},{'daily':'0'},{'monthly':'0'},{'daily':'NaN'},{'monthly':'Infinity'},
    {'daily':'-1'},{'requests':-1}])
def test_enabled_requires_separate_positive_limits(tmp_path,change):
    with pytest.raises(TypesafeError):
        configured(tmp_path,**change)


def test_bad_price_date_and_price_deny_before_http(tmp_path,monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY','synthetic-key')
    config=configured(tmp_path)
    facts=json.loads(config.price_file.read_text())
    facts['checked_at']=(datetime.now(timezone.utc).date()-timedelta(days=31)).isoformat()
    config.price_file.write_text(json.dumps(facts))
    assert config.status()['reason']=='invalid_or_stale_prices'
    with pytest.raises(TypesafeError,match='invalid_or_stale_prices'):
        TypesafeClient(config,transport=lambda *a,**k:pytest.fail('network')).decide(STATE,ACTIONS,binding())
    facts['checked_at']=datetime.now(timezone.utc).date().isoformat()
    facts['models'][MODEL]['output_per_million']='0.0001'
    config.price_file.write_text(json.dumps(facts))
    assert config.status()['ready'] is False


@pytest.mark.parametrize('key,value',[('ledger','../outside.sqlite'),('ledger','configs/publish.sqlite'),('price_file','../outside.json')])
def test_config_paths_stay_inside_private_workspace(tmp_path,key,value):
    configured(tmp_path)
    path=tmp_path/'configs/typesafe.toml'
    text=path.read_text()
    old='data/runtime/typesafe.sqlite' if key=='ledger' else 'configs/typesafe_prices.json'
    path.write_text(text.replace(old,value))
    with pytest.raises(TypesafeError,match='invalid_configuration'):
        TypesafeConfig.load(tmp_path)


def test_typed_request_is_fixed_canonical_and_bounded():
    body=build_request(STATE,ACTIONS,MODEL)
    assert set(body)=={'model','state','questions'}
    assert body['questions']['next_action']['type']=='choice'
    assert set(body['questions']['next_action']['criteria'])==set(ACTIONS)
    assert build_request(STATE,['verify','stop'],MODEL)['questions']['next_action']['criteria'].keys()=={'verify','stop'}
    for allowed in ([],['ask'],['stop','stop'],['__import__'],('stop',)):
        with pytest.raises(TypesafeError):
            build_request(STATE,allowed,MODEL)
    for state in ({'text':'x'*17000},{'v':float('nan')},{1:'not a JSON object key'}):
        with pytest.raises(TypesafeError):
            build_request(state,ACTIONS,MODEL)
    with pytest.raises(TypesafeError):
        build_request(STATE,ACTIONS,'jev-latest')


def test_call_owns_receipt_before_http_and_settles(tmp_path,monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY','synthetic-secret')
    config=configured(tmp_path)
    published=[]
    def transport(request,**kwargs):
        assert published[0]['status']=='reserved'
        assert records(config)[0]['status']=='reserved'
        assert request.full_url==ENDPOINT and request.method=='POST' and kwargs=={'timeout':20}
        assert request.get_header('Authorization')=='Bearer synthetic-secret'
        assert 'max_output_tokens' not in json.loads(request.data)
        return BytesIO(json.dumps(answer()).encode())
    result=TypesafeClient(config,transport=transport,on_receipt=published.append).decide(STATE,ACTIONS,binding())
    assert result['action']=='retrieve' and result['confidence']==0.8
    assert [item['status'] for item in published]==['reserved','settled']
    receipt=result['receipt']
    assert receipt['micro_usd_charged_or_reserved']==42
    assert receipt['input_tokens']==1000 and receipt['output_tokens']==35
    assert validate_receipt(receipt,binding(),digest(build_request(STATE,ACTIONS,MODEL)),result['response'])==receipt
    assert 'synthetic-secret' not in json.dumps(records(config))
    assert 'counter evidence' not in json.dumps(records(config))
    assert records(config)==[receipt]


def test_http_failure_retains_bound_redacts_and_never_retries(tmp_path,monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY','synthetic-secret')
    config=configured(tmp_path)
    calls=[]
    def fail(*args,**kwargs):
        calls.append(1)
        raise RuntimeError('synthetic-secret private issue text')
    client=TypesafeClient(config,transport=fail)
    with pytest.raises(TypesafeError) as caught:
        client.decide(STATE,ACTIONS,binding())
    assert str(caught.value)=='Typesafe unavailable: request_failed'
    assert caught.value.receipt['status']=='failed_bound'
    assert caught.value.receipt['micro_usd_charged_or_reserved']==2688
    assert validate_receipt(caught.value.receipt)
    with pytest.raises(TypesafeError,match='duplicate_attempt'):
        TypesafeClient(config,transport=fail).decide(STATE,ACTIONS,binding())
    assert calls==[1]


@pytest.mark.parametrize('usage',[None,{}, {'input_tokens':0,'output_tokens':20},
    {'input_tokens':True,'output_tokens':20},{'input_tokens':100,'output_tokens':-1}])
def test_missing_or_malformed_usage_keeps_full_bound(tmp_path,monkeypatch,usage):
    monkeypatch.setenv('TYPESAFE_API_KEY','synthetic-key')
    config=configured(tmp_path)
    result=TypesafeClient(config,transport=lambda *a,**k:BytesIO(json.dumps(answer(usage=usage)).encode())).decide(STATE,ACTIONS,binding())
    receipt=result['receipt']
    assert receipt['status']=='missing_usage_bound' and receipt['micro_usd_charged_or_reserved']==2688
    assert validate_receipt(receipt,response=result['response'])


def test_usage_breach_durably_locks_future_calls(tmp_path,monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY','synthetic-key')
    config=configured(tmp_path)
    transport=lambda *a,**k:BytesIO(json.dumps(answer(usage={'input_tokens':64001,'output_tokens':1})).encode())
    with pytest.raises(TypesafeError,match='usage_bound_breach') as caught:
        TypesafeClient(config,transport=transport).decide(STATE,ACTIONS,binding())
    assert caught.value.receipt['status']=='breach'
    assert validate_receipt(caught.value.receipt)
    with pytest.raises(TypesafeError,match='ledger_locked'):
        TypesafeClient(config,transport=lambda *a,**k:pytest.fail('network')).decide(STATE,ACTIONS,binding(2))


@pytest.mark.parametrize('bad',[
    {'model':'jev-latest'},
    {'answers':{'next_action':{'type':'choice','choice':'retrieve','confidence':True,'probabilities':{'retrieve':0.8,'verify':0.2,'stop':0}}}},
    {'answers':{'next_action':{'type':'choice','choice':'stop','confidence':0.8,'probabilities':{'retrieve':0.8,'verify':0.2,'stop':0}}}},
    {'answers':{'next_action':{'type':'choice','choice':'retrieve','confidence':0.8,'probabilities':{'retrieve':0.8,'verify':0.2}}}},
    {'answers':{'next_action':{'type':'choice','choice':'retrieve','confidence':0.8,'probabilities':{'retrieve':0.8,'verify':0.1,'stop':0}}}},
])
def test_invalid_answer_is_not_refunded_or_returned(tmp_path,monkeypatch,bad):
    monkeypatch.setenv('TYPESAFE_API_KEY','synthetic-key')
    config=configured(tmp_path)
    with pytest.raises(TypesafeError,match='invalid_response') as caught:
        TypesafeClient(config,transport=lambda *a,**k:BytesIO(json.dumps(answer(**bad)).encode())).decide(STATE,ACTIONS,binding())
    receipt=caught.value.receipt
    assert caught.value.response is None and receipt['status']=='failed_bound'
    assert receipt['response_sha256']==digest(answer(**bad))
    assert validate_receipt(receipt)


def test_reservations_are_atomic_for_duplicate_and_daily_caps(tmp_path):
    config=configured(tmp_path,requests=3)
    ledger=_Ledger(config)
    def reserve(index):
        try:
            return ledger.reserve(binding(job=f'{index:024x}'),digest({'index':index}),datetime.now(timezone.utc).date().isoformat())
        except TypesafeError:
            return None
    with ThreadPoolExecutor(max_workers=10) as pool:
        values=list(pool.map(reserve,range(20)))
    assert len([value for value in values if value])==3
    assert len(records(config))==3
    other=replace(config,ledger_file=tmp_path/'data/duplicate.sqlite')
    duplicate=_Ledger(other)
    def same(_):
        try:
            duplicate.reserve(binding(),digest(STATE),datetime.now(timezone.utc).date().isoformat())
            return True
        except TypesafeError:
            return False
    with ThreadPoolExecutor(max_workers=10) as pool:
        assert sum(pool.map(same,range(10)))==1


@pytest.mark.parametrize('daily,monthly,reason',[('0.002687','1','daily_usd_limit'),('1','0.002687','monthly_usd_limit')])
def test_reservation_enforces_independent_usd_caps(tmp_path,monkeypatch,daily,monthly,reason):
    monkeypatch.setenv('TYPESAFE_API_KEY','synthetic-key')
    config=configured(tmp_path,daily=daily,monthly=monthly)
    with pytest.raises(TypesafeError,match=reason):
        TypesafeClient(config,transport=lambda *a,**k:pytest.fail('network')).decide(STATE,ACTIONS,binding())


def test_binding_index_limits_and_hashes_deny_before_reservation(tmp_path,monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY','synthetic-key')
    config=configured(tmp_path)
    for bad in ({**binding(),'decision_index':5},{**binding(),'decision_index':True},
                {**binding(),'commit_sha':'bad'},{**binding(),'query_sha256':'bad'},
                {**binding(),'extra':'unexpected'}):
        with pytest.raises(TypesafeError,match='invalid_binding'):
            TypesafeClient(config,transport=lambda *a,**k:pytest.fail('network')).decide(STATE,ACTIONS,bad)
    assert not config.ledger_file.exists()


def test_oversized_response_retains_bound(tmp_path,monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY','synthetic-key')
    config=configured(tmp_path)
    with pytest.raises(TypesafeError,match='response_too_large') as caught:
        TypesafeClient(config,transport=lambda *a,**k:BytesIO(b' '*65537)).decide(STATE,ACTIONS,binding())
    assert caught.value.receipt['status']=='failed_bound'


def test_receipt_ownership_failure_prevents_http(tmp_path,monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY','synthetic-key')
    config=configured(tmp_path)
    def persist(receipt):
        raise OSError('private path')
    with pytest.raises(TypesafeError,match='receipt_persistence_failed') as caught:
        TypesafeClient(config,transport=lambda *a,**k:pytest.fail('network'),on_receipt=persist).decide(STATE,ACTIONS,binding())
    assert caught.value.receipt['status']=='failed_bound'
    assert records(config)[0]['status']=='failed_bound'


def test_cancellation_before_http_propagates_without_provider_fallback(tmp_path,monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY','synthetic-key')
    config=configured(tmp_path)
    def cancel(receipt):
        raise InterruptedError('cancelled')
    client=TypesafeClient(config,transport=lambda *a,**k:pytest.fail('network'),on_receipt=cancel)
    with pytest.raises(InterruptedError):
        client.decide(STATE,ACTIONS,binding())
    assert client.last_receipt['status']=='failed_bound'
    assert records(config)[0]['status']=='failed_bound'


def test_cancellation_after_http_keeps_settled_receipt(tmp_path,monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY','synthetic-key')
    config=configured(tmp_path)
    published=[]
    def cancel(receipt):
        published.append(receipt)
        if receipt['status']!='reserved':
            raise InterruptedError('cancelled')
    client=TypesafeClient(config,transport=lambda *a,**k:BytesIO(json.dumps(answer()).encode()),on_receipt=cancel)
    with pytest.raises(InterruptedError):
        client.decide(STATE,ACTIONS,binding())
    assert [receipt['status'] for receipt in published]==['reserved','settled']
    assert records(config)[0]['status']=='settled'


def test_receipt_tamper_and_redirect_rejected(tmp_path,monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY','synthetic-key')
    config=configured(tmp_path)
    result=TypesafeClient(config,transport=lambda *a,**k:BytesIO(json.dumps(answer()).encode())).decide(STATE,ACTIONS,binding())
    receipt=dict(result['receipt']);receipt['micro_usd_charged_or_reserved']=0
    receipt.pop('sha256');receipt['sha256']=digest(receipt)
    with pytest.raises(TypesafeError,match='invalid_receipt'):
        validate_receipt(receipt)
    receipt=dict(result['receipt']);receipt['input_tokens']=2000;receipt['micro_usd_charged_or_reserved']=84
    receipt.pop('sha256');receipt['sha256']=digest(receipt)
    with pytest.raises(TypesafeError,match='invalid_receipt'):
        validate_receipt(receipt,response=result['response'])
    receipt=dict(result['receipt']);receipt.update(status='missing_usage_bound',input_tokens=None,
        output_tokens=None,micro_usd_charged_or_reserved=2688)
    receipt.pop('sha256');receipt['sha256']=digest(receipt)
    with pytest.raises(TypesafeError,match='invalid_receipt'):
        validate_receipt(receipt,response=result['response'])
    with pytest.raises(TypesafeError,match='redirect_forbidden'):
        _NoRedirect().redirect_request(None,None,302,'redirect',{},'https://elsewhere.invalid/')


def test_price_month_rollover_keeps_monthly_usage(tmp_path):
    config=configured(tmp_path,monthly='0.004')
    ledger=_Ledger(config,clock=lambda:datetime(2026,10,17,tzinfo=timezone.utc))
    ledger.reserve(binding(),digest(STATE),'2026-10-17')
    ledger.clock=lambda:datetime(2026,10,18,tzinfo=timezone.utc)
    with pytest.raises(TypesafeError,match='monthly_usd_limit'):
        ledger.reserve(binding(2),digest(STATE),'2026-10-17')
