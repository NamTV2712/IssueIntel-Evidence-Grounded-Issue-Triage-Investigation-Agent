"""Explicit public release observations, separate from immutable investigations."""
import hashlib
import json
import re

from issueintel.integrations.github import github_input
from issueintel.integrations.github_index import GitHubReader
from issueintel.repository.acquire import _validate_revision
from issueintel.runtime.io import digest, write_json
from .versions import product_version_source


class ReleaseJobs:
    def __init__(self, workbench):
        self.host = workbench

    def source(self, identifier):
        host = self.host
        pack, raw, _ = host._load_pack(identifier)
        product_version_source(pack)
        current, seen = identifier, set()
        for _ in range(10):
            if current in seen:
                raise ValueError('Release source authority is cyclic')
            seen.add(current)
            saved = json.loads(host._job_path(current).read_bytes())['request']
            mode = saved.get('mode')
            if mode == 'context':
                current = saved['parent_job_id']
            elif mode == 'resume':
                current = saved['source_job_id']
            elif mode == 'github':
                source = github_input(saved['issue_url'])
                if (source.issue_url != pack['case'].get('issue_url')
                        or pack['case']['case_id'] != source.issue_url
                        or source.repository.casefold() != pack['case']['repository'].casefold()):
                    raise ValueError('Release inspection repository authority changed')
                return pack, raw, source.repository.lower()
            else:
                raise ValueError('Release inspection requires an explicit GitHub product investigation')
        raise ValueError('Release source authority exceeds its bound')

    @staticmethod
    def binding(identifier, pack, raw):
        return {'job_id': identifier, 'pack_sha256': hashlib.sha256(raw).hexdigest(),
                'commit_sha': pack['snapshot']['commit_sha'],
                'snapshot_sha256': pack['snapshot']['snapshot_sha256'],
                'query_sha256': hashlib.sha256(pack['case']['problem_statement'].encode()).hexdigest()}

    def submit(self, request):
        if not isinstance(request, dict) or set(request) != {'job_id','tag','reference_commit'}:
            raise ValueError('Choose an exact GitHub release tag and reference commit')
        tag = _validate_revision(request['tag'])
        reference = request['reference_commit']
        if (len(tag) > 200 or not isinstance(reference, str)
                or not re.fullmatch(r'[a-f0-9]{40}|[a-f0-9]{64}', reference)):
            raise ValueError('Require a bounded tag and full reference commit SHA')
        pack, raw, repository = self.source(request['job_id'])
        return self.host._enqueue({'mode':'release_check', 'provider':'none', 'depth':'release',
            'source_job_id':request['job_id'], 'source_pack_sha256':hashlib.sha256(raw).hexdigest(),
            'repository':repository, 'tag':tag, 'reference_commit':reference}, self.run)

    def history(self, identifier):
        host = self.host
        self.source(identifier)
        rows = []
        for path in sorted(host.jobs.glob('*.json'), key=lambda p:p.stat().st_mtime, reverse=True):
            row = json.loads(path.read_bytes())
            if row.get('mode') == 'release_check' and row.get('request',{}).get('source_job_id') == identifier:
                rows.append(host.job(row['id']))
                if len(rows) == 20:
                    break
        return rows

    def result_bytes(self, identifier):
        from issueintel.integrations.github_releases import validate_release_check
        host = self.host
        row = host.job(identifier)
        if row['mode'] != 'release_check' or row['status'] != 'completed':
            raise ValueError('Release inspection is not complete')
        request = json.loads(host._job_path(identifier).read_bytes())['request']
        expected = (host.directory/'runs'/identifier/'releases.json').resolve()
        ref = row['result']['releases.json']
        path = (host.root/ref['path']).resolve()
        if path != expected or not path.is_relative_to(host.directory.resolve()):
            raise ValueError('Release export path changed')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != ref['sha256']:
            raise ValueError('Release export integrity changed')
        result = validate_release_check(json.loads(raw))
        pack, source_raw, repository = self.source(request['source_job_id'])
        if (result.get('source') != self.binding(request['source_job_id'], pack, source_raw)
                or result['source']['pack_sha256'] != request['source_pack_sha256']
                or result['repository']['name'] != repository or request['repository'] != repository
                or result['requested'] != {'tag':request['tag'],'reference_commit':request['reference_commit']}):
            raise ValueError('Release inspection source or request binding changed')
        return raw

    def run(self, row):
        from issueintel.integrations.github_releases import inspect_release, validate_release_check
        host = self.host
        try:
            host._check_cancelled(row['id'])
            host._save(row, status='running', stage='inspect_release')
            request = row['request']
            pack, raw, repository = self.source(request['source_job_id'])
            if (hashlib.sha256(raw).hexdigest() != request['source_pack_sha256']
                    or request['repository'] != repository):
                raise ValueError('Release source changed while queued')
            def retain(receipt):
                sequence = receipt['sequence']
                if type(sequence) is not int or not 1 <= sequence <= 16:
                    raise ValueError('Release receipt limit exceeded')
                path = host.directory/'runs'/row['id']/'release_receipts'/f'{sequence:02d}.json'
                if path.exists():
                    raise ValueError('Release receipt already exists')
                write_json(path, receipt)
                host._save(row, completed_requests=sequence)
            result = inspect_release(repository, request['tag'], request['reference_commit'],
                reader=GitHubReader(repository), check_cancelled=lambda:host._check_cancelled(row['id']),
                on_receipt=retain)
            result['source'] = self.binding(request['source_job_id'], pack, raw)
            result.pop('sha256')
            result['sha256'] = digest(result)
            validate_release_check(result)
            host._check_cancelled(row['id'])
            _, current_raw, _ = self.source(request['source_job_id'])
            if hashlib.sha256(current_raw).hexdigest() != request['source_pack_sha256']:
                raise ValueError('Release source changed during inspection')
            path = host.directory/'runs'/row['id']/'releases.json'
            write_json(path, result)
            host._save(row, status='completed', stage='completed', source_job_id=request['source_job_id'],
                result={'releases.json':{'path':path.relative_to(host.root).as_posix(),
                                        'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}})
        except InterruptedError:
            host._save(row, status='cancelled', stage='cancelled')
        except Exception as error:
            host._save(row, status='failed', stage='failed',
                error=f'Release inspection failed ({type(error).__name__}); completed receipts retained, no automatic retry.')
        finally:
            with host.lock:
                host.active.discard(row['id']); host.cancelled.discard(row['id'])
            host.capacity.release()
