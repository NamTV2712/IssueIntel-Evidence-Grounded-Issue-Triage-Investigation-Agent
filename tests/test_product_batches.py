from copy import deepcopy
import hashlib
import json

import pytest

from issueintel.investigation import product_batches as batches
from issueintel.runtime.io import digest


def manifest(*requests):
    return {'schema': batches.MANIFEST_SCHEMA, 'run_key': 'author-run.1',
            'items': [{'key': 'item-'+str(index), 'request': request}
                      for index, request in enumerate(requests or ({'mode': 'example'},), 1)]}


def local(**overrides):
    return dict({'mode': 'local', 'repository': 'D:/repos/project',
                 'revision': 'refs/tags/v1.0', 'issue_text': 'Parser fails on null.'}, **overrides)


def seal(value):
    value = deepcopy(value)
    value.pop('sha256', None)
    return dict(value, sha256=digest(value))


def test_manifest_canonical_defaults_do_not_touch_source_or_call_model():
    seen = []
    def validator(request):
        seen.append(deepcopy(request))
        from issueintel.investigation.workbench import validate_request
        return validate_request(request)
    raw = manifest(local(repository='  D:/repos/project ', revision=' refs/tags/v1.0 '))
    before = deepcopy(raw)
    result = batches.validate_manifest(raw, request_validator=validator)
    assert raw == before
    assert seen == [raw['items'][0]['request']]
    assert result['items'][0]['request'] == local(provider='none', depth='quick')
    assert result is not raw


@pytest.mark.parametrize('input_request', [
    {'mode': 'main_dev', 'case_id': 'verified:private'},
    {'mode': 'followup', 'parent_job_id': 'a'*24, 'additional_context': 'more'},
    {'mode': 'example', 'depth': 'planned', 'provider': 'groq', 'controller': 'jev'},
    {'mode': 'example', 'provider': 'typesafe'},
    {'mode': 'example', 'model': 'openai/gpt-oss-20b'},
    {'mode': 'example', 'patch': 'restricted'},
    {'mode': 'example', 'controller': True},
    {'mode': [], 'provider': 'none'},
    {'mode': 'example', 'depth': False},
    {'mode': 'example', 'provider': []},
    local(issue_url='https://github.com/a/b/issues/1'),
    local(case_id='synthetic:fake'),
])
def test_manifest_denies_non_original_or_unknown_authority(input_request):
    with pytest.raises(ValueError):
        batches.validate_manifest(manifest(input_request))


@pytest.mark.parametrize('requests', [
    ({'mode': 'example'}, {'mode': 'example', 'provider': 'none', 'depth': 'quick'}),
    ({'mode': 'example', 'depth': 'planned'}, {'mode': 'example', 'depth': 'planned', 'controller': 'rules'}),
    (local(), local(repository=' D:/repos/project ', revision=' refs/tags/v1.0 ')),
    ({'mode': 'github', 'issue_url': 'https://github.com/Org/Repo/issues/12'},
     {'mode': 'github', 'issue_url': 'https://github.com/org/repo/issues/12', 'revision': 'HEAD'}),
])
def test_manifest_rejects_duplicate_canonical_requests(requests):
    with pytest.raises(ValueError, match='distinct'):
        batches.validate_manifest(manifest(*requests))


@pytest.mark.parametrize('mutation', [
    lambda x: x.update(schema='benchmark_batch_v1'),
    lambda x: x.update(run_key='../outside'),
    lambda x: x.update(run_key='a'*101),
    lambda x: x.update(run_key=''),
    lambda x: x.update(items=[]),
    lambda x: x.update(extra=True),
    lambda x: x['items'][0].update(key='-unsafe'),
    lambda x: x['items'][0].update(key='a'*65),
    lambda x: x['items'][0].update(extra=None),
    lambda x: x['items'].append(deepcopy(x['items'][0])),
])
def test_manifest_rejects_structural_errors(mutation):
    value = manifest()
    mutation(value)
    with pytest.raises(ValueError):
        batches.validate_manifest(value)


def test_manifest_item_and_utf8_bounds_apply_before_runtime_validation():
    value = manifest(*(local(issue_text='Issue '+str(i)) for i in range(21)))
    with pytest.raises(ValueError, match='twenty'):
        batches.validate_manifest(value)
    value = manifest(*(local(issue_text='á'*99000+str(i)) for i in range(3)))
    with pytest.raises(ValueError, match='size bound'):
        batches.validate_manifest(value, request_validator=lambda _: pytest.fail('Validation ran before byte bound'))
    value = manifest(*(local(issue_text='Issue '+str(i)) for i in range(20)))
    assert len(batches.validate_manifest(value)['items']) == 20


@pytest.mark.parametrize('raw', [
    '{"schema":"product_batch_manifest_v1","run_key":"a","run_key":"b","items":[]}',
    '{"schema":"product_batch_manifest_v1","run_key":"a","items":[{"key":"x","request":{"mode":"example","mode":"local"}}]}',
    '{"schema":"product_batch_manifest_v1","run_key":"a","items":NaN}',
    b'\xff', b'\x00', '[]', '{', '"text"',
])
def test_manifest_json_rejects_duplicate_fields_and_nonfinite_numbers(raw):
    with pytest.raises(ValueError):
        batches.parse_manifest(raw)


def test_manifest_json_read_and_request_upper_bounds_are_pure():
    raw = manifest({'mode': 'example'}, local(provider='groq', depth='quick'),
                   local(provider='groq', depth='deep'),
                   local(provider='groq', depth='planned'))
    expected = batches.validate_manifest(raw)
    assert batches.parse_manifest(json.dumps(raw).encode()) == expected
    assert [batches.call_upper_bound(row['request']) for row in expected['items']] == [0, 4, 6, 9]
    assert batches.MODEL == 'openai/gpt-oss-120b'


def test_stable_child_ids_are_scoped_to_author_run_and_item_key():
    assert batches.batch_id('run') == batches.batch_id('run')
    assert batches.stable_child_id('run', 'x') == batches.stable_child_id('run', 'x')
    assert len({batches.stable_child_id('run', 'x'), batches.stable_child_id('run', 'y'),
                batches.stable_child_id('next', 'x'), batches.batch_id('run')}) == 4
    assert len(batches.stable_child_id('run', 'x')) == 24
    with pytest.raises(ValueError):
        batches.stable_child_id('run', '../x')


def captured(request, revision='a'*40):
    request = batches.canonical_request(request)
    mode = request['mode']
    resolved = dict(request)
    if mode != 'example':
        resolved['revision'] = revision
    case = {'case_id': 'synthetic:parse-list' if mode == 'example' else 'local:'+digest(resolved)[:16],
            'repository': 'synthetic/example' if mode == 'example' else request.get('repository', 'org/repo'),
            'revision': revision, 'problem_statement': request.get('issue_text', 'Reported failure.'),
            'issue_created_at': None}
    provenance = None
    if mode == 'github':
        case.update(case_id=request['issue_url'], issue_url=request['issue_url'],
                    repository='/'.join(request['issue_url'].split('/')[3:5]),
                    issue_created_at='2026-01-01T00:00:00Z')
        provenance = {'source_url': request['issue_url'].replace('https://github.com/', 'https://api.github.com/repos/'),
                      'response_sha256': 'b'*64, 'fetched_at': '2026-10-23T00:00:00Z',
                      'body_version_at_creation_verified': False,
                      'limitation': 'Current text is not opening-version proof.'}
    source = batches.seal_source({'schema': batches.SOURCE_SCHEMA, 'mode': mode,
        'repository_path': 'D:/repos/captured', 'requested_revision': None if mode == 'example' else request['revision'],
        'revision': revision, 'case': case, 'issue_provenance': provenance}, request=resolved)
    return {'resolved_request': resolved, 'source': source, 'error': None}


def preview(*requests):
    value = batches.validate_manifest(manifest(*requests))
    return batches.build_preview(value, [dict(key=row['key'], **captured(row['request']))
                                        for row in value['items']], policy_sha256='c'*64)


def read_bundle(statuses=('pending',), desired_state='running', *, generated=False):
    raw = preview(*(local(issue_text='Issue '+str(index), provider='groq' if generated else 'none')
                    for index in range(len(statuses))))
    items = []
    for row, status in zip(raw['items'], statuses):
        request = row['resolved_request']
        items.append({'key': row['key'], 'job_id': batches.stable_child_id(raw['manifest']['run_key'], row['key']),
            'job_available': status not in ('pending', 'cancelled'),
            'request_sha256': row['request_sha256'], 'source_sha256': row['source_sha256'],
            'mode': request['mode'], 'provider': request['provider'], 'depth': request['depth'],
            'revision': row['source']['revision'], 'status': status, 'stage': status, 'error': None})
    counts = {status: sum(row['status'] == status for row in items) for status in batches.ITEM_STATUSES}
    return seal({'schema': batches.READ_SCHEMA, 'id': batches.batch_id(raw['manifest']['run_key']),
        'run_key': raw['manifest']['run_key'], 'manifest_sha256': raw['manifest_sha256'],
        'preview_sha256': raw['sha256'], 'policy_sha256': raw['policy_sha256'],
        'created_at': '2026-10-23T00:00:00Z', 'updated_at': '2026-10-23T00:00:01Z',
        'desired_state': desired_state, 'status': batches.batch_status(items, desired_state),
        'requires_api': generated, 'concurrency': 1, 'model_calls': 0,
        'counts': dict(total=len(items), **counts), 'items': items})


@pytest.mark.parametrize('input_request', [local(),
    {'mode': 'github', 'issue_url': 'https://github.com/Org/Repo/issues/12', 'revision': 'release/1.0'},
    {'mode': 'example', 'depth': 'planned'}])
def test_sources_and_preview_pin_requested_refs_to_saved_full_revisions(input_request, monkeypatch):
    from issueintel.runtime.config import RuntimeConfig
    monkeypatch.setattr(RuntimeConfig, 'load', lambda *args: pytest.fail('Pure contract read config'))
    result = preview(input_request)
    assert batches.validate_preview(result) == result
    assert result['can_start'] is True and result['model_calls'] == 0
    assert result['cost_bound_micro_usd'] == 0 and result['concurrency'] == 1
    assert result['items'][0]['source']['revision'] == 'a'*40
    assert result['items'][0]['source_sha256'] == result['items'][0]['source']['sha256']
    assert result['manifest_sha256'] == batches.manifest_sha256(result['manifest'])
    if input_request['mode'] != 'example':
        assert result['items'][0]['source']['requested_revision'] == input_request['revision']
        assert result['items'][0]['resolved_request']['revision'] == 'a'*40


def test_preview_keeps_per_item_source_failure_and_unknown_groq_cost_without_model_calls():
    value = batches.validate_manifest(manifest(local(), local(issue_text='Second', provider='groq', depth='deep')))
    items = [dict(key=value['items'][0]['key'], **captured(value['items'][0]['request'])),
             {'key': value['items'][1]['key'], 'resolved_request': None, 'source': None,
              'error': {'code': 'source_unavailable', 'message': 'Cannot read revision.'}}]
    result = batches.build_preview(value, items, policy_sha256='c'*64)
    assert batches.validate_preview(result) == result
    assert result['can_start'] is False and result['requires_api'] is True
    assert result['cost_bound_micro_usd'] is None and result['model_calls'] == 0
    assert result['items'][1]['call_upper_bound'] == 6
    assert result['items'][1]['source_sha256'] is None
    assert 'unknown; runtime budget checks still apply' in batches.render_markdown(result)


def test_preview_rejects_aliased_refs_resolving_to_duplicate_work():
    result = preview(local(revision='HEAD'), local(revision='refs/tags/v1'))
    assert result['can_start'] is False
    duplicate = result['items'][1]
    assert duplicate['error']['code'] == 'duplicate_resolved_source'
    assert duplicate['resolved_request'] is duplicate['source'] is duplicate['source_sha256'] is None
    assert batches.validate_preview(result) == result


def test_preview_dedup_uses_captured_windows_path_without_folding_posix_case():
    value = batches.validate_manifest(manifest(local(repository='D:/repos/demo'),
        local(repository='d:\\repos\\demo')))
    inputs = []
    for row, path in zip(value['items'], ('D:/repos/Demo', 'd:\\repos\\demo')):
        item = captured(row['request'])
        item['source'] = batches.seal_source(dict(item['source'], repository_path=path),
                                             request=item['resolved_request'])
        inputs.append(dict(key=row['key'], **item))
    result = batches.build_preview(value, inputs, policy_sha256='c'*64)
    assert result['items'][1]['error']['code'] == 'duplicate_resolved_source'
    assert not result['can_start']
    for item, path in zip(inputs, ('/repos/Demo', '/repos/demo')):
        item['source'] = batches.seal_source(dict(item['source'], repository_path=path),
                                             request=item['resolved_request'])
    result = batches.build_preview(value, inputs, policy_sha256='c'*64)
    assert result['can_start'] is True


@pytest.mark.parametrize('mutation', [
    lambda x: x['case'].update(problem_statement='Different body'),
    lambda x: x['case'].update(revision='b'*40),
    lambda x: x['case'].update(case_id='local:'+'f'*16),
    lambda x: x['case'].update(repository='Another repository'),
    lambda x: x.update(revision='HEAD'),
    lambda x: x.update(repository_path='relative/repository'),
    lambda x: x.update(issue_provenance={}),
    lambda x: x['case'].update(split='main_test'),
    lambda x: x.update(extra='unexpected'),
])
def test_rehashed_local_source_cannot_change_resolved_request_authority(mutation):
    item = captured(local())
    mutation(item['source'])
    with pytest.raises(ValueError):
        batches.validate_source(seal(item['source']), request=item['resolved_request'])


@pytest.mark.parametrize('mutation', [
    lambda x: x['issue_provenance'].update(body_version_at_creation_verified=True),
    lambda x: x['issue_provenance'].update(body_version_at_creation_verified=0),
    lambda x: x['issue_provenance'].update(response_sha256='unknown'),
    lambda x: x['issue_provenance'].update(source_url='https://evil.invalid/issue'),
    lambda x: x['issue_provenance'].update(fetched_at='2026-10-23'),
    lambda x: x['case'].update(issue_url='https://github.com/org/repo/issues/13'),
    lambda x: x['case'].update(issue_created_at=None),
])
def test_current_github_source_remains_unverified_and_exactly_bound(mutation):
    item = captured({'mode': 'github', 'issue_url': 'https://github.com/org/repo/issues/12'})
    mutation(item['source'])
    with pytest.raises(ValueError):
        batches.validate_source(seal(item['source']), request=item['resolved_request'])


@pytest.mark.parametrize('field,value', [('can_start', 1), ('requires_api', 0),
    ('concurrency', True), ('model_calls', False), ('model_calls', 1),
    ('cost_bound_micro_usd', False), ('cost_bound_micro_usd', 1), ('currency', 'VND')])
def test_rehashed_preview_rejects_forged_summary_scalars(field, value):
    result = preview(local())
    result[field] = value
    with pytest.raises(ValueError):
        batches.validate_preview(seal(result))


@pytest.mark.parametrize('mutation', [
    lambda x: x['items'][0].update(request_sha256='d'*64),
    lambda x: x['items'][0].update(source_sha256='d'*64),
    lambda x: x['items'][0].update(call_upper_bound=False),
    lambda x: x['items'][0].update(call_upper_bound=4),
    lambda x: x['items'][0]['resolved_request'].update(issue_text='Another issue'),
    lambda x: x['items'][0].update(key='another-key'),
    lambda x: x.update(manifest_sha256='d'*64),
    lambda x: x['items'][0]['source'].update(requested_revision='another-ref'),
])
def test_rehashed_preview_replays_bindings_and_call_bounds(mutation):
    result = preview(local())
    mutation(result)
    if (result['items'][0]['source'] and result['items'][0]['source']['sha256'] !=
            digest({key: item for key, item in result['items'][0]['source'].items() if key != 'sha256'})):
        result['items'][0]['source'] = seal(result['items'][0]['source'])
        result['items'][0]['source_sha256'] = result['items'][0]['source']['sha256']
    with pytest.raises(ValueError):
        batches.validate_preview(seal(result))


@pytest.mark.parametrize('statuses,desired,expected', [
    (('pending',), 'running', 'queued'), (('queued',), 'paused', 'paused'),
    (('running', 'pending'), 'cancelled', 'running'),
    (('pending',), 'cancelled', 'queued'),
    (('queued',), 'cancelled', 'queued'),
    (('completed',), 'paused', 'completed'),
    (('completed', 'failed'), 'cancelled', 'partial_success'),
    (('failed', 'cancelled'), 'running', 'failed'),
    (('interrupted', 'failed'), 'paused', 'interrupted'),
    (('cancelled', 'cancelled'), 'running', 'cancelled'),
])
def test_batch_read_preserves_terminal_partial_success_without_retry(statuses, desired, expected):
    result = read_bundle(statuses, desired)
    assert batches.validate_read(result) == result
    assert result['status'] == expected
    assert result['model_calls'] == 0
    assert sum(result['counts'][key] for key in batches.ITEM_STATUSES) == len(statuses)
    assert 'separate result and cost history' in batches.render_markdown(result)


@pytest.mark.parametrize('mutation', [
    lambda x: x.update(id='d'*24),
    lambda x: x.update(model_calls=False),
    lambda x: x.update(model_calls=1),
    lambda x: x.update(concurrency=True),
    lambda x: x.update(concurrency=2),
    lambda x: x.update(requires_api=0),
    lambda x: x.update(requires_api=True),
    lambda x: x.update(status='completed'),
    lambda x: x.update(desired_state='retry'),
    lambda x: x.update(updated_at='2025-01-01T00:00:00Z'),
    lambda x: x['counts'].update(total=True),
    lambda x: x['counts'].update(failed=1),
    lambda x: x['items'][0].update(job_id='e'*24),
    lambda x: x['items'][0].update(job_available=1),
    lambda x: x['items'][0].update(revision='main'),
    lambda x: x['items'][0].update(provider='typesafe'),
    lambda x: x['items'][0].update(mode='main_dev'),
    lambda x: x['items'][0].update(error={'code': 'failed'}),
    lambda x: x['items'][0].update(stage='x'*201),
    lambda x: x['items'].append(deepcopy(x['items'][0])),
])
def test_rehashed_read_rejects_forged_identity_counts_scalars_or_projection(mutation):
    value = read_bundle()
    mutation(value)
    with pytest.raises(ValueError):
        batches.validate_read(seal(value))


def test_markdown_escapes_source_errors_as_display_text():
    value = read_bundle(('failed',))
    value['items'][0]['error'] = '<script>alert(1)</script> | bad\n[open](javascript:alert(1)) `code`'
    value = seal(value)
    rendered = batches.render_markdown(value)
    assert '<script>' not in rendered and '&lt;script&gt;' in rendered
    assert '\\| bad' in rendered
    assert '\\[open\\]\\(javascript:' in rendered
    assert '\\`code\\`' in rendered
    assert 'bad\n[open]' not in rendered
