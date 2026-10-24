"""Transport diagnosis must preserve bounds and never persist exception text."""
from http.client import IncompleteRead, RemoteDisconnected
from io import BytesIO
import json
import socket
import ssl
from urllib.error import HTTPError, URLError

import pytest

from issueintel.investigation.providers import GroqProvider, ProviderError, _NoCredentialRedirect, failure_metadata, output_rejected
from test_runtime import configured


@pytest.mark.parametrize('error,expected,reason', [
    (URLError(socket.gaierror(-2, 'synthetic-secret-dns')), 'URLError', 'gaierror'),
    (URLError(TimeoutError('synthetic-secret-timeout')), 'URLError', 'TimeoutError'),
    (ConnectionResetError(10054, 'synthetic-secret-reset'), 'ConnectionResetError', 'ConnectionResetError'),
    (RemoteDisconnected('synthetic-secret-disconnect'), 'RemoteDisconnected', 'RemoteDisconnected'),
    (RuntimeError('synthetic-secret-unexpected'), 'RuntimeError', 'RuntimeError'),
])
def test_connect_failures_keep_one_owned_bound_and_safe_category(tmp_path, monkeypatch, error, expected, reason):
    monkeypatch.setenv('GROQ_API_KEY', 'synthetic-secret-key')
    attempts, receipts = [], []
    def fail(request, timeout):
        attempts.append(1)
        raise error
    config = configured(tmp_path)
    provider = GroqProvider(config, transport=fail, on_receipt=receipts.append)
    with pytest.raises(ProviderError) as caught:
        provider.complete_json([{'role': 'user', 'content': 'synthetic-secret-source'}], 64)
    metadata = failure_metadata(caught.value)
    assert metadata['transport_error_type'] == expected
    assert metadata['transport_reason_type'] == reason
    assert metadata['failure_phase'] == 'request'
    assert len(attempts) == 1 and len(receipts) == 2
    assert receipts[0]['status'] == 'reserved' and receipts[-1]['status'] == 'failed_bound'
    assert receipts[0]['reservation_id'] == receipts[-1]['reservation_id']
    assert config.ledger().receipt(provider.last_reservation) == receipts[-1]
    assert output_rejected(caught.value) is False
    assert 'synthetic-secret' not in str(caught.value) + json.dumps(metadata)


def test_certificate_failure_has_numeric_verify_code_without_certificate_details(tmp_path, monkeypatch):
    monkeypatch.setenv('GROQ_API_KEY', 'synthetic-secret-key')
    error = ssl.SSLCertVerificationError(1, 'synthetic-secret-certificate')
    error.verify_code = 20
    def fail(request, timeout):
        raise URLError(error)
    provider = GroqProvider(configured(tmp_path), transport=fail)
    with pytest.raises(ProviderError) as caught:
        provider.complete_json([{'role': 'user', 'content': 'Synthetic JSON'}], 64)
    metadata = failure_metadata(caught.value)
    assert metadata['transport_reason_type'] == 'SSLCertVerificationError'
    assert metadata['ssl_verify_code'] == 20
    assert 'synthetic-secret' not in str(caught.value) + json.dumps(metadata)


class Response(BytesIO):
    status = 200
    headers = {'content-type': 'text/html; private=synthetic-secret-header'}


@pytest.mark.parametrize('body,expected', [
    (b'<html>synthetic-secret-body</html>', 'JSONDecodeError'),
    (b'', 'JSONDecodeError'),
    (b'[]', 'ValueError'),
    (b'null', 'ValueError'),
])
def test_response_envelope_failure_preserves_read_vs_json_phase(tmp_path, monkeypatch, body, expected):
    monkeypatch.setenv('GROQ_API_KEY', 'synthetic-secret-key')
    attempts = []
    def respond(request, timeout):
        attempts.append(1)
        return Response(body)
    provider = GroqProvider(configured(tmp_path), transport=respond)
    with pytest.raises(ProviderError) as caught:
        provider.complete_json([{'role': 'user', 'content': 'Synthetic JSON'}], 64)
    metadata = failure_metadata(caught.value)
    assert metadata['failure_phase'] == 'response_json'
    assert metadata['transport_error_type'] == expected
    assert metadata['response_status'] == 200
    assert metadata['response_content_type'] == 'html'
    assert metadata['response_byte_count'] == len(body)
    assert len(attempts) == 1 and not output_rejected(caught.value)
    assert provider.config.ledger().receipt(provider.last_reservation)['status'] == 'failed_bound'
    assert 'synthetic-secret' not in json.dumps(metadata) + str(caught.value)


def test_mid_response_failure_does_not_claim_a_complete_body(tmp_path, monkeypatch):
    monkeypatch.setenv('GROQ_API_KEY', 'synthetic-secret-key')
    class Truncated(Response):
        def read(self, maximum):
            raise IncompleteRead(b'synthetic-secret-partial', 90)
    provider = GroqProvider(configured(tmp_path), transport=lambda *a, **kw: Truncated())
    with pytest.raises(ProviderError) as caught:
        provider.complete_json([{'role': 'user', 'content': 'Synthetic JSON'}], 64)
    metadata = failure_metadata(caught.value)
    assert metadata['failure_phase'] == 'response_read'
    assert metadata['transport_error_type'] == 'IncompleteRead'
    assert 'response_byte_count' not in metadata
    assert 'synthetic-secret' not in json.dumps(metadata) + str(caught.value)


def test_unknown_http_error_code_and_exception_names_cannot_echo_source_or_keys(tmp_path, monkeypatch):
    monkeypatch.setenv('GROQ_API_KEY', 'synthetic-secret-key')
    def reject(request, timeout):
        raise HTTPError(request.full_url, 401, 'synthetic-secret-message', {},
            BytesIO(json.dumps({'error': {'code': 'synthetic_secret_key', 'message': 'synthetic-secret-body'}}).encode()))
    provider = GroqProvider(configured(tmp_path), transport=reject)
    with pytest.raises(ProviderError) as caught:
        provider.complete_json([{'role': 'user', 'content': 'Synthetic JSON'}], 64)
    assert failure_metadata(caught.value)['http_status'] == 401
    assert failure_metadata(caught.value)['provider_code'] == 'unknown'
    assert 'synthetic' not in str(caught.value)
    private_exception = type('synthetic_secret_exception_name', (RuntimeError,), {})
    def fail(request, timeout):
        raise private_exception('synthetic-secret-message')
    provider = GroqProvider(configured(tmp_path), transport=fail)
    with pytest.raises(ProviderError) as caught:
        provider.complete_json([{'role': 'user', 'content': 'Synthetic JSON'}], 64)
    assert failure_metadata(caught.value)['transport_error_type'] == 'other'
    assert 'synthetic' not in str(caught.value) + json.dumps(failure_metadata(caught.value))


def test_manually_supplied_diagnostics_still_pass_explicit_allowlists():
    metadata = failure_metadata(ProviderError('unused', diagnostics={
        'failure_phase': 'synthetic-secret-phase', 'transport_error_type': 'synthetic-secret-type',
        'transport_reason_type': 'synthetic-secret-reason', 'response_content_type': 'synthetic-secret-media',
        'response_byte_count': -1, 'response_status': True, 'ssl_verify_code': float('nan'),
        'transport_errno': 'synthetic-secret-errno', 'unrecognized': 'synthetic-secret-extra'}))
    assert 'synthetic-secret' not in json.dumps(metadata)
    assert not {'failure_phase', 'response_byte_count', 'response_status', 'ssl_verify_code', 'transport_errno'} & metadata.keys()


@pytest.mark.parametrize('kind', ['redirect', 'size'])
def test_local_transport_rejections_keep_fixed_codes_without_becoming_output_repair(tmp_path, monkeypatch, kind):
    monkeypatch.setenv('GROQ_API_KEY', 'synthetic-secret-key')
    def transport(request, timeout):
        if kind == 'redirect':
            return _NoCredentialRedirect().redirect_request(None, None, 302,
                'synthetic-secret-redirect', {}, 'https://synthetic-secret.example/')
        return Response(b'x' * 4_000_001)
    provider = GroqProvider(configured(tmp_path), transport=transport)
    with pytest.raises(ProviderError) as caught:
        provider.complete_json([{'role': 'user', 'content': 'Synthetic JSON'}], 64)
    metadata = failure_metadata(caught.value)
    assert metadata['provider_code'] == ('redirect_forbidden' if kind == 'redirect' else 'response_size_limit')
    assert metadata['failure_phase'] == ('request' if kind == 'redirect' else 'response_read')
    assert not output_rejected(caught.value)
    assert 'synthetic-secret' not in str(caught.value) + json.dumps(metadata)
