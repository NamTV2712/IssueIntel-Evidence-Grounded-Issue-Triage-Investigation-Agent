"""Standalone, standard-library-only handoff integrity verifier.

This file is also copied into handoffs as verify.py. Never imports or executes
bundle contents, extracts paths, accesses Git, reads credentials or uses network.
Hashes establish internal consistency, not authorship or semantic correctness.
"""
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile

MAX_BYTES = 100_000_000
MAX_FILES = 512
SCHEMA = 'issueintel_handoff_v1'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')


def parsed(raw):
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('Duplicate JSON key')
            result[key]=value
        return result
    return json.loads(raw,object_pairs_hook=unique,parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))


def sealed(value):
    return type(value) is dict and value.get('sha256')==sha(encoded({k:v for k,v in value.items() if k!='sha256'}))


def safe_path(name):
    return (type(name) is str and re.fullmatch(r'[a-zA-Z0-9_./-]+',name) is not None
            and all(part not in {'','.','..'} for part in name.split('/'))
            and not name.startswith('/') and len(name)<160)


def verify(path, *, expected_sha256=None):
    path=Path(path)
    if path.stat().st_size>MAX_BYTES:raise ValueError('Handoff archive exceeds the size bound')
    raw=path.read_bytes()
    if expected_sha256 is not None and (not re.fullmatch('[a-f0-9]{64}',expected_sha256) or sha(raw)!=expected_sha256):
        raise ValueError('Handoff archive differs from the trusted expected SHA256')
    # No extraction: even malicious names, links and compressed bombs remain data.
    import io
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos=archive.infolist();names=[info.filename for info in infos]
        if (not 1<len(infos)<=MAX_FILES or len(set(names))!=len(names)
                or sum(i.file_size for i in infos)>MAX_BYTES
                or any(not safe_path(i.filename) or i.is_dir() or i.flag_bits&1
                       or (i.external_attr>>16)&0o170000 not in {0,0o100000}
                       or i.compress_type not in {zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED}
                       for i in infos)):
            raise ValueError('Unsafe or oversized handoff inventory')
        contents={i.filename:archive.read(i) for i in infos}
    manifest=parsed(contents.get('manifest.json',b'{}'))
    if (not sealed(manifest) or manifest.get('schema')!=SCHEMA
            or type(manifest.get('entries')) is not list
            or type(manifest.get('model_calls')) is not int or manifest['model_calls']!=0
            or manifest.get('evaluation_performed') is not False):
        raise ValueError('Invalid handoff manifest')
    listed={}
    for entry in manifest['entries']:
        if (type(entry) is not dict or set(entry)!={'path','kind','bytes','sha256'}
                or not safe_path(entry['path']) or entry['path']=='manifest.json' or entry['path'] in listed
                or type(entry['bytes']) is not int or entry['bytes']<0 or type(entry['kind']) is not str):
            raise ValueError('Invalid handoff entry')
        value=contents.get(entry['path'])
        if value is None or len(value)!=entry['bytes'] or sha(value)!=entry['sha256']:
            raise ValueError('Handoff entry bytes or hash changed: '+entry['path'])
        listed[entry['path']]=entry
    if set(contents)!=set(listed)|{'manifest.json'}:raise ValueError('Unlisted handoff files')
    if not {'index.html','verify.py','handoff.json','README.txt'}<=set(listed):raise ValueError('Incomplete handoff tools')
    handoff=parsed(contents['handoff.json'])
    if (not sealed(handoff) or handoff.get('schema')!='issueintel_handoff_selection_v1'
            or handoff['sha256']!=manifest.get('selection_sha256')
            or handoff.get('source')!=manifest.get('source') or type(handoff.get('runs')) is not list):
        raise ValueError('Invalid handoff selection binding')
    runs={row['job_id']:row for row in handoff['runs']}
    if (len(runs)!=len(handoff['runs']) or set(runs)!=set(manifest.get('included_job_ids',[]))
            or not runs or manifest['source']['selected_job_id'] not in runs):
        raise ValueError('Invalid handoff run selection')
    selected=runs[manifest['source']['selected_job_id']]
    if not selected.get('binding') or selected['binding']['pack_sha256']!=manifest['source']['selected_pack_sha256']:
        raise ValueError('Selected source pack differs from the manifest')
    for identifier,row in runs.items():
        if not re.fullmatch('[a-f0-9]{24}',identifier):raise ValueError('Invalid handoff job ID')
        if row['parent_job_id'] is not None and row['parent_job_id'] not in runs:
            raise ValueError('Missing required handoff ancestor')
        visited=set();current=identifier
        while current is not None:
            if current in visited or len(visited)>10:raise ValueError('Invalid handoff ancestry')
            visited.add(current);current=runs[current]['parent_job_id']
        if row['report'] is None:continue
        prefix='runs/'+identifier+'/'
        pack_raw=contents.get(prefix+'pack.json');report_raw=contents.get(prefix+'report.json')
        if pack_raw is None or report_raw is None:raise ValueError('Missing source pack or report')
        pack=parsed(pack_raw);report=parsed(report_raw);binding=row['binding']
        if (not sealed(report) or report!=row['report'] or report['sha256']!=binding['report_sha256']
                or sha(pack_raw)!=binding['pack_sha256']
                or report['source']['pack_sha256']!=binding['pack_sha256']
                or report['source']['job_id']!=identifier
                or report['source']['query_sha256']!=sha(pack['case']['problem_statement'].encode())
                or any(report['source'][key]!=binding[key] for key in ('query_sha256','snapshot_sha256','commit_sha'))
                or pack['snapshot']['snapshot_sha256']!=binding['snapshot_sha256']
                or pack['snapshot']['commit_sha']!=binding['commit_sha']):
            raise ValueError('Changed report/pack source binding')
        evidence={e['id']:e for e in pack['evidence']}
        for excerpt in report['evidence']:
            source=evidence.get(excerpt['id'])
            if (source is None or sha(excerpt['text'].encode())!=excerpt['excerpt_sha256']
                    or not source['text'].startswith(excerpt['text'])
                    or any(source[k]!=excerpt[k] for k in ('kind','locator','sha256','revision'))):
                raise ValueError('Changed evidence excerpt binding')
        link=pack.get('followup') or pack.get('context_selection')
        if link and runs[link['parent_job_id']]['binding']['pack_sha256']!=link['parent_pack_sha256']:
            raise ValueError('Changed parent pack binding')
        recovery=pack.get('resumed_from') or pack.get('finalized_from')
        if recovery:
            parent=recovery['source_job_id']
            checkpoints={'verification_input':recovery.get('checkpoint_sha256') or recovery.get('verification_input_sha256'),
                         'verification':recovery.get('verification_sha256')}
            for stage,checksum in checkpoints.items():
                if checksum is not None and sha(contents.get('runs/'+parent+'/stages/'+stage+'.json',b''))!=checksum:
                    raise ValueError('Missing or changed recovery checkpoint')
    return {'schema':'issueintel_handoff_integrity_v1','archive_sha256':sha(raw),
            'manifest_sha256':manifest['sha256'],'files':len(contents),'runs':len(runs),
            'integrity':'consistent','authorship_verified':False,'semantic_verified':False,
            'model_calls':0,'evaluation_performed':False}


def main(argv=None):
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive',type=Path)
    parser.add_argument('--expected-sha256')
    args=parser.parse_args(argv)
    try:
        print(json.dumps(verify(args.archive,expected_sha256=args.expected_sha256),indent=2));return 0
    except (ValueError,KeyError,TypeError,OSError,zipfile.BadZipFile,RuntimeError) as error:
        print('Handoff verification failed: '+str(error),file=sys.stderr);return 2


if __name__=='__main__':
    raise SystemExit(main())
