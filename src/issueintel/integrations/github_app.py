"""Lazy, repository-scoped GitHub App installation authentication.

This broker never falls back to a PAT, follows redirects, retries a mint, or
stores credentials on disk. Callers still own local installation/connection
admission and the explicit approval of a particular GitHub write.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import stat
from threading import RLock
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener


API = 'https://api.github.com'
MAX_KEY_BYTES = 65536
MAX_RESPONSE_BYTES = 1_000_000
_MAX_ID = (1 << 63) - 1
_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}')
_HEADER_TOKEN = re.compile(r'[A-Za-z0-9_.-]{1,16384}')
_READ = {'contents': 'read', 'issues': 'read', 'pull_requests': 'read'}
_COMMENT = {'issues': 'write'}
_MESSAGES = {
    'not_configured': 'GitHub App signing credentials are not configured',
    'configuration_invalid': 'GitHub App configuration is invalid',
    'unsafe_private_key': 'GitHub App private key must be a bounded regular file without links',
    'invalid_private_key': 'GitHub App private key is not an unencrypted private PEM',
    'jwt_dependency_missing': 'Install the github-app extra to sign GitHub App JWTs',
    'jwt_signing_failed': 'GitHub App JWT signing failed',
    'invalid_binding': 'GitHub App requires a canonical repository and positive public identity bindings',
    'request_failed': 'GitHub App request failed; no automatic retry was attempted',
    'redirect_forbidden': 'GitHub App authentication refuses redirects',
    'unexpected_status': 'GitHub App did not confirm the requested authentication operation',
    'invalid_response': 'GitHub App returned an invalid bounded JSON response',
    'response_too_large': 'GitHub App authentication response exceeds its byte bound',
    'installation_mismatch': 'GitHub App installation differs from its local binding',
    'installation_suspended': 'GitHub App installation is suspended or its active status is unknown',
    'installation_permissions': 'GitHub App installation lacks the requested minimum permissions',
    'token_invalid': 'GitHub App returned an invalid installation token',
    'token_expiry': 'GitHub App installation token has an invalid or excessive expiry',
    'token_permissions': 'GitHub App installation token permissions differ from the requested minimum',
    'token_repository': 'GitHub App installation token is not limited to the bound public repository',
    'repository_mismatch': 'GitHub App repository identity differs from its local binding',
    'repository_not_public': 'GitHub App access is limited to public repositories',
}


class GitHubAppError(ValueError):
    """Stable safe codes, never provider bodies, token values or PEM paths."""
    def __init__(self, code, *, http_status=None):
        self.code = code if code in _MESSAGES else 'request_failed'
        self.http_status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        super().__init__(_MESSAGES[self.code])


def _id(value, *, environment=False, code='invalid_binding'):
    if environment and isinstance(value, str) and re.fullmatch(r'[1-9][0-9]{0,18}', value):
        value = int(value)
    if type(value) is not int or not 0 < value <= _MAX_ID:
        raise GitHubAppError(code)
    return value


def _repository(value):
    if (not isinstance(value, str) or not _NAME.fullmatch(value)
            or value.split('/')[1] in {'.', '..'}):
        raise GitHubAppError('invalid_binding')
    return value.lower()


def _utc(clock):
    try:
        value = clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError()
        result = value.astimezone(timezone.utc)
        if not math.isfinite(result.timestamp()):
            raise ValueError()
        return result
    except (ValueError, TypeError, OverflowError, OSError):
        raise GitHubAppError('configuration_invalid') from None


def _no_links(path):
    """Reject aliases before and after opening; do not resolve through links."""
    for current in (*reversed(path.parents), path):
        info = current.lstat()
        if (stat.S_ISLNK(info.st_mode)
                or getattr(info, 'st_file_attributes', 0) & 0x400
                or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1)):
            raise GitHubAppError('unsafe_private_key')
    return path.lstat()


def _private_key(path):
    try:
        path = Path(os.path.abspath(path))
        before = _no_links(path)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_KEY_BYTES:
            raise GitHubAppError('unsafe_private_key')
        flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, 'rb') as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino, opened.st_size) != (before.st_dev, before.st_ino, before.st_size):
                raise GitHubAppError('unsafe_private_key')
            raw = stream.read(before.st_size + 1)
        after = _no_links(path)
        if (len(raw) != before.st_size or (after.st_dev, after.st_ino, after.st_size) !=
                (before.st_dev, before.st_ino, before.st_size)):
            raise GitHubAppError('unsafe_private_key')
    except GitHubAppError:
        raise
    except (OSError, TypeError, ValueError):
        raise GitHubAppError('unsafe_private_key') from None
    stripped = raw.strip()
    if not any(stripped.startswith(b'-----BEGIN ' + label + b'-----')
               and stripped.endswith(b'-----END ' + label + b'-----')
               for label in (b'RSA PRIVATE KEY', b'PRIVATE KEY')):
        raise GitHubAppError('invalid_private_key')
    return raw


@dataclass(frozen=True)
class GitHubAppConfig:
    app_id: int
    private_key_path: Path = field(repr=False)
    client_id: str | None = None
    installation_id: int | None = None

    def __post_init__(self):
        _id(self.app_id, code='configuration_invalid')
        if self.installation_id is not None:
            _id(self.installation_id, code='configuration_invalid')
        if (self.client_id is not None and (not isinstance(self.client_id, str)
                or not re.fullmatch(r'[A-Za-z0-9_.-]{1,200}', self.client_id))):
            raise GitHubAppError('configuration_invalid')
        try:
            value = Path(self.private_key_path)
            if not value.is_absolute():
                raise ValueError()
        except (TypeError, ValueError):
            raise GitHubAppError('configuration_invalid') from None
        object.__setattr__(self, 'private_key_path', value)

    @classmethod
    def load(cls, root=Path('.'), environ=None, *, required=False):
        """Read environment only when called; opening/signing the PEM is later.

        A webhook-only App ID is not signing material. App-bound callers use
        ``required=True`` so missing credentials can never imply PAT fallback.
        """
        if type(required) is not bool:
            raise GitHubAppError('configuration_invalid')
        source = os.environ if environ is None else environ
        try:
            values = {key: source.get(key, '') for key in ('GITHUB_APP_ID',
                'GITHUB_APP_PRIVATE_KEY_PATH', 'GITHUB_APP_CLIENT_ID', 'GITHUB_INSTALLATION_ID')}
            if any(not isinstance(value, str) for value in values.values()):
                raise ValueError()
            values = {key: value.strip() for key, value in values.items()}
            app = _id(values['GITHUB_APP_ID'], environment=True, code='configuration_invalid') if values['GITHUB_APP_ID'] else None
            installation = _id(values['GITHUB_INSTALLATION_ID'], environment=True,
                code='configuration_invalid') if values['GITHUB_INSTALLATION_ID'] else None
            material = values['GITHUB_APP_PRIVATE_KEY_PATH'] or values['GITHUB_APP_CLIENT_ID']
            if not material and not required:
                return None
            if app is None or not values['GITHUB_APP_PRIVATE_KEY_PATH']:
                raise GitHubAppError('not_configured')
            path = Path(values['GITHUB_APP_PRIVATE_KEY_PATH'])
            if not path.is_absolute():
                path = Path(root).absolute() / path
            return cls(app, Path(os.path.abspath(path)), values['GITHUB_APP_CLIENT_ID'] or None, installation)
        except GitHubAppError:
            raise
        except (AttributeError, TypeError, ValueError, OSError):
            raise GitHubAppError('configuration_invalid') from None

    from_env = load


@dataclass(frozen=True)
class GitHubAppCredential:
    token: str = field(repr=False)
    expires_at: datetime
    repository: str
    repository_id: int
    installation_id: int
    app_id: int
    purpose: str
    permissions: tuple[tuple[str, str], ...]

    @property
    def binding(self):
        return {'repository': self.repository, 'repository_id': self.repository_id,
            'installation_id': self.installation_id, 'app_id': self.app_id,
            'purpose': self.purpose, 'permissions': dict(self.permissions),
            'expires_at': self.expires_at.isoformat()}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise GitHubAppError('redirect_forbidden')


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError()
            result[key] = value
        return result
    def finite(_):
        raise ValueError()
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique, parse_constant=finite)
        pending, count = [(value, 0)], 0
        while pending:
            item, depth = pending.pop(); count += 1
            if count > 10000 or depth > 32:
                raise ValueError()
            if isinstance(item, dict):
                pending.extend((child, depth + 1) for child in item.values())
            elif isinstance(item, list):
                pending.extend((child, depth + 1) for child in item)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise GitHubAppError('invalid_response') from None


class GitHubAppAuth:
    """Memory-only, short-lived credentials for one trusted repository binding."""
    def __init__(self, config, *, transport=None, clock=None, jwt_encoder=None,
                 timeout=20, refresh_margin=60):
        if (not isinstance(config, GitHubAppConfig)
                or type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 30
                or type(refresh_margin) is not int or not 0 <= refresh_margin <= 300
                or transport is not None and not callable(transport)
                or jwt_encoder is not None and not callable(jwt_encoder)
                or clock is not None and not callable(clock)):
            raise GitHubAppError('configuration_invalid')
        self.config, self.timeout, self.refresh_margin = config, timeout, refresh_margin
        self._transport = transport
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._encoder = jwt_encoder
        self._cache = {}
        self._lock = RLock()

    def _jwt(self):
        key = _private_key(self.config.private_key_path)
        now = int(_utc(self._clock).timestamp())
        claims = {'iat': now - 60, 'exp': now + 540, 'iss': self.config.client_id or str(self.config.app_id)}
        try:
            encode = self._encoder
            if encode is None:
                try:
                    import jwt
                except ImportError:
                    raise GitHubAppError('jwt_dependency_missing') from None
                encode = jwt.encode
            value = encode(claims, key, algorithm='RS256')
            if not isinstance(value, str) or not _HEADER_TOKEN.fullmatch(value):
                raise ValueError()
            return value
        except GitHubAppError:
            raise
        except Exception:
            raise GitHubAppError('jwt_signing_failed') from None

    def _request(self, method, path, token, *, body=None, expected_status=200):
        route = (isinstance(path, str) and ((method == 'GET' and re.fullmatch(r'/repos/[A-Za-z0-9-]+/[A-Za-z0-9_.-]+(?:/installation)?', path)
                and path.split('/')[3] not in {'.', '..'})
            or (method == 'POST' and re.fullmatch(r'/app/installations/[1-9][0-9]*/access_tokens', path))))
        if not route or not isinstance(token, str) or not _HEADER_TOKEN.fullmatch(token):
            raise GitHubAppError('invalid_binding')
        url = API + path
        data = json.dumps(body, separators=(',', ':'), allow_nan=False).encode('utf-8') if body is not None else None
        request = Request(url, data, {'Authorization': 'Bearer ' + token,
            'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2026-03-10',
            'Content-Type': 'application/json', 'User-Agent': 'IssueIntel-GitHub-App'}, method=method)
        try:
            opener = self._transport or build_opener(_NoRedirect()).open
            with opener(request, timeout=self.timeout) as response:
                status = getattr(response, 'status', None)
                if type(status) is not int or status != expected_status:
                    if type(status) is int and 300 <= status <= 399:
                        raise GitHubAppError('redirect_forbidden', http_status=status)
                    raise GitHubAppError('unexpected_status', http_status=status)
                if not callable(getattr(response, 'geturl', None)) or response.geturl() != url:
                    raise GitHubAppError('redirect_forbidden')
                length = getattr(response, 'headers', {}).get('Content-Length')
                if length is not None and (not re.fullmatch(r'[0-9]{1,12}', str(length))
                                            or int(length) > MAX_RESPONSE_BYTES):
                    raise GitHubAppError('response_too_large')
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if not isinstance(raw, bytes):
                    raise GitHubAppError('invalid_response')
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise GitHubAppError('response_too_large')
            return _json(raw)
        except GitHubAppError:
            raise
        except HTTPError as error:
            code = error.code
            error.close()
            if type(code) is int and 300 <= code <= 399:
                raise GitHubAppError('redirect_forbidden', http_status=code) from None
            raise GitHubAppError('request_failed', http_status=code) from None
        except Exception:
            raise GitHubAppError('request_failed') from None

    def _grant(self, repo, installation, purpose, jwt):
        value = self._request('GET', f'/repos/{repo}/installation', jwt)
        if (type(value.get('id')) is not int or value['id'] != installation
                or type(value.get('app_id')) is not int or value['app_id'] != self.config.app_id
                or self.config.client_id is not None and value.get('client_id', self.config.client_id) != self.config.client_id):
            raise GitHubAppError('installation_mismatch')
        if 'suspended_at' not in value or value['suspended_at'] is not None:
            raise GitHubAppError('installation_suspended')
        permissions = value.get('permissions')
        required = _READ if purpose == 'read' else _COMMENT
        ranks = {'read': 1, 'write': 2, 'admin': 3}
        if (not isinstance(permissions, dict)
                or any(not isinstance(key, str) or not isinstance(level, str) or level not in ranks
                       for key, level in permissions.items())
                or any(ranks.get(permissions.get(key), 0) < ranks[level] for key, level in required.items())):
            raise GitHubAppError('installation_permissions')

    @staticmethod
    def _public_repo(value, repo, repo_id, *, code):
        if (not isinstance(value, dict) or type(value.get('id')) is not int or value['id'] != repo_id
                or not isinstance(value.get('full_name'), str) or value['full_name'].lower() != repo):
            raise GitHubAppError(code)
        if value.get('private') is not False:
            raise GitHubAppError('repository_not_public')

    def authorize(self, repo, repo_id, installation_id, purpose='read', fresh_grant=False):
        repo = _repository(repo)
        repo_id, installation_id = _id(repo_id), _id(installation_id)
        if not isinstance(purpose, str) or purpose not in {'read', 'comment'} or type(fresh_grant) is not bool:
            raise GitHubAppError('invalid_binding')
        if self.config.installation_id is not None and installation_id != self.config.installation_id:
            raise GitHubAppError('installation_mismatch')
        key = repo, repo_id, installation_id, purpose
        with self._lock:
            now = _utc(self._clock)
            cached = self._cache.get(key)
            if (purpose == 'read' and not fresh_grant and cached is not None
                    and cached.expires_at > now + timedelta(seconds=self.refresh_margin)):
                return cached
            self._cache.pop(key, None)
            try:
                jwt = self._jwt()
                self._grant(repo, installation_id, purpose, jwt)
                required = dict(_READ if purpose == 'read' else _COMMENT)
                value = self._request('POST', f'/app/installations/{installation_id}/access_tokens', jwt,
                    body={'repository_ids': [repo_id], 'permissions': required}, expected_status=201)
                token = value.get('token')
                if not isinstance(token, str) or not _HEADER_TOKEN.fullmatch(token):
                    raise GitHubAppError('token_invalid')
                permissions = value.get('permissions')
                allowed = {**required, 'metadata': 'read'}
                if (not isinstance(permissions, dict) or any(permissions.get(name) != level for name, level in required.items())
                        or any(name not in allowed or level != allowed[name] for name, level in permissions.items())):
                    raise GitHubAppError('token_permissions')
                # The request itself scopes the token to this exact global ID.
                # GitHub may omit the optional repository list from its mint
                # response. Validate it when present and independently verify
                # the target identity/public visibility with the minted token.
                if value.get('repository_selection', 'selected') != 'selected':
                    raise GitHubAppError('token_repository')
                if 'repositories' in value:
                    repositories = value['repositories']
                    if not isinstance(repositories, list) or len(repositories) != 1:
                        raise GitHubAppError('token_repository')
                    self._public_repo(repositories[0], repo, repo_id, code='token_repository')
                try:
                    expiry = datetime.fromisoformat(value['expires_at'])
                    current = _utc(self._clock)
                    if (expiry.tzinfo is None or expiry.utcoffset() != timedelta(0)
                            or not current < expiry <= current + timedelta(hours=1)):
                        raise ValueError()
                    expiry = expiry.astimezone(timezone.utc)
                except (KeyError, ValueError, TypeError, OverflowError):
                    raise GitHubAppError('token_expiry') from None
                public = self._request('GET', f'/repos/{repo}', token)
                self._public_repo(public, repo, repo_id, code='repository_mismatch')
                if expiry <= _utc(self._clock):
                    raise GitHubAppError('token_expiry')
                credential = GitHubAppCredential(token, expiry, repo, repo_id,
                    installation_id, self.config.app_id, purpose, tuple(sorted(permissions.items())))
                if purpose == 'read':
                    current = _utc(self._clock)
                    self._cache = {binding: saved for binding, saved in self._cache.items()
                                   if saved.expires_at > current}
                    if len(self._cache) >= 128:
                        self._cache.pop(next(iter(self._cache)))
                    self._cache[key] = credential
                return credential
            except BaseException:
                # A fresh comment authorization can discover revocation or
                # suspension while a previous read credential is still cached.
                # Never retain that stale grant in another permission scope.
                self._cache = {binding: saved for binding, saved in self._cache.items()
                               if binding[:3] != key[:3]}
                raise

    def token_provider(self, repo, repo_id, installation_id, purpose='read'):
        """A lazy callable for paginated readers; it never exposes PAT fallback."""
        repo = _repository(repo)
        repo_id, installation_id = _id(repo_id), _id(installation_id)
        if not isinstance(purpose, str) or purpose not in {'read', 'comment'}:
            raise GitHubAppError('invalid_binding')
        return lambda: self.authorize(repo, repo_id, installation_id, purpose=purpose).token

    def invalidate(self, *, installation_id=None, repository=None):
        if installation_id is not None:
            installation_id = _id(installation_id)
        if repository is not None:
            repository = _repository(repository)
        with self._lock:
            self._cache = {key: value for key, value in self._cache.items()
                if not ((installation_id is None or key[2] == installation_id)
                        and (repository is None or key[0] == repository))}
