"""Issue relationships stay side-bound, explicit and unverified."""
from copy import deepcopy
import hashlib
import json

import pytest

from issueintel.investigation import issue_relations as relations
from issueintel.investigation.providers import ProviderError
from issueintel.runtime.io import digest


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def pack(label, *, repository='Owner/Project', query=None):
    query = query or ('😀 '+label+' parser fails on missing input. Affected version 1.0. '
        'Reported mechanism: parser calls strip on None without checking it.')
    commit = 'a'*40; snapshot = 'b'*64
    return {
        'case': {'case_id': 'local:'+label, 'repository': repository, 'revision': commit,
                 'problem_statement': query, 'issue_created_at': None},
        'snapshot': {'commit_sha': commit, 'snapshot_sha256': snapshot, 'protocol': 'benchmark_base'},
        'evidence': [
            {'id': 'issue', 'kind': 'issue', 'text': query, 'sha256': sha(query),
             'locator': 'local:'+label, 'revision': None, 'provenance': {'kind': 'user_input'}},
            {'id': 'code', 'kind': 'code', 'text': 'def parse_'+label+'(value):\n    return value.strip()\n',
             'sha256': sha('def parse_'+label+'(value):\n    return value.strip()\n'),
             'locator': 'parser.py:1-2', 'revision': commit, 'provenance': {'snapshot_sha256': snapshot}},
        ],
        'annotation': None,
        'leakage_audit': {'strict_issue_time_verified': False},
    }


@pytest.fixture
def source():
    return relations.build_source(
        relations.project_pack(pack('first'), '1'*24, 'c'*64),
        relations.project_pack(pack('second'), '2'*24, 'd'*64))


def ref(source, side, kind='code', quote=None):
    row = next(s for s in source[side]['sources'] if s['kind'] == kind)
    quote = quote or row['text'].splitlines()[0]
    start = row['text'].index(quote)
    return {'side': side, 'source_id': row['id'], 'start': start, 'end': start+len(quote), 'quote': quote}


def response(source):
    symptoms = [ref(source, side, 'issue', 'parser fails') for side in ('left', 'right')]
    code = [ref(source, side) for side in ('left', 'right')]
    versions = [ref(source, side, 'issue', 'version 1.0') for side in ('left', 'right')]
    values = []
    for name, judgment, support in (
        ('symptoms', 'similar', symptoms), ('mechanism', 'similar', code),
        ('version', 'compatible', versions), ('differences', 'non_material', code),
    ):
        values.append({'name': name, 'judgment': judgment,
            'explanation': 'The cited saved sources describe this dimension; the judgment remains unverified.',
            'support': deepcopy(support), 'counter': [], 'contextual_fit': 'demonstrated',
            'inferential_link': 'demonstrated', 'counter_resolution': 'not_supplied'})
    return {'classification': 'possible_duplicate', 'reason': 'A shared proposed mechanism deserves review; no duplicate is confirmed.',
        'dimensions': values, 'mechanism_basis': {'left': 'code', 'right': 'code'},
        'versions': {side: {'status': 'reported', 'value': '1.0', 'citations': [deepcopy(versions[i])]}
                     for i, side in enumerate(('left', 'right'))}, 'assumptions': [], 'unknowns': []}


def generate(source, value=None):
    return relations.generate(source, lambda *a, **k: deepcopy(value or response(source)), model=relations.MODEL)


def reseal(value):
    value['sha256'] = digest({k: v for k, v in value.items() if k != 'sha256'})
    return value


def synced_item(number=2, **changes):
    record = {'kind': 'issue', 'repository': 'owner/project', 'key': 'issue:'+str(number),
              'number': number, 'title': 'Other parser fails',
              'body': 'Affected version 1.0. Reported mechanism: parser calls strip on None without checking it.',
              'url': 'https://github.com/owner/project/issues/'+str(number)}
    record.update(changes)
    receipt = {'repository_id': 42, 'records': {record['key']: digest(record)}}
    return {'record': record, 'record_sha256': digest(record), 'receipt': receipt,
            'receipt_sha256': digest(receipt), 'availability': {'status': 'last_observed_available'}}


def test_offline_packs_need_no_generated_hypotheses_and_preserve_two_scopes(source):
    assert source['left']['kind'] == 'investigation'
    assert source['left']['reference']['pack_sha256'] == 'c'*64
    assert source['left']['repository'] == 'owner/project'
    assert [s['id'] for s in source['left']['sources']] == [s['id'] for s in source['right']['sources']]
    bundle = generate(source)
    assert bundle['assessment']['classification'] == 'possible_duplicate'
    assert bundle['assessment']['model_classification'] == 'possible_duplicate'
    assert bundle['assessment']['semantic_relation'] == 'unverified'
    assert all(bundle[k] is False for k in ('duplicate_verified', 'causal_verified', 'independent_human', 'evaluation_performed'))
    left, right = bundle['assessment']['dimensions'][1]['support']
    assert left['side'] == 'left' and right['side'] == 'right'
    assert left['quote_sha256'] != right['quote_sha256']
    assert left['source_sha256'] == source['left']['sources'][1]['sha256']
    assert all('no_counter_in_supplied_scope' in g['code'] for g in bundle['assessment']['gaps'])


def test_same_error_quotes_alone_never_supply_a_shared_reported_mechanism(source):
    value = response(source)
    value['dimensions'][1]['support'] = deepcopy(value['dimensions'][0]['support'])
    value['mechanism_basis'] = {'left': 'reported_mechanism', 'right': 'reported_mechanism'}
    result = generate(source, value)['assessment']
    assert result['classification'] == 'related'
    assert {'left_mechanism_basis_missing', 'right_mechanism_basis_missing'} <= {g['code'] for g in result['gaps']}


def test_reported_mechanisms_can_be_compared_with_synced_issue_without_code():
    left = relations.project_pack(pack('first'), '1'*24, 'c'*64)
    right = relations.project_synced(synced_item(), 'Owner/Project')
    source = relations.build_source(left, right)
    # The candidate side intentionally has issue-only evidence.
    value = {'classification': 'possible_duplicate', 'reason': 'Both issue reports separately describe a proposed mechanism.',
        'dimensions': [], 'mechanism_basis': {'left': 'reported_mechanism', 'right': 'reported_mechanism'},
        'versions': {}, 'assumptions': [], 'unknowns': []}
    for name, judgment in (('symptoms', 'similar'), ('mechanism', 'similar'), ('version', 'compatible'), ('differences', 'non_material')):
        quote = 'parser fails' if name == 'symptoms' else 'parser calls strip on None without checking it.' if name in {'mechanism', 'differences'} else 'version 1.0'
        support = [ref(source, side, 'issue', quote) for side in ('left', 'right')]
        value['dimensions'].append({'name': name, 'judgment': judgment, 'explanation': 'Exact reported context is present on both sides, but is not execution proof.',
            'support': support, 'counter': [], 'contextual_fit': 'demonstrated', 'inferential_link': 'demonstrated', 'counter_resolution': 'not_supplied'})
    value['versions'] = {side: {'status': 'reported', 'value': '1.0', 'citations': [ref(source, side, 'issue', 'version 1.0')]} for side in ('left', 'right')}
    result = generate(source, value)
    assert result['assessment']['classification'] == 'possible_duplicate'
    assert result['source']['right']['commit_sha'] is None
    assert result['assessment']['duplicate_verified'] is False


@pytest.mark.parametrize('change,gap', [
    ('mechanism_unknown', 'shared_mechanism_not_established'),
    ('mechanism_context', 'mechanism_context_unresolved'),
    ('mechanism_link', 'mechanism_inferential_link_unresolved'),
    ('one_side', 'mechanism_lacks_both_sides'),
    ('material_difference', 'material_or_unknown_differences'),
    ('unknown_difference', 'material_or_unknown_differences'),
    ('assumption', 'unverified_assumptions'),
    ('unresolved_counter', 'mechanism_counter_unresolved'),
])
def test_sufficiency_downgrades_critical_unknowns_but_keeps_raw_judgment(source, change, gap):
    value = response(source); mechanism = value['dimensions'][1]
    if change == 'mechanism_unknown':mechanism['judgment'] = 'unknown'
    elif change == 'mechanism_context':mechanism['contextual_fit'] = 'unknown'
    elif change == 'mechanism_link':mechanism['inferential_link'] = 'missing'
    elif change == 'one_side':mechanism['support'] = mechanism['support'][:1]
    elif change == 'material_difference':value['dimensions'][3]['judgment'] = 'material'
    elif change == 'unknown_difference':value['dimensions'][3]['judgment'] = 'unknown'
    elif change == 'assumption':value['assumptions'] = [{'condition': 'Both reports use the same caller.', 'status': 'unverified', 'citations': []}]
    else:
        mechanism['counter'] = [ref(source, 'right', quote='return value.strip()')]
        mechanism['counter_resolution'] = 'unresolved'
    result = generate(source, value)
    assert result['comparison']['classification'] == 'possible_duplicate'
    assert result['assessment']['classification'] == 'related'
    assert gap in {g['code'] for g in result['assessment']['gaps']}


def test_unknown_affected_versions_remain_visible_without_erasing_strong_candidate(source):
    value = response(source)
    value['versions'] = {side: {'status': 'unknown', 'value': None, 'citations': []} for side in ('left', 'right')}
    value['dimensions'][2].update(judgment='unknown', support=[], contextual_fit='unknown', inferential_link='unknown')
    value['unknowns'] = ['Affected releases have not been reported; request them before deciding a duplicate.']
    result = generate(source, value)['assessment']
    assert result['classification'] == 'possible_duplicate'
    assert result['versions']['left']['value'] is None
    assert {'left_affected_version_unknown', 'right_affected_version_unknown', 'explicit_unknowns_remain'} <= {g['code'] for g in result['gaps']}
    assert result['unknowns'] == value['unknowns']


def test_model_abstention_is_not_promoted_by_structural_support(source):
    value = response(source); value['classification'] = 'insufficient'
    assert generate(source, value)['assessment']['classification'] == 'insufficient'


def test_same_version_or_explained_differences_alone_do_not_manufacture_related(source):
    value = response(source); value['classification'] = 'related'
    value['dimensions'][0]['judgment'] = 'different'
    value['dimensions'][1]['judgment'] = 'different'
    assert generate(source, value)['assessment']['classification'] == 'insufficient'


def test_repeated_same_error_at_another_offset_is_not_reported_mechanism(source):
    value = response(source); changed = deepcopy(source)
    for side in ('left', 'right'):
        report = changed[side]['sources'][0]
        report['text'] += ' parser fails'
        report['sha256'] = sha(report['text']); reseal(changed[side])
    reseal(changed)
    value = response(changed)
    for side, citation in zip(('left', 'right'), value['dimensions'][1]['support']):
        report = changed[side]['sources'][0]; start = report['text'].rindex('parser fails')
        citation.update(source_id=report['id'], start=start, end=start+len('parser fails'), quote='parser fails')
    value['mechanism_basis'] = {'left': 'reported_mechanism', 'right': 'reported_mechanism'}
    assert generate(changed, value)['assessment']['classification'] == 'related'


@pytest.mark.parametrize('mutation', ['side', 'quote', 'offset', 'bool_offset', 'source', 'counter_role', 'unknown_version_value', 'commit_as_version', 'version_wrong_side', 'assumption', 'extra', 'dimension'])
def test_invalid_wire_or_side_citations_never_get_silently_repaired(source, mutation):
    value = response(source); citation = value['dimensions'][1]['support'][0]
    if mutation == 'side':citation['side'] = 'right'
    elif mutation == 'quote':citation['quote'] = 'invented code'
    elif mutation == 'offset':citation['start'] += 1
    elif mutation == 'bool_offset':citation['start'] = True
    elif mutation == 'source':citation['source_id'] = 'not-present'
    elif mutation == 'counter_role':
        value['dimensions'][1]['counter'] = [deepcopy(citation)]
        value['dimensions'][1]['counter_resolution'] = 'addressed'
    elif mutation == 'unknown_version_value':value['versions']['left']['status'] = 'unknown'
    elif mutation == 'commit_as_version':value['versions']['left']['value'] = 'a'*40
    elif mutation == 'version_wrong_side':value['versions']['left']['citations'][0]['side'] = 'right'
    elif mutation == 'assumption':value['assumptions'] = [{'condition': 'Necessary caller equivalence.', 'status': 'source_supported', 'citations': []}]
    elif mutation == 'extra':value['execute_command'] = 'do not execute'
    else:value['dimensions'][0]['name'] = 'mechanism'
    with pytest.raises(ValueError):relations.validate_comparison(value, source)


def test_unicode_offsets_are_codepoints_not_utf16_or_utf8(source):
    value = response(source)
    row = source['left']['sources'][0]
    value['dimensions'][0]['support'][0] = ref(source, 'left', 'issue', '😀 first parser')
    relations.validate_comparison(value, source)
    value['dimensions'][0]['support'][0]['end'] += 1
    with pytest.raises(ValueError, match='Unicode'):relations.validate_comparison(value, source)
    assert row['text'].startswith('😀')


@pytest.mark.parametrize('mutation', ['classification', 'gap', 'bound_side', 'rubric', 'truth', 'provenance'])
def test_resealed_results_cannot_change_the_recomputed_contract(source, mutation):
    bundle = generate(source)
    if mutation == 'classification':bundle['assessment']['classification'] = 'insufficient'
    elif mutation == 'gap':bundle['assessment']['gaps'] = []
    elif mutation == 'bound_side':bundle['assessment']['dimensions'][1]['support'][0]['side'] = 'right'
    elif mutation == 'rubric':bundle['rubric']['version'] = 'other'
    elif mutation == 'truth':bundle['duplicate_verified'] = 0
    else:bundle['provenance']['attempts'] = True
    reseal(bundle)
    with pytest.raises(ValueError):relations.validate(bundle, source)


@pytest.mark.parametrize('change', ['same_key', 'same_query', 'same_job', 'other_repo'])
def test_pair_identity_and_repository_guards(source, change):
    left = deepcopy(source['left']); right = deepcopy(source['right'])
    if change == 'same_key':right['issue_key'] = left['issue_key']
    elif change == 'same_query':
        right['sources'][0].update(text=left['sources'][0]['text'], sha256=left['sources'][0]['sha256'])
    elif change == 'same_job':right['reference']['job_id'] = left['reference']['job_id']
    else:right['repository'] = 'other/project'
    reseal(right)
    with pytest.raises(ValueError):relations.build_source(left, right)


def test_distinct_github_issue_identities_can_have_identical_reports():
    left = pack('first'); right = pack('second', query=left['case']['problem_statement'])
    for number, value in enumerate((left, right), 1):
        value['case']['issue_url'] = 'https://github.com/Owner/Project/issues/'+str(number)
        value['case']['case_id'] = value['case']['issue_url']
    source = relations.build_source(relations.project_pack(left, '1'*24, 'c'*64),
                                    relations.project_pack(right, '2'*24, 'd'*64))
    assert source['left']['sources'][0]['sha256'] == source['right']['sources'][0]['sha256']
    assert source['left']['issue_key'] != source['right']['issue_key']
    assert generate(source)['assessment']['classification'] == 'possible_duplicate'


def test_same_github_issue_across_runs_remains_self_comparison():
    left = pack('first'); right = pack('second')
    for value in (left, right):value['case']['issue_url'] = 'https://github.com/owner/project/issues/1'
    with pytest.raises(ValueError):
        relations.build_source(relations.project_pack(left, '1'*24, 'c'*64),
                               relations.project_pack(right, '2'*24, 'd'*64))


def test_explicit_issue_identity_cannot_disguise_another_repository():
    value = pack('first'); value['case']['issue_url'] = 'https://github.com/other/project/issues/1'
    with pytest.raises(ValueError):relations.project_pack(value, '1'*24, 'c'*64)


@pytest.mark.parametrize('change', ['strict', 'split', 'instance', 'gold', 'wrong_code_revision', 'corrupt_text'])
def test_pack_projection_never_accepts_benchmark_or_changed_code(change):
    value = pack('first')
    if change == 'strict':value['snapshot']['protocol'] = 'issue_time'
    elif change == 'split':value['case']['split'] = 'main_dev'
    elif change == 'instance':value['case']['instance_id'] = 'restricted'
    elif change == 'gold':value['case']['gold'] = {'patch': 'restricted'}
    elif change == 'wrong_code_revision':value['evidence'][1]['revision'] = 'f'*40
    else:value['evidence'][1]['text'] += 'changed'
    with pytest.raises(ValueError):relations.project_pack(value, '1'*24, 'c'*64)


@pytest.mark.parametrize('change', ['pr', 'record_hash', 'receipt_hash', 'missing_record_binding', 'other_repository', 'unavailable'])
def test_synced_projection_keeps_record_and_receipt_scope(change):
    item = synced_item()
    if change == 'pr':
        item['record'].update(kind='pull', key='pull:2', url='https://github.com/owner/project/pull/2')
        item['record_sha256'] = digest(item['record']); item['receipt']['records'] = {'pull:2': item['record_sha256']}; item['receipt_sha256'] = digest(item['receipt'])
    elif change == 'record_hash':item['record']['body'] += 'changed'
    elif change == 'receipt_hash':item['receipt']['repository_id'] = 43
    elif change == 'missing_record_binding':item['receipt']['records'] = {}; item['receipt_sha256'] = digest(item['receipt'])
    elif change == 'other_repository':item['record']['repository'] = 'other/project'; item['record_sha256'] = digest(item['record'])
    else:item['availability'] = {'reason': 'deleted_or_inaccessible'}
    with pytest.raises(ValueError):relations.project_synced(item, 'owner/project')


def test_followup_source_case_identity_and_repository_path_semantics():
    value = pack('first'); value['case']['case_id'] = 'followup:'+'9'*24
    value['followup'] = {'source_case_id': 'local:root'}
    assert relations.project_pack(value, '1'*24, 'c'*64)['issue_key'] == 'local:root'
    assert relations.canonical_repository('/Work/Repo') == '/Work/Repo'
    assert relations.canonical_repository(r'D:\Work\Repo') == relations.canonical_repository(r'd:\work\repo')
    assert relations.canonical_repository('https://github.com/Owner/Project.git') == 'owner/project'


@pytest.mark.parametrize('value',[None, {}, {'case': None}, {'case': {'case_id': True}}])
def test_malformed_pack_projection_rejects_with_value_error(value):
    with pytest.raises(ValueError):relations.project_pack(value, '1'*24, 'c'*64)


@pytest.mark.parametrize('value',[None, {}, {'record': None}, {'record': {'kind': True}}])
def test_malformed_synced_projection_rejects_with_value_error(value):
    with pytest.raises(ValueError):relations.project_synced(value, 'owner/project')


def test_generation_allows_one_output_repair_only_and_no_transport_retry(source):
    calls = []
    def repair(messages, **kwargs):
        calls.append(messages)
        if len(calls) == 1:return {'invalid': 'schema'}
        return response(source)
    bundle = relations.generate(source, repair, model=relations.MODEL)
    assert len(calls) == 2 and bundle['provenance']['attempts'] == 2 and bundle['provenance']['repaired'] is True
    assert 'only schema repair' in calls[1][-1]['content']
    calls.clear()
    def transport(*a, **k):
        calls.append(1); raise ProviderError('synthetic failure', status=503)
    with pytest.raises(ProviderError):relations.generate(source, transport, model=relations.MODEL)
    assert len(calls) == 1


def test_malformed_output_consumes_at_most_two_attempts(source):
    calls = []
    with pytest.raises(ValueError):
        relations.generate(source, lambda *a, **k: calls.append(1) or {'invalid': 'schema'}, model=relations.MODEL)
    assert len(calls) == 2


def test_rendering_is_source_bound_and_literal_not_an_instruction_or_claim_of_truth(source):
    value = response(source); value['reason'] = '<script>do not execute</script> @someone https://evil.invalid'
    bundle = generate(source, value); saved = json.dumps(bundle, ensure_ascii=False)
    text = relations.render_markdown(bundle)
    assert 'Confirmed duplicate: **no**' in text and 'Semantic relationship: **unverified**' in text
    assert 'Source SHA256:' in text and 'left / code' in text
    assert '<script>' not in text and '@someone' not in text
    assert json.dumps(bundle, ensure_ascii=False) == saved


def test_model_payload_uses_public_projection_not_machine_paths_or_local_owners(source):
    source = deepcopy(source)
    private_repository = relations.canonical_repository(r'C:\Users\PrivateAuthor\ConfidentialRepo')
    for side in ('left', 'right'):
        source[side]['repository'] = private_repository
        reseal(source[side])
    reseal(source)
    messages = relations._messages(source); wire = json.dumps(messages, ensure_ascii=False)
    payload = json.loads(messages[1]['content'])
    assert 'privateauthor' not in wire.lower() and 'confidentialrepo' not in wire.lower()
    assert source['left']['reference']['job_id'] not in wire
    assert source['right']['reference']['job_id'] not in wire
    assert source['left']['reference']['pack_sha256'] not in wire
    assert payload['model_input_projection'] == 'issueintel.public_model_inputs.v1'
    assert payload['left']['repository'] == 'local_repository'
    assert payload['left']['issue_url'] == 'input://issue'
    assert payload['left']['sources'][0]['locator'] == 'input://issue'
    assert payload['left']['sources'][1]['locator'] == 'parser.py:1-2'
    assert payload['left']['commit_sha'] == source['left']['commit_sha']
    assert source['left']['repository'] == private_repository
