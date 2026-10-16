"""Bounded local Git ancestry facts, never release or fix verification.

The caller supplies the two snapshot commit IDs. Only an explicitly supplied
reference may be a short revision. No fetch, checkout, hook or target code is
run. Negative ancestry is deliberately unknown for shallow/altered histories.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import threading
import time

from issueintel.repository.acquire import _validate_revision
from issueintel.runtime.io import digest


TAG_LIMIT = 200
_OUTPUT_LIMIT = 2 * 1024 * 1024
_COMMAND_TIMEOUT = 5
_TOTAL_TIMEOUT = 60
_OID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


class _Unavailable(Exception):
    def __init__(self, reason):
        self.reason = reason


class _LocalGit:
    """Capture a bounded amount of stdout without buffering arbitrary output."""

    def __init__(self, repository, check_cancelled):
        self.repository = repository
        self.check_cancelled = check_cancelled or (lambda: None)
        self.deadline = time.monotonic() + _TOTAL_TIMEOUT
        self.environment = os.environ.copy()
        # A caller's shell must not silently retarget the selected repository.
        for key in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR', 'GIT_INDEX_FILE',
                    'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES',
                    'GIT_SHALLOW_FILE', 'GIT_NAMESPACE', 'GIT_REPLACE_REF_BASE'):
            self.environment.pop(key, None)
        self.environment.update({
            'GIT_NO_REPLACE_OBJECTS': '1', 'GIT_NO_LAZY_FETCH': '1',
            'GIT_OPTIONAL_LOCKS': '0', 'GIT_TERMINAL_PROMPT': '0',
            'GIT_ALLOW_PROTOCOL': '', 'GIT_GRAFT_FILE': os.devnull,
        })

    def call(self, *arguments):
        self.check_cancelled()
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise _Unavailable('inspection_timeout')
        command = ['git', '-c', 'core.hooksPath=', '-c', 'credential.helper=',
                   '-c', 'protocol.allow=never', '-c', 'core.commitGraph=false',
                   '-C', str(self.repository), *arguments]
        output = bytearray()
        overflow = threading.Event()
        with subprocess.Popen(command, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              env=self.environment) as process:
            def drain():
                while chunk := process.stdout.read(65536):
                    if len(output) + len(chunk) > _OUTPUT_LIMIT:
                        overflow.set()
                        process.kill()
                        return
                    output.extend(chunk)

            reader = threading.Thread(target=drain, daemon=True)
            reader.start()
            try:
                process.wait(timeout=min(_COMMAND_TIMEOUT, remaining))
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                reader.join()
                raise _Unavailable('inspection_timeout') from None
            reader.join()
            self.check_cancelled()
            if overflow.is_set():
                raise _Unavailable('inspection_output_limit')
            return process.returncode, bytes(output)

    def require(self, *arguments):
        code, output = self.call(*arguments)
        if code:
            # Git errors may contain account/configuration data. Keep them out
            # of product artifacts and exception messages.
            raise ValueError('Local Git inspection could not resolve the requested objects')
        return output


def _resolve(git, revision, *, full=False):
    if full:
        if not isinstance(revision, str) or not _OID.fullmatch(revision):
            raise ValueError('Source and target require full commit object IDs')
    else:
        _validate_revision(revision)
    raw = git.require('rev-parse', '--verify', '--end-of-options', revision + '^{commit}')
    commit = raw.decode('ascii').strip()
    if not _OID.fullmatch(commit) or (full and commit != revision):
        raise ValueError('Git did not return the requested full commit object ID')
    return commit


def _ref_inventory(git):
    # Enumerate ref names/OIDs once; ancestry work uses these captured OIDs,
    # never a mutable tag name. The count is exact for this bounded snapshot.
    output = git.require('for-each-ref', '--sort=refname',
                         '--format=%(refname)%09%(objectname)%09%(symref)',
                         'refs/tags/', 'refs/replace/')
    tags = []
    replacements = False
    for line in output.decode('utf-8').splitlines():
        name, oid, symbolic = line.split('\t')
        if not _OID.fullmatch(oid) or any(ord(c) < 32 for c in name):
            raise ValueError('Invalid local Git ref inventory')
        if name.startswith('refs/tags/'):
            tags.append({'name': name[len('refs/tags/'):], 'object_sha': oid,
                         'symbolic': bool(symbolic)})
        elif name.startswith('refs/replace/'):
            replacements = True
    return tags, replacements


def inspect_reachability(repository, source_commit, target_commit,
                         reference_revision=None, *, check_cancelled=None):
    """Capture local ancestry and tags containing an optional reference commit.

    ``contained`` means Git ancestry at the pinned target, not a published
    release, semantic patch equivalence, successful test, or a validated fix.
    All refs are local and at most 200 tags receive containment checks. Missing
    history/time/output bounds preserve unknown rather than certify absence.
    """
    repository = Path(repository).resolve()
    if not repository.is_dir():
        raise ValueError('Require an existing local Git repository')
    git = _LocalGit(repository, check_cancelled)
    try:
        source = _resolve(git, source_commit, full=True)
        target = _resolve(git, target_commit, full=True)
        reference = _resolve(git, reference_revision) if reference_revision is not None else None
        tags, replacements = _ref_inventory(git)
        shallow_value = git.require('rev-parse', '--is-shallow-repository').decode('ascii').strip()
        if shallow_value not in {'true', 'false'}:
            raise ValueError('Could not establish local shallow-history status')
        common = Path(os.fsdecode(git.require('rev-parse', '--git-common-dir').strip()))
        common = common if common.is_absolute() else repository / common
        graft_paths = [common / 'info' / 'grafts']
        custom_grafts = os.environ.get('GIT_GRAFT_FILE')
        if custom_grafts:
            graft_paths.append(Path(custom_grafts))
        grafts = any(path.exists() and path.stat().st_size for path in graft_paths)
    except _Unavailable as exc:
        # Without an exact inventory/binding, reporting a partial count would be
        # misleading. Traversal failures below can instead be represented.
        raise ValueError('Local Git metadata inspection exceeded its configured bounds') from exc
    shallow = shallow_value == 'true'
    # Traverse raw commit parents instead of mutable shallow boundaries. Another
    # fetch/process may rewrite the real shallow file after our metadata sample;
    # treating a newly truncated walk as a complete negative would be unsound.
    # Missing parent objects still make Git fail and remain unknown below.
    git.environment['GIT_SHALLOW_FILE'] = os.devnull
    altered = bool(replacements or grafts)
    history = {'shallow': shallow, 'altered': altered,
               'complete': not (shallow or altered),
               'reason': ('altered_history' if altered else 'shallow_history' if shallow
                          else 'complete_local_history')}

    def contains(ancestor, descendant):
        git.check_cancelled()
        if ancestor == descendant:
            return True, 'identical_commit'
        if altered:
            return None, 'altered_history'
        try:
            code, _ = git.call('merge-base', '--is-ancestor', ancestor, descendant)
        except _Unavailable as exc:
            history['complete'] = False
            if not shallow:
                history['reason'] = 'history_incomplete'
            return None, exc.reason
        if code == 0:
            return True, 'reference_ancestor'
        if code == 1 and not shallow:
            return False, 'no_ancestor_path'
        if code != 1:
            history['complete'] = False
            if not shallow:
                history['reason'] = 'history_incomplete'
        return None, 'shallow_history' if shallow else 'history_incomplete'

    if source == target:
        relation = {'status': 'same_commit', 'reason': 'identical_commit',
                    'ancestry_complete': history['complete']}
    else:
        forward, forward_reason = contains(source, target)
        backward, backward_reason = ((None, None) if forward is True else contains(target, source))
        status = ('source_ancestor' if forward is True else 'target_ancestor' if backward is True
                  else 'diverged' if forward is False and backward is False else 'unknown')
        reason = ('source_reaches_target' if forward is True else 'target_reaches_source'
                  if backward is True else 'no_ancestor_path' if status == 'diverged'
                  else forward_reason if forward is None else backward_reason)
        relation = {'status': status, 'reason': reason,
                    'ancestry_complete': not (shallow or altered) and status != 'unknown'}
    reference_result = None
    tag_result = {'scope': 'local_git_refs', 'scanned': 0, 'total': len(tags),
                  'total_count_exact': True, 'limit': TAG_LIMIT, 'truncated': False,
                  'containing': [], 'unavailable': 0, 'refs_sha256': digest(tags),
                  'reason': 'reference_not_requested'}
    if reference is not None:
        contained, reason = contains(reference, target)
        reference_result = {'requested_revision': reference_revision, 'commit_sha': reference,
                            'status': 'contained' if contained is True else 'not_contained'
                            if contained is False else 'unknown',
                            'reason': 'reference_equals_target' if reference == target else reason}
        tag_result['reason'] = 'local_tag_scan_limit' if len(tags) > TAG_LIMIT else 'local_tags_only'
        tag_result['truncated'] = len(tags) > TAG_LIMIT
        for tag in tags[:TAG_LIMIT]:
            git.check_cancelled()
            tag_result['scanned'] += 1
            if tag['symbolic'] or len(tag['name']) > 1024:
                tag_result['unavailable'] += 1
                continue
            try:
                commit = _resolve(git, tag['object_sha'])
                present, _ = contains(reference, commit)
            except ValueError:
                present = None
            except _Unavailable:
                present = None
                history['complete'] = False
                if not (shallow or altered):
                    history['reason'] = 'history_incomplete'
            if present is True:
                tag_result['containing'].append({'name': tag['name'], 'commit_sha': commit})
            elif present is None:
                tag_result['unavailable'] += 1
    result = {
        'schema': 'product_git_reachability_v1', 'source_commit_sha': source,
        'target_commit_sha': target, 'source_to_target': relation,
        'reference': reference_result, 'tags': tag_result, 'history': history,
        'fix_status': 'unverified', 'publication_status': 'unverified',
        'evaluation_performed': False, 'model_calls': 0,
        'limitations': [
            'Ancestry is not proof that a change fixes the issue or is semantically equivalent to a patch.',
            'Tags are captured local Git refs, not verified published releases or release dates.',
            'No fetch, checkout, target code/tests, model call, or benchmark evaluation is performed.',
            'Missing paths in shallow, altered, unavailable or timed-out history remain unknown.',
            'Tag checks use at most 200 refs sorted by name; unavailable/non-commit, symbolic or oversized tag names are counted separately.',
            'Source and target snapshots/citations are not revalidated by this ancestry-only view.',
        ],
    }
    result['sha256'] = digest(result)
    return result
