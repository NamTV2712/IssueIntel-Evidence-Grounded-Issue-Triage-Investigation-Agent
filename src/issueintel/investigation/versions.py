"""Bounded descriptive code changes, never a claim that an issue is fixed."""
from collections import Counter
import difflib
import hashlib

from issueintel.repository.snapshot import source_lines
from issueintel.runtime.io import digest
from .comparison import compare_packs
from .pipeline import validate_prepared_evidence


def product_version_source(pack, *, owned_product=False):
    if type(owned_product) is not bool:
        raise ValueError('Choose an explicit product version authority')
    case = pack['case']
    if (any(key in case for key in ('split','instance_id'))
            or case['case_id'].startswith(('verified:', 'live:', 'main_dev:', 'main_test:'))
            or pack.get('followup') and not owned_product or pack['snapshot']['protocol'] != 'benchmark_base'):
        raise ValueError('Version inspection requires an original product investigation, outside benchmark splits')
    # Replay citations/context without transferring their validity to another commit.
    compare_packs(pack, pack)
    return case


def _descriptor(snapshot):
    value = snapshot.to_dict()
    value['files'] = [{k: v for k, v in file.to_dict().items() if k != 'text'} for file in snapshot.files]
    return value


def _diff(old, new, path):
    left = source_lines(old.text) if old else []
    right = source_lines(new.text) if new else []
    # SequenceMatcher has quadratic worst cases. Bound input as well as output.
    if len(left) > 2000 or len(right) > 2000 or sum(map(len, left+right)) > 40000:
        return {'text': '', 'truncated': True, 'reason': 'diff_input_limit'}
    chunks = []; size = 0; truncated = False
    for line in difflib.unified_diff(left, right, fromfile='before/'+path, tofile='after/'+path, n=3, lineterm=''):
        if size+len(line)+1 > 12000:
            truncated = True
            break
        chunks.append(line); size += len(line)+1
    text = '\n'.join(chunks)
    return {'text': text, 'sha256': hashlib.sha256(text.encode()).hexdigest(),
            'truncated': truncated, 'reason': 'diff_output_limit' if truncated else None}


def compare_versions(pack, before, after, *, owned_product=False):
    """Compare admitted snapshot files; skipped sources remain unavailable."""
    case = product_version_source(pack,owned_product=owned_product)
    before.verify(); after.verify()
    if (before.to_dict()['repository'] != after.to_dict()['repository']
            or after.protocol != 'benchmark_base' or after.cutoff is not None
            or digest(_descriptor(before)) != digest(pack['snapshot'])):
        raise ValueError('Version snapshots differ from the bound product repository/source')
    validate_prepared_evidence(case, [e for e in pack['evidence'] if e['kind'] != 'repository_context'], before)
    old = {file.path: file for file in before.files}
    new = {file.path: file for file in after.files}
    skipped_old = {row['path']: row['reason'] for row in before.skipped if 'path' in row}
    skipped_new = {row['path']: row['reason'] for row in after.skipped if 'path' in row}
    relevant = {e['locator'].rsplit(':', 1)[0] for e in pack['evidence'] if e['kind'] == 'code'}
    counts = Counter(); changes = []; diff_count = 0; candidates = 0
    for path in sorted(old.keys() | new.keys() | skipped_old.keys() | skipped_new.keys(), key=lambda p: (p not in relevant, p)):
        a, b = old.get(path), new.get(path)
        unavailable = path in skipped_old or path in skipped_new
        status = ('unavailable' if unavailable else 'added' if a is None else 'removed' if b is None
                  else 'unchanged' if a.raw_sha256 == b.raw_sha256 else 'modified')
        counts[status] += 1
        if status == 'unchanged' and path not in relevant:
            continue
        candidates += 1
        if len(changes) >= 200:
            continue
        def reference(file, snapshot):
            return ({'sha256': file.sha256, 'raw_sha256': file.raw_sha256, 'blob_sha': file.blob_sha,
                     'commit_sha': snapshot.commit_sha} if file else None)
        row = {'path': path, 'status': status, 'in_source_evidence': path in relevant,
               'before': reference(a, before), 'after': reference(b, after),
               'unavailable_before': skipped_old.get(path), 'unavailable_after': skipped_new.get(path)}
        if status in {'added', 'removed', 'modified'} and path in relevant and diff_count < 4:
            row['diff'] = _diff(a, b, path); diff_count += 1
        changes.append(row)
    result = {'schema': 'product_version_comparison_v1', 'repository': case['repository'],
              'case_id': case['case_id'], 'query_sha256': hashlib.sha256(case['problem_statement'].encode()).hexdigest(),
              'before': {'commit_sha': before.commit_sha, 'snapshot_sha256': before.snapshot_sha256},
              'after': {'commit_sha': after.commit_sha, 'snapshot_sha256': after.snapshot_sha256},
              'counts': {status: counts[status] for status in ('added', 'removed', 'modified', 'unchanged', 'unavailable')},
              'files': changes, 'files_omitted': candidates-len(changes),
              'scope': 'bounded_tracked_text_snapshots', 'source_evidence_paths': sorted(relevant),
              'before_skipped': len(before.skipped), 'after_skipped': len(after.skipped),
              'fix_status': 'unverified', 'evaluation_performed': False, 'model_calls': 0,
              'limitations': ['Code changes do not prove a fix or a released version containing a fix.',
                  'No target code/tests are executed; this file diff alone does not establish ancestry or release publication.',
                  'Only admitted tracked text is compared. Skipped files are unavailable, not deleted.',
                  'Renames appear as added/removed. Diffs cover at most four source-evidence files.',
                  'Diffs normalize line endings; raw-byte hashes also detect changes invisible in the text diff.',
                  'Prior hypotheses/citations are not revalidated against the target revision.']}
    result['sha256'] = digest(result)
    return result
