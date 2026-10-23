"""Exact excerpt changes and honest bounded search on temporary Git snapshots."""
from copy import deepcopy
import hashlib
from pathlib import Path

import pytest

from issueintel.investigation.evidence_impact import build_impact,validate_impact,render_markdown,DEFAULT_LIMITS
from issueintel.repository.snapshot import build_snapshot
from issueintel.runtime.io import digest
from test_versions import versions,commit


def projection(fixture, target=None, *, limits=None):
    wb, identifier, pack = fixture
    before=build_snapshot(pack['snapshot']['repository'],pack['snapshot']['commit_sha'])
    after=build_snapshot(pack['snapshot']['repository'],target or before.commit_sha)
    raw=wb.result_bytes(identifier)
    value=build_impact(pack,before,after,source_job_id=identifier,
                       source_pack_sha256=hashlib.sha256(raw).hexdigest(),limits=limits)
    return value,next(row for row in value['entries'] if row['before']['path']=='parser.py')


def sealed(value):
    result=deepcopy(value);result.pop('sha256',None)
    return dict(result,sha256=digest(result))


def test_same_commit_retains_exact_original_source_and_needs_no_model(versions):
    wb,identifier,pack=versions
    original=wb.result_bytes(identifier)
    value,row=projection(versions)
    assert row['status']=='unchanged' and row['after']['original_slot']['text']==row['before']['text']
    assert not row['after']['search']['performed']
    assert value['fix_status']=='unverified' and value['evaluation_performed'] is False and value['model_calls']==0
    assert value['target_text_inventory_included'] is False
    assert validate_impact(value,pack=pack,source_job_id=identifier,source_pack_sha256=hashlib.sha256(original).hexdigest())==value
    assert wb.result_bytes(identifier)==original
    assert value['before']==value['after']


@pytest.mark.parametrize('change,expected',[
    ('insert','moved'),('edit','changed'),('remove','missing'),('binary','unavailable'),
    ('duplicate','ambiguous'),('rename','moved'),('append','unchanged'),
])
def test_source_excerpt_status_follows_target_text_and_path(versions,change,expected):
    _,_,pack=versions;repo=Path(pack['snapshot']['repository']);parser=repo/'parser.py'
    original=parser.read_text(encoding='utf-8')
    if change=='insert':parser.write_text('# Unicode α and extra lines\n\n'+original,encoding='utf-8')
    elif change=='edit':parser.write_text('def parse_list(items):\n    raise ValueError("changed target")\n',encoding='utf-8')
    elif change=='remove':parser.unlink()
    elif change=='binary':parser.write_bytes(b'\x00binary')
    elif change=='duplicate':
        parser.unlink()
        (repo/'copy_one.py').write_text(original,encoding='utf-8')
        (repo/'copy_two.py').write_text(original,encoding='utf-8')
    elif change=='rename':parser.rename(repo/'moved_parser.py')
    elif change=='append':parser.write_text(original+'\nEXTRA = 1\n',encoding='utf-8')
    target=commit(repo)
    value,row=projection(versions,target)
    assert row['status']==expected,row
    assert value['before']['commit_sha']!=value['after']['commit_sha']
    assert value['fix_status']=='unverified'
    if change in {'insert','rename'}:
        assert len(row['after']['matches'])==1
        assert row['after']['matches'][0]['text']==row['before']['text']
        assert row['after']['search']['complete']
    if change=='duplicate':assert len(row['after']['matches'])==2
    if change=='binary':assert row['after']['skipped_reason']=='binary'
    assert validate_impact(value,pack=pack)==value


def test_scan_cap_keeps_absence_unknown_and_never_calls_missing(versions):
    _,_,pack=versions;repo=Path(pack['snapshot']['repository']);parser=repo/'parser.py'
    parser.write_text('# shifted\n'+parser.read_text(encoding='utf-8'),encoding='utf-8')
    target=commit(repo)
    value,row=projection(versions,target,limits=dict(DEFAULT_LIMITS,max_scan_files=1))
    assert row['status']=='unavailable' and not row['after']['search']['complete']
    assert row['after']['search']['match_count'] is None
    assert row['after']['search']['limit_reason']=='file_scan_limit'
    assert value['coverage']['searches_limited']>=1


def test_witness_cap_retains_unknown_instead_of_inventing_target_quotes(versions):
    value,row=projection(versions,limits=dict(DEFAULT_LIMITS,max_witness_utf8=1))
    assert row['status']=='unavailable' and row['after']['matches']==[]
    assert row['after']['search']['limit_reason']=='match_witness_limit'
    assert validate_impact(value)==value


@pytest.mark.parametrize('limit,reason',[
    ('max_scan_utf8','byte_scan_limit'),('max_scan_lines','line_scan_limit'),
    ('max_match_checks','match_check_limit'),('max_excerpt_utf8','excerpt_size_limit'),
])
def test_search_work_caps_remain_unknown(versions,limit,reason):
    _,_,pack=versions;repo=Path(pack['snapshot']['repository']);parser=repo/'parser.py'
    parser.write_text('# shift\n'+parser.read_text(encoding='utf-8'),encoding='utf-8')
    value,row=projection(versions,commit(repo),limits=dict(DEFAULT_LIMITS,**{limit:1}))
    assert row['status']=='unavailable' and row['after']['search']['limit_reason']==reason
    assert row['after']['search']['match_count'] is None
    assert validate_impact(value,pack=pack)==value


@pytest.mark.parametrize('count,cap,expected',[(2,1,'unavailable'),(9,8,'ambiguous')])
def test_match_storage_cap_never_selects_the_first_location(versions,count,cap,expected):
    _,_,pack=versions;repo=Path(pack['snapshot']['repository']);parser=repo/'parser.py'
    original=parser.read_text(encoding='utf-8');parser.unlink()
    for index in range(count):(repo/('copy_'+str(index)+'.py')).write_text(original,encoding='utf-8')
    value,row=projection(versions,commit(repo),limits=dict(DEFAULT_LIMITS,max_matches=cap))
    assert row['status']==expected and len(row['after']['matches'])==cap
    assert row['after']['search']['match_count']==count and row['after']['search']['complete']
    assert row['after']['search']['matches_truncated'] and row['after']['search']['limit_reason']=='match_witness_limit'
    assert value['coverage']['searches_limited']>=1


def test_unicode_formfeed_and_crlf_keep_lf_line_coordinates(versions):
    _,_,pack=versions;repo=Path(pack['snapshot']['repository']);parser=repo/'parser.py'
    text='# α\fβ\n'+parser.read_text(encoding='utf-8')
    parser.write_bytes(text.replace('\n','\r\n').encode('utf-8'))
    _,row=projection(versions,commit(repo))
    assert row['status']=='moved' and row['after']['matches'][0]['start_line']==2
    assert row['after']['matches'][0]['text']==row['before']['text']


@pytest.mark.parametrize('mutation',[
    lambda x:x.update(model_calls=False),
    lambda x:x.update(evaluation_performed=0),
    lambda x:x.update(fix_status='fixed'),
    lambda x:x.update(target_text_inventory_included=True),
    lambda x:x['counts'].update(unchanged=float(x['counts']['unchanged'])),
    lambda x:x['entries'][0].update(status='moved'),
    lambda x:x['entries'][0]['after']['search'].update(performed=0),
    lambda x:x['entries'][0]['after']['search'].update(match_count=0),
    lambda x:x['entries'][0]['after']['search'].update(limit_reason=[]),
    lambda x:x['entries'][0]['before'].update(start_line=True),
    lambda x:x['entries'][0]['before'].update(locator='../outside.py:1-2'),
    lambda x:x['entries'][0]['before'].update(revision='f'*40),
    lambda x:x['coverage'].update(reported=False),
    lambda x:x['limits'].update(max_matches=999999),
    lambda x:x['source'].update(job_id='b'*24),
    lambda x:x['source'].update(pack_sha256='c'*64),
    lambda x:x['entries'][0]['hypotheses']['support_ids'].append('invented-hypothesis'),
    lambda x:x.update(entries=x['entries']*2),
])
def test_rehashed_inconsistent_artifact_is_rejected(versions,mutation):
    wb,identifier,pack=versions;value,_=projection(versions)
    mutation(value);value=sealed(value)
    with pytest.raises(ValueError):
        validate_impact(value,pack=pack,source_job_id=identifier,
                        source_pack_sha256=hashlib.sha256(wb.result_bytes(identifier)).hexdigest())


def test_source_text_forgery_is_rejected_even_with_rehashed_target_witness(versions):
    _,_,pack=versions;value,_=projection(versions);row=value['entries'][0]
    replacement=''.join('\n' if char=='\n' else 'X' for char in row['before']['text'])
    for quote in (row['before'],row['after']['original_slot']):
        quote['text']=replacement;quote['sha256']=hashlib.sha256(replacement.encode()).hexdigest()
    with pytest.raises(ValueError):validate_impact(sealed(value),pack=pack)


@pytest.mark.parametrize('unit',['files','utf8_bytes','lines'])
def test_declared_inventory_must_accommodate_captured_file_witnesses(versions,unit):
    value,_=projection(versions)
    if unit=='files':value['coverage']['after_admitted_files']=0
    for row in value['entries']:row['after']['search'][unit+'_total']=0
    with pytest.raises(ValueError):validate_impact(sealed(value))


def test_missing_target_path_cannot_also_supply_an_exact_match(versions):
    _,_,pack=versions;repo=Path(pack['snapshot']['repository']);parser=repo/'parser.py'
    parser.write_text('# shifted\n'+parser.read_text(encoding='utf-8'),encoding='utf-8')
    value,row=projection(versions,commit(repo))
    assert row['status']=='moved'
    row['after'].update(path_status='missing',same_path_file=None,original_slot=None)
    with pytest.raises(ValueError):validate_impact(sealed(value))


def test_same_path_file_bytes_must_accommodate_its_excerpt(versions):
    value,row=projection(versions)
    row['after']['same_path_file']['utf8_bytes']=0
    with pytest.raises(ValueError):validate_impact(sealed(value))


def test_markdown_describes_two_revisions_and_declared_basis(versions):
    value,_=projection(versions)
    text=render_markdown(value)
    assert 'Before commit: '+value['before']['commit_sha'] in text
    assert 'After commit: '+value['after']['commit_sha'] in text
    assert 'Fix status: unverified' in text
    assert 'declared captured local observation' in text
    assert '    def parse_list(items):' in text
