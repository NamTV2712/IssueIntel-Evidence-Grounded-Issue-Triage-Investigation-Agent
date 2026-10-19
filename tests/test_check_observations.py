"""Synthetic saved-check observations, exact bindings and append-only corrections."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import sqlite3

import pytest

from issueintel.investigation.check_observations import (
    CheckObservationStore, FLAGS, initial_journal, replay_journal,
    validate_journal, render_markdown,
)
from issueintel.investigation.check_plans import compare_plan_checks
from issueintel.investigation.hypothesis_review import _quoted
from issueintel.runtime.io import digest
from test_check_plans import check, generate, seal, source, saved, fixture_repo


PLAN_JOB = 'b' * 24


@pytest.fixture
def plan(source):
    return generate(source, [check(source), check(source, identifier='check_2')])


def observation(**changes):
    return dict({'check_id': 'check_1', 'outcome': 'A',
        'conditions': 'Synthetic parser, saved revision, input held constant',
        'observed_result': 'The guard rejected the input before strip.',
        'interpretation': 'User report only; the original hypotheses remain unverified.',
        'tested_commit_sha': None, 'supersedes': None}, **changes)


def append(store, plan, state=None, job=PLAN_JOB, **changes):
    state = store.load(plan, job) if state is None else state
    return store.append(plan, job, {'expected_sha256': state['sha256'],
                                  'observation': observation(**changes)})


def test_saved_plan_and_complete_check_are_bound_without_mutation(plan, tmp_path):
    before = deepcopy(plan)
    store = CheckObservationStore(tmp_path/'observations.sqlite')
    initial = initial_journal(plan, PLAN_JOB)
    assert initial == store.load(plan, PLAN_JOB)
    assert initial['revision'] == 0 and initial['parent_sha256'] is None
    state = append(store, plan, initial)
    event = state['events'][0]
    assert state['source'] == event['source'] == {
        'plan_job_id': PLAN_JOB, 'plan_sha256': plan['sha256'],
        **{key: plan['source'][key] for key in ('source_job_id', 'source_pack_sha256',
            'report_sha256', 'query_sha256', 'snapshot_sha256', 'commit_sha', 'annotation_sha256')},
        'source_projection_sha256': plan['source']['sha256']}
    assert event['check_binding'] == {'id': 'check_1', 'sha256': digest(plan['checks'][0])}
    assert event['observation_id'] == 'observation_1'
    assert event['observation']['tested_commit_sha'] is None
    assert state['parent_sha256'] == event['parent_sha256'] == initial['sha256']
    for key, value in FLAGS.items():
        assert state[key] == event[key] == value
        assert type(state[key]) is type(value)
    assert validate_journal(state, plan, PLAN_JOB) == state
    assert CheckObservationStore(store.path).load(plan, PLAN_JOB) == state
    assert plan == before


def test_correction_chain_retains_original_and_requires_same_active_check(plan, tmp_path):
    store = CheckObservationStore(tmp_path/'journal.sqlite')
    first = append(store, plan, tested_commit_sha='9'*40)
    other = append(store, plan, first, check_id='check_2', outcome='B')
    for change in [{'supersedes': 'observation_2'}, {'supersedes': 'observation_99'}]:
        with pytest.raises(ValueError, match='active observation'):
            append(store, plan, other, **change)
    corrected = append(store, plan, other, supersedes='observation_1', outcome='B',
                       observed_result='Correction: input reached strip.')
    assert corrected['events'][:2] == other['events']
    with pytest.raises(ValueError, match='active observation'):
        append(store, plan, corrected, supersedes='observation_1')
    again = append(store, plan, corrected, supersedes='observation_3', outcome='inconclusive')
    assert len(again['events']) == 4 and again['events'][:3] == corrected['events']
    assert replay_journal(plan, PLAN_JOB, again['events'][:2]) == other
    markdown = render_markdown(again, plan)
    assert 'observation_1 (superseded)' in markdown and 'observation_3 (superseded)' in markdown
    assert '9'*40 in markdown
    assert 'Source commit: '+plan['source']['commit_sha'] in markdown


@pytest.mark.parametrize('change', [
    {'outcome': 'verified'}, {'outcome': True}, {'outcome': 1},
    {'check_id': 'step_1'}, {'check_id': 'check_3'}, {'check_id': []},
    {'conditions': ''}, {'conditions': ' '}, {'observed_result': ''},
    {'tested_commit_sha': 'HEAD'}, {'tested_commit_sha': True}, {'tested_commit_sha': 'A'*40},
    {'supersedes': False}, {'supersedes': 'observation_0'}, {'expected_result': 'invented'},
    {'fix_verified': True}, {'conditions': []}, {'interpretation': None},
    {'conditions': 'x'*2001}, {'observed_result': 'x'*4001}, {'interpretation': 'x'*2001},
    {'observed_result': 'x\x00'}, {'observed_result': 'x\x01'},
    {'interpretation': 'hidden\u202econtrol'}, {'conditions': '\ud800'},
    {'outcome': 'not_run'},
    {'outcome': 'not_run', 'observed_result': '', 'interpretation': ' '},
    {'outcome': 'not_run', 'observed_result': '', 'tested_commit_sha': 'a'*40},
])
def test_invalid_or_invented_claims_rejected_without_write(plan, tmp_path, change):
    store = CheckObservationStore(tmp_path/'journal.sqlite')
    initial = store.load(plan, PLAN_JOB)
    with pytest.raises(ValueError):
        append(store, plan, initial, **change)
    assert store.load(plan, PLAN_JOB) == initial


def test_unrun_other_inconclusive_and_reported_test_revision_are_honest_user_data(plan, tmp_path):
    store = CheckObservationStore(tmp_path/'journal.sqlite')
    state = append(store, plan, outcome='not_run', conditions='', observed_result='',
                   interpretation='No reproducible environment available yet.')
    for outcome in ('A', 'B', 'other', 'inconclusive'):
        state = append(store, plan, state, outcome=outcome, tested_commit_sha='9'*64,
                       conditions='Environment:\n\tmanual synthetic run', interpretation='')
    assert [event['observation']['outcome'] for event in state['events']] == [
        'not_run', 'A', 'B', 'other', 'inconclusive']
    assert all(not event['causal_verified'] and not event['fix_verified']
               and not event['executed_by_issueintel'] for event in state['events'])
    assert state['source']['commit_sha'] != state['events'][-1]['observation']['tested_commit_sha']


def test_concurrent_writers_and_exact_retry_append_once(plan, tmp_path):
    stores = [CheckObservationStore(tmp_path/'journal.sqlite') for _ in range(2)]
    initial = stores[0].load(plan, PLAN_JOB)
    request = {'expected_sha256': initial['sha256'], 'observation': observation()}
    def save(index):
        try:
            return stores[index].append(plan, PLAN_JOB, request)
        except ValueError as error:
            assert 'changed' in str(error)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(save, range(2)))
    assert sum(value is not None for value in results) == 1
    with pytest.raises(ValueError, match='changed'):
        stores[0].append(plan, PLAN_JOB, request)
    assert stores[0].load(plan, PLAN_JOB)['revision'] == 1


def test_plan_job_namespace_and_changed_plan_cannot_reuse_observations(plan, tmp_path):
    store = CheckObservationStore(tmp_path/'journal.sqlite')
    first = append(store, plan)
    other_job = 'c'*24
    other = store.load(plan, other_job)
    assert other['revision'] == 0 and other['sha256'] != initial_journal(plan, PLAN_JOB)['sha256']
    with pytest.raises(ValueError):
        validate_journal(first, plan, other_job)
    changed = deepcopy(plan)
    changed['checks'][0]['predictions'][0]['reason'] += ' Changed claim.'
    changed['comparison'] = compare_plan_checks(changed['checks'], changed['source'])
    seal(changed)
    with pytest.raises(ValueError, match='source or history'):
        store.load(changed, PLAN_JOB)
    assert store.load(plan, PLAN_JOB) == first
    for invalid in ['a'*23, 'A'*24, None, True, '../plan']:
        with pytest.raises(ValueError):
            store.load(plan, invalid)


@pytest.mark.parametrize('change', [
    lambda e: e['source'].update(plan_sha256='0'*64),
    lambda e: e['source'].update(report_sha256='0'*64),
    lambda e: e['source'].update(annotation_sha256='0'*64),
    lambda e: e['check_binding'].update(sha256='0'*64),
    lambda e: e['check_binding'].update(id='check_2'),
    lambda e: e.update(parent_sha256='0'*64),
    lambda e: e.update(observation_id='observation_2'),
    lambda e: e.update(revision=True),
    lambda e: e.update(fix_verified=0),
    lambda e: e.update(executed_by_issueintel=True),
    lambda e: e.update(independent_human=True),
    lambda e: e.update(recorded_at='2026-10-18T12:00:00'),
    lambda e: e.update(recorded_at='2026-10-18T12:00:00+01:00'),
    lambda e: e.update(recorded_at='not-a-time'),
    lambda e: e.update(extra='unsupported'),
])
def test_resealed_event_cannot_escape_source_check_types_or_history(plan, tmp_path, change):
    store = CheckObservationStore(tmp_path/'journal.sqlite')
    state = append(store, plan)
    event = deepcopy(state['events'][0]); change(event); seal(event)
    with sqlite3.connect(store.path) as connection:
        connection.execute('UPDATE supplemental_check_observations SET sha256=?,payload=?',
                           (event['sha256'], json.dumps(event)))
    with pytest.raises(ValueError):
        store.load(plan, PLAN_JOB)


def test_tampering_shape_duplicate_json_and_missing_chain_record_fail(plan, tmp_path):
    for mode in ('unsealed', 'duplicate', 'gap', 'bad_json', 'oversized'):
        store = CheckObservationStore(tmp_path/(mode+'.sqlite'))
        state = append(store, plan)
        state = append(store, plan, state)
        event = deepcopy(state['events'][0])
        if mode == 'unsealed':
            event['observation']['observed_result'] = 'Altered report.'
            payload = json.dumps(event)
        elif mode == 'duplicate':
            payload = json.dumps(event)[:-1] + ', "revision": 1}'
        elif mode == 'bad_json':
            payload = '{'
        elif mode == 'oversized':
            payload = ' '*64001
        else:
            payload = json.dumps(event)
        with sqlite3.connect(store.path) as connection:
            if mode == 'gap':
                connection.execute('DELETE FROM supplemental_check_observations WHERE revision=1')
            else:
                connection.execute('UPDATE supplemental_check_observations SET payload=? WHERE revision=1', (payload,))
        with pytest.raises(ValueError):
            store.load(plan, PLAN_JOB)


def test_journal_top_level_reseal_cannot_change_types_source_checks_or_flags(plan, tmp_path):
    store = CheckObservationStore(tmp_path/'journal.sqlite')
    initial = initial_journal(plan, PLAN_JOB)
    state = append(store, plan)
    changes = [lambda j:j.update(revision=False), lambda j:j.update(causal_verified=0),
        lambda j:j.update(evaluation_performed=True), lambda j:j.update(extra='unrecognized'),
        lambda j:j['source'].update(query_sha256='0'*64),
        lambda j:j['checks'][0]['predictions'][0].update(outcome='B'),
        lambda j:j['events'][0]['observation'].update(conditions='rewritten')]
    for original in (initial, state):
        for mutate in changes:
            if original['revision'] == 0 and mutate is changes[-1]:
                continue
            altered = deepcopy(original); mutate(altered); seal(altered)
            with pytest.raises(ValueError):
                validate_journal(altered, plan, PLAN_JOB)


def test_limit_rollback_empty_plan_and_invalid_requests(plan, source, tmp_path, monkeypatch):
    store = CheckObservationStore(tmp_path/'journal.sqlite')
    monkeypatch.setattr('issueintel.investigation.check_observations.LIMIT', 2)
    state = append(store, plan)
    state = append(store, plan, state)
    with pytest.raises(ValueError, match='limit'):
        append(store, plan, state)
    assert store.load(plan, PLAN_JOB) == state
    empty = generate(source, [])
    assert initial_journal(empty, 'e'*24)['checks'] == []
    with pytest.raises(ValueError, match='exact check'):
        append(store, empty, job='e'*24)
    for invalid in [None, {}, {'expected_sha256': state['sha256'], 'observation': observation(), 'source': {}},
                    {'expected_sha256': True, 'observation': observation()}]:
        with pytest.raises(ValueError):
            store.append(plan, PLAN_JOB, invalid)


def test_markdown_contains_full_plan_predictions_and_literal_chronological_history(plan, tmp_path):
    store = CheckObservationStore(tmp_path/'journal.sqlite')
    initial = store.load(plan, PLAN_JOB)
    assert 'No manual observations recorded' in render_markdown(initial, plan)
    first = append(store, plan, observed_result='<script>x</script>\n@everyone https://invalid.test')
    second = append(store, plan, first, check_id='check_2', outcome='other')
    markdown = render_markdown(second, plan)
    for saved_check in plan['checks']:
        assert _quoted(saved_check['procedure']) in markdown
        assert _quoted(saved_check['assumptions']) in markdown
        assert _quoted(saved_check['outcome_a']) in markdown and _quoted(saved_check['outcome_b']) in markdown
        for prediction in saved_check['predictions']:
            assert _quoted(prediction['reason']) in markdown
    assert markdown.index('## observation_1') < markdown.index('## observation_2')
    assert '<script>' not in markdown and '&lt;script&gt;' in markdown
    assert 'A/B are user-selected labels' in markdown
    assert second['sha256'] in markdown and plan['sha256'] in markdown
    assert digest(plan['checks'][0]) in markdown
    assert render_markdown(second, plan) == markdown
