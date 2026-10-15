import hashlib
import subprocess

from issueintel.repository.snapshot import build_snapshot, source_lines
from issueintel.repository.graph import build_graph
from issueintel.retrieval import retrieve_evidence
from issueintel.investigation.pipeline import investigate, validate_prepared_evidence


def repository(tmp_path,text):
    def git(*args):
        return subprocess.check_output(['git','-c','core.hooksPath=','-C',str(tmp_path),*args]).decode().strip()
    git('init','-q')
    (tmp_path/'parser.py').write_text(text,encoding='utf-8')
    git('add','parser.py')
    git('-c','user.name=Synthetic','-c','user.email=test@example.invalid','-c','commit.gpgsign=false','commit','-qm','Synthetic fixture')
    return git('rev-parse','HEAD')


def test_overlong_lines_are_skipped_without_fabricating_partial_line_citations(tmp_path):
    text='#'+'x'*1800+'\ndef parse_list(items):\n    return items\n'
    commit=repository(tmp_path,text)
    snapshot=build_snapshot(tmp_path,commit)
    evidence=retrieve_evidence('parser parse_list',snapshot,build_graph(snapshot))
    assert evidence
    for record in evidence:
        start,end=map(int,record['locator'].rsplit(':',1)[1].split('-'))
        assert start>=2
        assert record['text']=='\n'.join(source_lines(text)[start-1:end])
        assert len(record['text'])<=1600
        assert hashlib.sha256(record['text'].encode()).hexdigest()==record['sha256']
    case={'case_id':'synthetic:parser','repository':'synthetic/repo','revision':commit,
          'problem_statement':'parser parse_list'}
    pack=investigate(case,tmp_path,snapshot=snapshot)
    validate_prepared_evidence(case,pack['evidence'],snapshot)


def test_only_overlong_or_blank_text_yields_no_invented_excerpt(tmp_path):
    commit=repository(tmp_path,'#'+'parser '*400+'\n\n')
    snapshot=build_snapshot(tmp_path,commit)
    assert retrieve_evidence('parser',snapshot,build_graph(snapshot))==[]
