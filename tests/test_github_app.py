"""GitHub App auth with synthetic transports; no account or key access."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest

from issueintel.integrations import github_app as app


NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
PEM = b'-----BEGIN PRIVATE KEY-----\nSYNTHETIC_PRIVATE_MATERIAL\n-----END PRIVATE KEY-----\n'


class Response(BytesIO):
    def __init__(self, request, value, *, status=200, raw=None, url=None, headers=None):
        super().__init__(json.dumps(value).encode() if raw is None else raw)
        self.status = status
        self.url = request.full_url if url is None else url
        self.headers = {'Content-Type': 'application/json', **(headers or {})}

    def geturl(self):
        return self.url


@pytest.fixture
def scenario(tmp_path):
    key = tmp_path / 'private-key.pem'; key.write_bytes(PEM)
    config = app.GitHubAppConfig(123, key, 'Iv1.synthetic', 77)
    state = SimpleNamespace(now=NOW, requests=[], encodings=[], grant_changes={},
        mint_changes={}, public_changes={}, response_changes={}, fault=None)
    def encoder(claims, key_bytes, *, algorithm):
        state.encodings.append((deepcopy(claims), key_bytes, algorithm))
        return 'synthetic.app.jwt'
    def transport(request, *, timeout):
        assert timeout == 20
        state.requests.append(request)
        if state.fault:
            raise state.fault
        if request.method == 'GET' and request.full_url.endswith('/installation'):
            value = {'id': 77, 'app_id': 123, 'client_id': 'Iv1.synthetic',
                'suspended_at': None, 'permissions': {'contents': 'read', 'issues': 'write',
                                                   'pull_requests': 'read', 'metadata': 'read'}}
            value.update(state.grant_changes)
            return Response(request, value, **state.response_changes)
        if request.method == 'POST':
            assert request.full_url == 'https://api.github.com/app/installations/77/access_tokens'
            body = json.loads(request.data)
            value = {'token': 'ghs_123_' + ('header.payload.signature_' * 100) + str(len(state.requests)),
                'expires_at': (state.now + timedelta(hours=1)).isoformat(),
                'permissions': {**body['permissions'], 'metadata': 'read'},
                'repository_selection': 'selected',
                'repositories': [{'id': 999, 'full_name': 'Acme/Widget', 'private': False}]}
            value.update(state.mint_changes)
            return Response(request, value, status=201)
        assert request.method == 'GET' and request.full_url == 'https://api.github.com/repos/acme/widget'
        value = {'id': 999, 'full_name': 'Acme/Widget', 'private': False}
        value.update(state.public_changes)
        return Response(request, value)
    state.auth = app.GitHubAppAuth(config, transport=transport, clock=lambda: state.now, jwt_encoder=encoder)
    state.key = key
    return state


def authorize(state, **kwargs):
    return state.auth.authorize('Acme/Widget', 999, 77, **kwargs)


def test_read_token_is_repo_and_permission_scoped_with_safe_public_binding(scenario):
    value = authorize(scenario)
    assert len(scenario.requests) == 3
    mint = scenario.requests[1]
    assert json.loads(mint.data) == {'repository_ids': [999],
        'permissions': {'contents': 'read', 'issues': 'read', 'pull_requests': 'read'}}
    assert scenario.requests[0].get_header('Authorization') == 'Bearer synthetic.app.jwt'
    assert mint.get_header('Authorization') == 'Bearer synthetic.app.jwt'
    assert scenario.requests[2].get_header('Authorization') == 'Bearer ' + value.token
    assert len(value.token) > 1000
    assert value.binding == {'repository': 'acme/widget', 'repository_id': 999,
        'installation_id': 77, 'app_id': 123, 'purpose': 'read',
        'permissions': {'contents': 'read', 'issues': 'read', 'metadata': 'read', 'pull_requests': 'read'},
        'expires_at': (NOW + timedelta(hours=1)).isoformat()}
    assert value.token not in repr(value) and value.token not in repr(value.binding)
    assert str(scenario.key) not in repr(scenario.auth.config)
    assert sorted(path.name for path in scenario.key.parent.iterdir()) == ['private-key.pem']


def test_jwt_claims_use_skew_margin_and_client_id(scenario):
    authorize(scenario)
    claims, key, algorithm = scenario.encodings[0]
    assert claims == {'iat': int(NOW.timestamp()) - 60, 'exp': int(NOW.timestamp()) + 540,
                      'iss': 'Iv1.synthetic'}
    assert key == PEM and algorithm == 'RS256'


def test_app_id_is_valid_jwt_issuer_fallback(scenario):
    scenario.auth.config = app.GitHubAppConfig(123, scenario.key, installation_id=77)
    authorize(scenario)
    assert scenario.encodings[0][0]['iss'] == '123'


def test_read_cache_refreshes_before_expiry_and_provider_is_lazy(scenario, monkeypatch):
    monkeypatch.setenv('GITHUB_TOKEN', 'PAT_MUST_NEVER_BE_USED')
    provider = scenario.auth.token_provider('Acme/Widget', 999, 77)
    assert scenario.requests == scenario.encodings == []
    first = provider()
    assert provider() == first and len(scenario.requests) == 3
    scenario.now += timedelta(minutes=59)
    second = provider()
    assert second != first and len(scenario.requests) == 6
    assert all('PAT_MUST_NEVER_BE_USED' not in request.get_header('Authorization') for request in scenario.requests)


def test_fresh_grant_read_always_checks_remote_again_and_evicts_failure(scenario):
    authorize(scenario)
    authorize(scenario, fresh_grant=True)
    assert len(scenario.requests) == 6
    scenario.grant_changes['suspended_at'] = '2026-10-26T12:00:00Z'
    with pytest.raises(app.GitHubAppError, match='suspended'):
        authorize(scenario, fresh_grant=True)
    before = len(scenario.requests)
    with pytest.raises(app.GitHubAppError):
        authorize(scenario)
    assert len(scenario.requests) == before + 1


@pytest.mark.parametrize('failure', ['suspended', 'removed', 'signing'])
def test_failed_comment_authorization_invalidates_cached_read_grant(scenario, failure):
    authorize(scenario)
    if failure == 'suspended':
        scenario.grant_changes['suspended_at'] = NOW.isoformat()
    elif failure == 'removed':
        scenario.fault = HTTPError('https://api.github.com/repos/acme/widget/installation',
                                   404, 'Not Found', {}, BytesIO())
    else:
        scenario.key.unlink()
    with pytest.raises(app.GitHubAppError):
        authorize(scenario, purpose='comment')
    assert scenario.auth._cache == {}
    # A cached token must never bypass the failed fresh grant/signing check.
    with pytest.raises(app.GitHubAppError):
        authorize(scenario)


@pytest.mark.parametrize('omit', [('repositories',), ('repositories', 'repository_selection')])
def test_optional_mint_repository_fields_can_be_omitted_with_exact_request_scope(scenario, omit):
    original = scenario.auth._transport
    def compact_response(request, *, timeout):
        response = original(request, timeout=timeout)
        if request.method != 'POST':
            return response
        value = json.loads(response.getvalue())
        for field in omit:
            value.pop(field)
        return Response(request, value, status=201)
    scenario.auth._transport = compact_response
    credential = authorize(scenario)
    assert credential.repository_id == 999
    assert json.loads(scenario.requests[1].data)['repository_ids'] == [999]
    assert scenario.requests[2].full_url == 'https://api.github.com/repos/acme/widget'


def test_comment_tokens_have_only_issues_write_and_always_fresh(scenario):
    authorize(scenario)  # A cached read token is never elevated or reused.
    first = authorize(scenario, purpose='comment')
    second = authorize(scenario, purpose='comment')
    assert len(scenario.requests) == 9 and first.token != second.token
    assert json.loads(scenario.requests[4].data) == {'repository_ids': [999], 'permissions': {'issues': 'write'}}
    assert dict(first.permissions) == {'issues': 'write', 'metadata': 'read'}


def test_invalidate_installation_or_repository_forces_refresh(scenario):
    authorize(scenario)
    scenario.auth.invalidate(installation_id=88)  # Another installation cannot evict this binding.
    authorize(scenario)
    assert len(scenario.requests) == 3
    scenario.auth.invalidate(repository='ACME/WIDGET')
    authorize(scenario)
    assert len(scenario.requests) == 6
    scenario.auth.invalidate(installation_id=77)
    authorize(scenario)
    assert len(scenario.requests) == 9
    scenario.auth.invalidate()
    authorize(scenario)
    assert len(scenario.requests) == 12


@pytest.mark.parametrize('change,code', [
    ({'id': 78}, 'installation_mismatch'), ({'id': True}, 'installation_mismatch'),
    ({'app_id': 124}, 'installation_mismatch'), ({'app_id': True}, 'installation_mismatch'),
    ({'client_id': 'wrong-client'}, 'installation_mismatch'),
    ({'suspended_at': 'yesterday'}, 'installation_suspended'),
    ({'permissions': {}}, 'installation_permissions'),
    ({'permissions': {'contents': 'read', 'issues': 'read'}}, 'installation_permissions'),
    ({'permissions': {'contents': 'read', 'issues': 'read', 'pull_requests': []}}, 'installation_permissions'),
])
def test_untrusted_remote_installation_never_mints(scenario, change, code):
    scenario.grant_changes.update(change)
    with pytest.raises(app.GitHubAppError) as error:
        authorize(scenario)
    assert error.value.code == code and len(scenario.requests) == 1


def test_unknown_installation_active_status_denies_without_mint(scenario):
    original = scenario.auth._transport
    def changed(request, *, timeout):
        response = original(request, timeout=timeout)
        value = json.loads(response.getvalue()); value.pop('suspended_at')
        return Response(request, value)
    scenario.auth._transport = changed
    with pytest.raises(app.GitHubAppError) as error:
        authorize(scenario)
    assert error.value.code == 'installation_suspended' and len(scenario.requests) == 1


@pytest.mark.parametrize('change,code', [
    ({'permissions': {'contents': 'write', 'issues': 'read', 'pull_requests': 'read'}}, 'token_permissions'),
    ({'permissions': {'contents': 'read', 'issues': 'read', 'pull_requests': 'read', 'checks': 'write'}}, 'token_permissions'),
    ({'permissions': {'contents': 'read', 'issues': 'read', 'pull_requests': 'read', 'members': 'read'}}, 'token_permissions'),
    ({'permissions': {'contents': 'read', 'issues': 'read', 'pull_requests': 'read', 'metadata': 'write'}}, 'token_permissions'),
    ({'permissions': {'contents': 'read', 'issues': 'read'}}, 'token_permissions'),
    ({'repositories': []}, 'token_repository'),
    ({'repositories': [{'id': 998, 'full_name': 'acme/widget', 'private': False}]}, 'token_repository'),
    ({'repositories': [{'id': 999, 'full_name': 'acme/another', 'private': False}]}, 'token_repository'),
    ({'repositories': [{'id': 999, 'full_name': 'acme/widget', 'private': True}]}, 'repository_not_public'),
    ({'repositories': [{'id': 999, 'full_name': 'acme/widget', 'private': False}] * 2}, 'token_repository'),
    ({'repository_selection': 'all'}, 'token_repository'),
    ({'token': 'secret\r\nInjected: header'}, 'token_invalid'),
    ({'token': ''}, 'token_invalid'), ({'token': 'x' * 16385}, 'token_invalid'),
])
def test_minted_scope_is_exact_and_target_not_probed_on_bad_token(scenario, change, code):
    scenario.mint_changes.update(change)
    with pytest.raises(app.GitHubAppError) as error:
        authorize(scenario)
    assert error.value.code == code and len(scenario.requests) == 2


@pytest.mark.parametrize('expiry', [None, 'invalid', '2026-10-26T13:00:00',
    '2026-10-26T12:00:00Z', '2026-10-26T11:59:59Z', '2026-10-26T13:00:01Z',
    '2026-10-26T14:00:00+01:00'])
def test_invalid_or_long_lived_token_expiry_is_denied(scenario, expiry):
    scenario.mint_changes['expires_at'] = expiry
    with pytest.raises(app.GitHubAppError) as error:
        authorize(scenario)
    assert error.value.code == 'token_expiry' and len(scenario.requests) == 2


@pytest.mark.parametrize('change,code', [
    ({'id': 998}, 'repository_mismatch'), ({'id': True}, 'repository_mismatch'),
    ({'full_name': 'acme/another'}, 'repository_mismatch'),
    ({'private': True}, 'repository_not_public'), ({'private': None}, 'repository_not_public'),
])
def test_final_public_repository_identity_is_independently_verified(scenario, change, code):
    scenario.public_changes.update(change)
    with pytest.raises(app.GitHubAppError) as error:
        authorize(scenario)
    assert error.value.code == code and len(scenario.requests) == 3
    assert scenario.auth._cache == {}


@pytest.mark.parametrize('arguments', [
    ('https://github.com/acme/widget', 999, 77), ('acme/..', 999, 77), ('acme/widget?x', 999, 77),
    ('acme/widget', True, 77), ('acme/widget', 0, 77), ('acme/widget', 999, True),
    ('acme/widget', 999, 78), ('acme/widget', '999', 77),
])
def test_invalid_local_binding_never_reads_key_or_requests(scenario, arguments):
    with pytest.raises(app.GitHubAppError):
        scenario.auth.authorize(*arguments)
    assert scenario.encodings == scenario.requests == []


@pytest.mark.parametrize('purpose', ['admin', '', None, [], True])
def test_unknown_purpose_never_requests(scenario, purpose):
    with pytest.raises(app.GitHubAppError):
        authorize(scenario, purpose=purpose)
    assert scenario.requests == []


def test_no_signing_material_defaults_to_none_but_required_app_fails(tmp_path):
    assert app.GitHubAppConfig.load(tmp_path, {}) is None
    assert app.GitHubAppConfig.from_env(tmp_path, {'GITHUB_APP_ID': '123'}) is None
    with pytest.raises(app.GitHubAppError) as error:
        app.GitHubAppConfig.load(tmp_path, {'GITHUB_APP_ID': '123'}, required=True)
    assert error.value.code == 'not_configured'


def test_config_load_is_lazy_and_resolves_only_explicit_path(tmp_path):
    config = app.GitHubAppConfig.load(tmp_path, {'GITHUB_APP_ID': '123',
        'GITHUB_APP_CLIENT_ID': 'Iv1.synthetic', 'GITHUB_APP_PRIVATE_KEY_PATH': 'missing.pem',
        'GITHUB_INSTALLATION_ID': '77'})
    assert config.private_key_path == tmp_path / 'missing.pem'
    assert config.app_id == 123 and config.installation_id == 77
    assert not config.private_key_path.exists()


@pytest.mark.parametrize('values', [
    {'GITHUB_APP_ID': '0'}, {'GITHUB_APP_ID': 'secret-path-and-key'},
    {'GITHUB_INSTALLATION_ID': 'true'}, {'GITHUB_APP_ID': True},
    {'GITHUB_APP_CLIENT_ID': 'Iv1.synthetic'},
    {'GITHUB_APP_PRIVATE_KEY_PATH': 'key.pem'},
    {'GITHUB_APP_ID': '123', 'GITHUB_APP_PRIVATE_KEY_PATH': 'key.pem', 'GITHUB_APP_CLIENT_ID': 'secret\nheader'},
])
def test_invalid_or_partial_configuration_has_sanitized_errors(tmp_path, values):
    with pytest.raises(app.GitHubAppError) as error:
        app.GitHubAppConfig.load(tmp_path, values)
    assert 'secret-path-and-key' not in str(error.value)
    assert 'secret\nheader' not in str(error.value)


@pytest.mark.parametrize('kind', ['missing', 'directory', 'oversized', 'empty', 'wrong-header', 'encrypted'])
def test_key_shape_rejected_before_jwt_or_network(scenario, kind):
    scenario.key.unlink()
    if kind == 'directory': scenario.key.mkdir()
    elif kind == 'oversized': scenario.key.write_bytes(b'x' * (app.MAX_KEY_BYTES + 1))
    elif kind == 'empty': scenario.key.write_bytes(b'')
    elif kind == 'wrong-header': scenario.key.write_bytes(b'SYNTHETIC_PRIVATE_MATERIAL')
    elif kind == 'encrypted': scenario.key.write_bytes(b'-----BEGIN ENCRYPTED PRIVATE KEY-----\nsecret\n-----END ENCRYPTED PRIVATE KEY-----')
    with pytest.raises(app.GitHubAppError) as error:
        authorize(scenario)
    assert scenario.encodings == scenario.requests == []
    assert str(scenario.key) not in str(error.value) and 'SYNTHETIC_PRIVATE_MATERIAL' not in str(error.value)


def test_hardlinked_key_is_rejected(scenario):
    alias = scenario.key.with_name('alias.pem'); os.link(scenario.key, alias)
    with pytest.raises(app.GitHubAppError) as error:
        authorize(scenario)
    assert error.value.code == 'unsafe_private_key' and scenario.requests == []


def test_symlink_key_is_rejected(scenario):
    alias = scenario.key.with_name('linked.pem')
    try:
        alias.symlink_to(scenario.key)
    except OSError:
        pytest.skip('Creating symlinks requires local privileges')
    scenario.auth.config = app.GitHubAppConfig(123, alias)
    with pytest.raises(app.GitHubAppError) as error:
        authorize(scenario)
    assert error.value.code == 'unsafe_private_key'


def test_reparse_ancestor_is_rejected_before_key_open(scenario, monkeypatch):
    native = Path.lstat
    parent = scenario.key.parent
    def marked(path):
        value = native(path)
        if path == parent:
            return SimpleNamespace(st_mode=value.st_mode, st_nlink=value.st_nlink,
                                   st_file_attributes=0x400)
        return value
    monkeypatch.setattr(Path, 'lstat', marked)
    with pytest.raises(app.GitHubAppError) as error:
        authorize(scenario)
    assert error.value.code == 'unsafe_private_key' and scenario.requests == []


def test_signing_and_transport_errors_never_expose_secret_values(scenario):
    def failed(*a, **k):
        raise RuntimeError('SYNTHETIC_PRIVATE_MATERIAL secret-token-' + str(scenario.key))
    scenario.auth._encoder = failed
    with pytest.raises(app.GitHubAppError) as error:
        authorize(scenario)
    assert error.value.code == 'jwt_signing_failed'
    assert 'SYNTHETIC_PRIVATE_MATERIAL' not in str(error.value) and str(scenario.key) not in str(error.value)
    assert error.value.__suppress_context__ is True


def test_transport_has_no_automatic_retry(scenario):
    scenario.fault = URLError('secret-token-should-not-appear')
    with pytest.raises(app.GitHubAppError) as error:
        authorize(scenario)
    assert error.value.code == 'request_failed'
    assert 'secret-token' not in str(error.value) and len(scenario.requests) == 1


@pytest.mark.parametrize('change,code', [
    ({'status': 301}, 'redirect_forbidden'), ({'url': 'https://evil.example/steal'}, 'redirect_forbidden'),
    ({'headers': {'Content-Length': '1000001'}}, 'response_too_large'),
    ({'raw': b'x' * 1000001}, 'response_too_large'),
    ({'raw': b'{"id":77,"id":78}'}, 'invalid_response'),
    ({'raw': b'{"id":NaN}'}, 'invalid_response'),
    ({'raw': b'not json: secret-token'}, 'invalid_response'),
    ({'status': 500}, 'unexpected_status'),
])
def test_response_bounds_redirects_and_json_are_fail_closed(scenario, change, code):
    scenario.response_changes.update(change)
    with pytest.raises(app.GitHubAppError) as error:
        authorize(scenario)
    assert error.value.code == code and len(scenario.requests) == 1
    assert 'secret-token' not in str(error.value)


@pytest.mark.parametrize('path', ['/user', '//evil.example/steal', 'https://evil.example/',
    '/repos/acme/../installation', '/repos/acme/widget/issues/1/comments'])
def test_auth_broker_only_accepts_its_three_fixed_api_routes(scenario, path):
    with pytest.raises(app.GitHubAppError):
        scenario.auth._request('GET', path, 'synthetic.jwt')
    assert scenario.requests == []


def test_default_redirect_handler_refuses_without_following():
    with pytest.raises(app.GitHubAppError) as error:
        app._NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.example/')
    assert error.value.code == 'redirect_forbidden'


def test_http_error_body_is_not_read_or_logged(scenario):
    response = BytesIO(b'secret-token private-key-path')
    scenario.fault = HTTPError('https://api.github.com/private', 401, 'secret-token', {}, response)
    with pytest.raises(app.GitHubAppError) as error:
        authorize(scenario)
    assert error.value.http_status == 401 and error.value.code == 'request_failed'
    assert 'secret-token' not in str(error.value) and response.closed


def test_missing_jwt_extra_has_a_safe_actionable_error(scenario, monkeypatch):
    import builtins
    original = builtins.__import__
    def missing(name, *args, **kwargs):
        if name == 'jwt': raise ImportError('secret-path')
        return original(name, *args, **kwargs)
    scenario.auth._encoder = None
    monkeypatch.setattr(builtins, '__import__', missing)
    with pytest.raises(app.GitHubAppError) as error:
        authorize(scenario)
    assert error.value.code == 'jwt_dependency_missing' and scenario.requests == []


def test_module_import_remains_stdlib_only_and_does_not_load_env_or_keys(tmp_path):
    source = str(Path(__file__).resolve().parents[1] / 'src')
    program = '''import os,sys,socket
sys.path.insert(0,sys.argv[1])
class Guard(dict):
 def get(self,key,*args):
  if key.startswith('GITHUB_APP_'): raise AssertionError('read signing environment during import')
  return super().get(key,*args)
os.environ=Guard(os.environ)
def deny(*a,**k): raise AssertionError('network during import')
socket.create_connection=deny
from issueintel.integrations import github_app
assert 'jwt' not in sys.modules and 'cryptography' not in sys.modules
print('stdlib-only-import')
'''
    result = subprocess.run([sys.executable, '-I', '-S', '-c', program, source],
        cwd=tmp_path, check=True, text=True, capture_output=True)
    assert result.stdout.strip() == 'stdlib-only-import'


def test_real_rs256_signature_and_claims(tmp_path):
    jwt = pytest.importorskip('jwt')
    rsa = pytest.importorskip('cryptography.hazmat.primitives.asymmetric.rsa')
    serialization = pytest.importorskip('cryptography.hazmat.primitives.serialization')
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path = tmp_path / 'generated-test-key.pem'
    path.write_bytes(private.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    auth = app.GitHubAppAuth(app.GitHubAppConfig(123, path, 'Iv1.test'), clock=lambda: NOW)
    encoded = auth._jwt()
    assert jwt.get_unverified_header(encoded)['alg'] == 'RS256'
    claims = jwt.decode(encoded, private.public_key(), algorithms=['RS256'],
        options={'verify_exp': False, 'verify_iat': False})
    assert claims == {'iat': int(NOW.timestamp()) - 60, 'exp': int(NOW.timestamp()) + 540, 'iss': 'Iv1.test'}
    assert path.read_bytes().startswith(b'-----BEGIN PRIVATE KEY-----')
