"""Loopback batch routes preserve service bindings and local-session guards."""
from contextlib import contextmanager
from http.client import HTTPConnection
import json
import threading

import pytest

from issueintel.interfaces.web import make_server


class BatchService:
    def __init__(self):
        self.calls = []

    def _call(self, action, *args):
        self.calls.append((action, args))
        return {'action': action, 'args': list(args)}

    def batch_history(self): return self._call('history')
    def batch(self, identifier): return self._call('read', identifier)
    def batch_preview(self, manifest): return self._call('preview', manifest)
    def batch_submit(self, request): return self._call('submit', request)
    def batch_action(self, identifier, action, request): return self._call(action, identifier, request)
    def batch_item_cancel(self, identifier, key, request): return self._call('item_cancel', identifier, key, request)


@contextmanager
def server(tmp_path):
    service = BatchService()
    httpd = make_server(tmp_path, port=0, workbench=service)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    try: yield service, httpd
    finally:
        httpd.shutdown()
        worker.join(timeout=5)
        httpd.server_close()


def request(httpd, method, route, body=None, *, authenticated=True, raw=None):
    connection = HTTPConnection('127.0.0.1', httpd.server_port, timeout=5)
    headers = {'Content-Type': 'application/json'}
    if authenticated: headers['X-IssueIntel-Token'] = httpd.session_token
    if raw is None and body is not None: raw = json.dumps(body).encode()
    try:
        connection.request(method, route, raw, headers)
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally: connection.close()


@pytest.mark.parametrize('method,route,body,action,args,status', [
    ('GET', '/api/batches', None, 'history', [], 200),
    ('GET', '/api/batches/'+'a'*24, None, 'read', ['a'*24], 200),
    ('POST', '/api/batches/preview', {'manifest': {'run_key': 'one'}}, 'preview', [{'run_key': 'one'}], 200),
    ('POST', '/api/batches', {'manifest': {}, 'preview': {}, 'confirmed': True, 'allow_api_calls': False},
     'submit', [{'manifest': {}, 'preview': {}, 'confirmed': True, 'allow_api_calls': False}], 202),
    *[('POST', '/api/batches/'+'a'*24+'/'+action, {'manifest_sha256': 'b'*64, 'preview_sha256': 'c'*64},
       action, ['a'*24, {'manifest_sha256': 'b'*64, 'preview_sha256': 'c'*64}], 200)
      for action in ('pause', 'continue', 'cancel')],
    ('POST', '/api/batches/'+'a'*24+'/items/first/cancel', {'manifest_sha256': 'b'*64},
     'item_cancel', ['a'*24, 'first', {'manifest_sha256': 'b'*64}], 200),
])
def test_batch_routes_delegate_exact_payload(tmp_path, method, route, body, action, args, status):
    with server(tmp_path) as (service, httpd):
        code, value = request(httpd, method, route, body)
        assert code == status and value == {'action': action, 'args': args}
        assert service.calls == [(action, tuple(args))]


@pytest.mark.parametrize('method,route,body', [
    ('GET', '/api/batches', None),
    ('POST', '/api/batches/preview', {'manifest': {}}),
    ('POST', '/api/batches', {'confirmed': True}),
    ('POST', '/api/batches/'+'a'*24+'/cancel', {}),
])
def test_batch_requires_local_session_before_service(tmp_path, method, route, body):
    with server(tmp_path) as (service, httpd):
        code, _ = request(httpd, method, route, body, authenticated=False)
        assert code == 403 and not service.calls


@pytest.mark.parametrize('raw', [
    b'{"manifest":{},"manifest":{}}',
    b'{"manifest":{"run_key":"a","run_key":"b"}}',
    b'{"manifest":{},"unknown":true}',
    b'{"manifest":{"run_key":NaN}}',
    b'[]',
])
def test_ambiguous_preview_request_is_rejected_before_capture(tmp_path, raw):
    with server(tmp_path) as (service, httpd):
        code, _ = request(httpd, 'POST', '/api/batches/preview', raw=raw)
        assert code == 400 and not service.calls


def test_acceptance_envelope_can_include_large_frozen_preview(tmp_path):
    # Only the service decides validity; HTTP must not truncate an otherwise
    # bounded preview merely because it exceeds the older single-job body cap.
    body = {'manifest': {}, 'preview': {'source': 'x'*600_000},
            'confirmed': True, 'allow_api_calls': False}
    with server(tmp_path) as (service, httpd):
        code, value = request(httpd, 'POST', '/api/batches', body)
        assert code == 202 and value['args'] == [body] and len(service.calls) == 1


def test_pasted_manifest_is_parsed_without_losing_duplicate_inner_fields(tmp_path):
    raw = '{"schema":"product_batch_manifest_v1","run_key":"one","items":[{"key":"x","request":{"mode":"example","mode":"local"}}]}'
    with server(tmp_path) as (service, httpd):
        code, _ = request(httpd, 'POST', '/api/batches/preview', {'manifest_json': raw})
        assert code == 400 and not service.calls
        value = {'schema': 'product_batch_manifest_v1', 'run_key': 'one',
                 'items': [{'key': 'x', 'request': {'mode': 'example'}}]}
        code, result = request(httpd, 'POST', '/api/batches/preview', {'manifest_json': json.dumps(value)})
        assert code == 200
        assert result['args'][0]['items'][0]['request'] == {
            'mode': 'example', 'provider': 'none', 'depth': 'quick'}


@pytest.mark.parametrize('method,route', [
    ('GET', '/api/batches/a/extra'),
    ('POST', '/api/batches/a/retry-all'),
    ('POST', '/api/batches/a/items/first/cancel/extra'),
])
def test_unknown_batch_routes_do_not_dispatch(tmp_path, method, route):
    with server(tmp_path) as (service, httpd):
        code, _ = request(httpd, method, route, {} if method == 'POST' else None)
        assert code == 404 and not service.calls
