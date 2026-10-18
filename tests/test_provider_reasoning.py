from dataclasses import replace
from io import BytesIO
import json
import pytest
from urllib.error import HTTPError
from issueintel.investigation.providers import GroqProvider
from test_runtime import configured


def test_gpt_oss_uses_include_reasoning_and_retains_durable_usage(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "synthetic-test-key")
    seen = []
    def transport(request, timeout):
        seen.append(json.loads(request.data))
        return BytesIO(json.dumps({"choices":[{"finish_reason":"stop","message":{"content":"{}"}}],
            "usage":{"prompt_tokens":100,"completion_tokens":20,"total_tokens":120}}).encode())
    provider = GroqProvider(replace(configured(tmp_path), reasoning_effort="high"), transport=transport)
    provider.complete_json([{"role":"user","content":"synthetic fixture only"}], 256)
    assert seen[0]["reasoning_effort"] == "high"
    assert seen[0]["include_reasoning"] is False and "reasoning_format" not in seen[0]
    assert provider.last_reservation and provider.last_usage["total_tokens"] == 120


def test_provider_error_retains_safe_diagnostics_and_receipt_without_source_or_key(tmp_path,monkeypatch):
    from issueintel.investigation.providers import ProviderError,failure_metadata
    monkeypatch.setenv('GROQ_API_KEY','synthetic-test-key')
    def transport(request,timeout):
        payload={'error':{'code':'json_validate_failed','message':'Synthetic validation failure',
                          'failed_generation':'<tool_call> secret-source-and-key </tool_call>'}}
        raise HTTPError(request.full_url,400,'Bad Request',{},BytesIO(json.dumps(payload).encode()))
    provider=GroqProvider(configured(tmp_path),transport=transport)
    with pytest.raises(ProviderError) as caught:provider.complete_json([{'role':'user','content':'Return JSON'}],64)
    metadata=failure_metadata(caught.value)
    assert metadata['http_status']==400 and metadata['native_call_marker'] is True
    assert metadata['failed_generation_is_json_object'] is False and len(metadata['failed_generation_sha256'])==64
    assert 'secret-source-and-key' not in json.dumps(metadata) and 'synthetic-test-key' not in json.dumps(metadata)
    assert provider.config.ledger().receipt(provider.last_reservation)['status']=='failed_bound'


@pytest.mark.parametrize('variant',['valid','wrong_name','multiple','truncated','not_object','bad_call_type'])
def test_forced_function_is_exactly_one_typed_proposal_and_keeps_usage(tmp_path,monkeypatch,variant):
    from issueintel.investigation.providers import ProviderError
    from issueintel.annotations.structured import obj,text
    monkeypatch.setenv('GROQ_API_KEY','synthetic-test-key')
    schema=obj({'action':text()})
    def transport(request,timeout):
        body=json.loads(request.data)
        assert 'response_format' not in body and body['parallel_tool_calls'] is False
        assert body['tool_choice']=={'type':'function','function':{'name':'issueintel_response'}}
        assert body['tools'][0]['function']['parameters']==schema
        call={'type':'function','function':{'name':'other' if variant=='wrong_name' else 'issueintel_response',
              'arguments':'[]' if variant=='not_object' else '{"action":"finish"}'}}
        payload={'choices':[{'finish_reason':'length' if variant=='truncated' else 'tool_calls',
                    'message':{'content':None,'tool_calls':[None] if variant=='bad_call_type' else [call,call] if variant=='multiple' else [call]}}],
                 'usage':{'prompt_tokens':100,'completion_tokens':20,'total_tokens':120}}
        return BytesIO(json.dumps(payload).encode())
    provider=GroqProvider(configured(tmp_path),transport=transport)
    if variant=='valid':assert provider.complete_function([{'role':'user','content':'Submit a response'}],schema=schema,max_output_tokens=256)=={'action':'finish'}
    else:
        with pytest.raises(ProviderError):provider.complete_function([{'role':'user','content':'Submit a response'}],schema=schema,max_output_tokens=256)
    assert provider.config.ledger().receipt(provider.last_reservation)['status']=='settled'


@pytest.mark.parametrize('variant',['valid','unexpected_tool','truncated'])
def test_strict_json_schema_has_one_output_channel_and_keeps_full_bounds(tmp_path,monkeypatch,variant):
    from issueintel.investigation.providers import ProviderError
    from issueintel.investigation.policy_agent import ACTION_SCHEMA
    monkeypatch.setenv('GROQ_API_KEY','synthetic-test-key')
    def transport(request,timeout):
        body=json.loads(request.data)
        assert body['response_format']=={'type':'json_schema','json_schema':{
            'name':'issueintel_response','strict':True,'schema':ACTION_SCHEMA}}
        assert not {'tools','tool_choice','parallel_tool_calls'} & body.keys()
        assert body['stream'] is False
        message={'content':json.dumps({'choice_id':'STOP','reason':'Evidence exhausted'})}
        if variant=='unexpected_tool':message['tool_calls']=[{'type':'function','function':{'name':'read_code','arguments':'{}'}}]
        return BytesIO(json.dumps({'choices':[{'finish_reason':'length' if variant=='truncated' else 'stop','message':message}],
             'usage':{'prompt_tokens':100,'completion_tokens':20,'total_tokens':120}}).encode())
    provider=GroqProvider(configured(tmp_path),transport=transport)
    if variant=='valid':
        assert provider.complete_structured([{'role':'user','content':'Classify a selection'}],schema=ACTION_SCHEMA,max_output_tokens=256)['choice_id']=='STOP'
    else:
        with pytest.raises(ProviderError):provider.complete_structured([{'role':'user','content':'Propose an action'}],schema=ACTION_SCHEMA,max_output_tokens=256)
    assert provider.config.ledger().receipt(provider.last_reservation)['status']=='settled'
