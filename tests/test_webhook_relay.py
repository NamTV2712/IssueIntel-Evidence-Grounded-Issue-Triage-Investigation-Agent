"""Real loopback HTTP/socket tests; no tunnel, GitHub, secrets or datasets."""
from contextlib import contextmanager
from hashlib import sha256
import hmac
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import socketserver
import threading
import time

import pytest

from issueintel.integrations import webhook_relay as relay_module


SECRET = 'x'  # Existing one-character values remain valid; never a real key.
DELIVERY = '12345678-1234-1234-1234-123456789abc'
BODY = b'{ "synthetic": true, "unicode": "\\u03b1" }\n'


def headers(body=BODY, **changes):
    result = {'Content-Type': 'application/json', 'Content-Length': str(len(body)),
        'X-Hub-Signature-256': 'sha256=' + hmac.new(SECRET.encode(), body, sha256).hexdigest(),
        'X-GitHub-Event': 'issues', 'X-GitHub-Delivery': DELIVERY}
    result.update(changes)
    return result


@contextmanager
def running(server):
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
    thread.start()
    try: yield server
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


@contextmanager
def upstream(*, status=202, payload=b'{"private":"session-and-job-metadata"}', location=None, delay=0):
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            body = self.rfile.read(int(self.headers['Content-Length']))
            calls.append({'path': self.path, 'headers': list(self.headers.items()), 'body': body})
            if delay: time.sleep(delay)
            try:
                self.send_response(status)
                if location: self.send_header('Location', location)
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers(); self.wfile.write(payload)
            except OSError: pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    with running(server): yield server, calls


@contextmanager
def relay_to(port):
    with running(relay_module.make_relay(port=0, workbench_port=port, secret=SECRET)) as server:
        yield server


def request(server, *, method='POST', path=relay_module.ROUTE, body=BODY, fields=None):
    client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
    try:
        client.request(method, path, body=body, headers=headers(body) if fields is None else fields)
        response = client.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally: client.close()


def raw_request(server, lines, body=b''):
    raw = ('\r\n'.join(lines) + '\r\n\r\n').encode('ascii') + body
    with socket.create_connection(('127.0.0.1', server.server_port), timeout=3) as connection:
        connection.sendall(raw)
        connection.shutdown(socket.SHUT_WR)
        chunks = []
        while True:
            try: chunk = connection.recv(65536)
            except ConnectionResetError: break
            if not chunk: break
            chunks.append(chunk)
    return b''.join(chunks)


def raw_lines(*extra, body=BODY):
    return ['POST /api/github/webhook HTTP/1.1', 'Host: public-relay.example',
            *[key + ': ' + value for key, value in headers(body).items()], *extra]


def test_signed_exact_route_preserves_raw_bytes_guid_and_only_allowlisted_headers():
    with upstream() as (receiver, calls), relay_to(receiver.server_port) as relay:
        supplied = headers(**{'Host': 'public-relay.example', 'Origin': 'https://attacker.invalid',
            'Authorization': 'Bearer never-forward', 'Cookie': 'private-session',
            'X-IssueIntel-Token': 'never-forward', 'X-Unrelated': 'drop-me'})
        code, response_headers, raw = request(relay, fields=supplied)
        assert code == 202 and json.loads(raw) == {'status': 'accepted'}
        assert len(calls) == 1 and calls[0]['body'] == BODY and calls[0]['path'] == relay_module.ROUTE
        forwarded = {key.lower(): value for key, value in calls[0]['headers']}
        assert forwarded['host'] == '127.0.0.1:' + str(receiver.server_port)
        assert forwarded['x-github-delivery'] == DELIVERY
        assert forwarded['x-hub-signature-256'] == headers()['X-Hub-Signature-256']
        assert forwarded['x-github-event'] == 'issues'
        assert set(forwarded) == {'host', 'content-type', 'content-length', 'x-hub-signature-256',
                                  'x-github-event', 'x-github-delivery'}
        assert not any(key in forwarded for key in ('origin', 'authorization', 'cookie', 'x-issueintel-token', 'x-unrelated'))
        assert b'private' not in raw and 'Location' not in response_headers


@pytest.mark.parametrize('method,path', [('GET', '/'), ('GET', '/api/github/webhook'),
    ('POST', '/api/github/webhook?query=1'), ('POST', '/api/github/webhook/'),
    ('POST', '/api/jobs'), ('POST', '/api/github/%77ebhook'), ('PUT', '/api/github/webhook'),
    ('DELETE', '/api/github/webhook'), ('OPTIONS', '/api/github/webhook'), ('PATCH', '/api/github/webhook'),
    ('CUSTOM', '/api/github/webhook'), ('HEAD', '/api/github/webhook')])
def test_no_ui_other_routes_or_methods_are_forwarded(method, path):
    with upstream() as (receiver, calls), relay_to(receiver.server_port) as relay:
        code, _, raw = request(relay, method=method, path=path)
        assert code == 404 and calls == []
        assert b'<html' not in raw and b'api/jobs' not in raw


def test_bad_signature_never_reaches_receiver():
    with upstream() as (receiver, calls), relay_to(receiver.server_port) as relay:
        code, _, raw = request(relay, fields=headers(**{'X-Hub-Signature-256': 'sha256=' + '0' * 64}))
        assert code == 403 and json.loads(raw) == {'status': 'invalid_signature'} and calls == []


@pytest.mark.parametrize('extra', ['Content-Length: 1', 'Content-Type: application/json',
    'Host: duplicate.example', 'X-Hub-Signature-256: sha256=' + '0' * 64,
    'X-GitHub-Delivery: ' + DELIVERY, 'X-GitHub-Event: issues', 'Transfer-Encoding: chunked',
    'Expect: 100-continue', ' Folded: forbidden', 'Bad Header: forbidden'])
def test_duplicate_smuggling_and_unsupported_framing_headers_are_rejected(extra):
    with upstream() as (receiver, calls), relay_to(receiver.server_port) as relay:
        raw = raw_request(relay, raw_lines(extra), BODY)
        assert raw.startswith((b'HTTP/1.0 400 ', b'HTTP/1.0 431 ')), raw[:100]
        assert calls == [] and b'forbidden' not in raw


@pytest.mark.parametrize('field,value', [('Content-Length', '0'), ('Content-Length', '-1'),
    ('Content-Length', '01'), ('Content-Length', '1,1'), ('Content-Type', 'text/plain'),
    ('X-GitHub-Delivery', 'not-a-guid'), ('X-GitHub-Event', 'issues with spaces'),
    ('X-Hub-Signature-256', 'invalid')])
def test_invalid_envelopes_are_rejected(field, value):
    with upstream() as (receiver, calls), relay_to(receiver.server_port) as relay:
        fields = headers(); fields[field] = value
        raw = raw_request(relay, ['POST /api/github/webhook HTTP/1.1', 'Host: local.example',
            *[key + ': ' + item for key, item in fields.items()]], BODY)
        assert raw.startswith(b'HTTP/1.0 400 ') and calls == []


def test_oversized_body_and_header_are_bounded_without_forwarding():
    with upstream() as (receiver, calls), relay_to(receiver.server_port) as relay:
        fields = headers(); fields['Content-Length'] = str(relay_module.MAX_BODY_BYTES + 1)
        raw = raw_request(relay, ['POST /api/github/webhook HTTP/1.1', 'Host: relay.example',
            *[key + ': ' + value for key, value in fields.items()]])
        assert raw.startswith(b'HTTP/1.0 413 ')
        raw = raw_request(relay, raw_lines('X-Oversized: ' + 'x' * relay_module.MAX_HEADER_BYTES), BODY)
        assert raw.startswith((b'HTTP/1.0 400 ', b'HTTP/1.0 431 ')) and calls == []


@pytest.mark.parametrize('code', [200, 201, 202, 204, 400, 401, 403, 404, 409, 429, 500, 503])
def test_receiver_bodies_never_escape_and_error_statuses_remain_visible(code):
    with upstream(status=code) as (receiver, calls), relay_to(receiver.server_port) as relay:
        actual, _, raw = request(relay)
        assert actual == (202 if code < 300 else code)
        assert json.loads(raw) == {'status': 'accepted' if code < 300 else 'receiver_error'}
        assert b'session-and-job-metadata' not in raw and len(calls) == 1


def test_redirect_is_not_followed_and_location_or_private_body_is_not_exposed():
    with upstream(status=302, location='http://private-target.invalid/token') as (receiver, calls), relay_to(receiver.server_port) as relay:
        code, fields, raw = request(relay)
        assert code == 502 and json.loads(raw) == {'status': 'receiver_error'}
        assert 'Location' not in fields and b'private-target' not in raw and len(calls) == 1


def test_large_receiver_response_is_only_drained_to_a_bound():
    with upstream(payload=b'private' * 100_000) as (receiver, calls), relay_to(receiver.server_port) as relay:
        code, _, raw = request(relay)
        assert code == 202 and raw == b'{"status":"accepted"}' and len(calls) == 1


def test_upstream_timeout_reports_unknown_outcome_and_does_not_retry(monkeypatch):
    monkeypatch.setattr(relay_module, 'REQUEST_SECONDS', 0.15)
    with upstream(delay=0.5) as (receiver, calls), relay_to(receiver.server_port) as relay:
        code, _, raw = request(relay)
        assert code == 504 and json.loads(raw) == {'status': 'outcome_unknown'}
        assert len(calls) == 1


def test_body_read_timeout_is_bounded_without_forwarding(monkeypatch):
    monkeypatch.setattr(relay_module, 'REQUEST_SECONDS', 0.15)
    monkeypatch.setattr(relay_module, 'IDLE_SECONDS', 0.1)
    with upstream() as (receiver, calls), relay_to(receiver.server_port) as relay:
        with socket.create_connection(('127.0.0.1', relay.server_port), timeout=2) as connection:
            connection.sendall(('\r\n'.join(raw_lines()) + '\r\n\r\n').encode())
            raw = connection.recv(4096)
        assert raw.startswith(b'HTTP/1.0 408 ') and calls == []


def test_slow_request_line_cannot_extend_total_request_deadline(monkeypatch):
    monkeypatch.setattr(relay_module, 'REQUEST_SECONDS', 0.15)
    monkeypatch.setattr(relay_module, 'IDLE_SECONDS', 0.1)
    with upstream() as (receiver, calls), relay_to(receiver.server_port) as relay:
        with socket.create_connection(('127.0.0.1', relay.server_port), timeout=2) as connection:
            def trickle():
                for char in b'POST /api/github/webhook HTTP/1.1\r\n':
                    try: connection.sendall(bytes((char,)))
                    except OSError: return
                    time.sleep(0.025)
            sender = threading.Thread(target=trickle, daemon=True); sender.start()
            start = time.monotonic()
            raw = connection.recv(4096)
            elapsed = time.monotonic() - start
        sender.join(timeout=0.2)
        assert raw.startswith(b'HTTP/1.0 408 ') and elapsed < 0.8 and calls == []


def test_slow_upstream_header_trickle_is_cut_off_without_retry(monkeypatch):
    monkeypatch.setattr(relay_module, 'REQUEST_SECONDS', 0.2)
    calls = []
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            calls.append(self.request.recv(4096))
            for char in b'HTTP/1.1 202 Accepted\r\nContent-Length: 0\r\n\r\n':
                try: self.request.sendall(bytes((char,)))
                except OSError: return
                time.sleep(0.025)
    server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    with running(server), relay_to(server.server_address[1]) as relay:
        start = time.monotonic()
        code, _, raw = request(relay)
        assert code == 504 and json.loads(raw) == {'status': 'outcome_unknown'}
        assert time.monotonic() - start < 0.8 and len(calls) == 1


@pytest.mark.parametrize('missing', ['Host', 'Content-Length', 'Content-Type', 'X-Hub-Signature-256',
                                    'X-GitHub-Event', 'X-GitHub-Delivery'])
def test_required_headers_must_each_appear_once(missing):
    with upstream() as (receiver, calls), relay_to(receiver.server_port) as relay:
        fields = {'Host': 'public.example', **headers()}; fields.pop(missing)
        raw = raw_request(relay, ['POST /api/github/webhook HTTP/1.1',
            *[key + ': ' + value for key, value in fields.items()]], BODY)
        assert raw.startswith(b'HTTP/1.0 400 ') and calls == []


def test_short_body_is_not_authenticated_or_forwarded():
    with upstream() as (receiver, calls), relay_to(receiver.server_port) as relay:
        raw = raw_request(relay, raw_lines(), BODY[:-3])
        assert raw.startswith(b'HTTP/1.0 400 ') and calls == []


def test_exact_body_limit_is_accepted_without_reencoding():
    body = b'x' * relay_module.MAX_BODY_BYTES
    with upstream() as (receiver, calls), relay_to(receiver.server_port) as relay:
        code, _, raw = request(relay, body=body)
        assert code == 202 and json.loads(raw) == {'status': 'accepted'}
        assert len(calls) == 1 and calls[0]['body'] == body


@pytest.mark.parametrize('line', ['POST /api/github/webhook HTTP/0.9', 'POST ' + 'x' * 3000 + ' HTTP/1.1'])
def test_bad_or_oversized_request_line_gets_only_minimal_error(line):
    with upstream() as (receiver, calls), relay_to(receiver.server_port) as relay:
        raw = raw_request(relay, [line])
        assert raw.startswith(b'HTTP/1.0 400 ') and calls == []
        assert b'<html' not in raw and b'xxx' not in raw


def test_admission_is_bounded_before_creating_slow_header_workers(monkeypatch):
    monkeypatch.setattr(relay_module, 'REQUEST_SECONDS', 1.5)
    with upstream() as (receiver, calls), relay_to(receiver.server_port) as relay:
        sockets = []
        try:
            for _ in range(relay_module.MAX_CONNECTIONS):
                connection = socket.create_connection(('127.0.0.1', relay.server_port), timeout=2)
                connection.sendall(b'POST /api/github/webhook HTTP/1.1\r\n')
                sockets.append(connection)
            deadline = time.monotonic() + 1
            while relay._admission._value and time.monotonic() < deadline: time.sleep(0.005)
            assert relay._admission._value == 0
            # Admission rejects this socket before reading any request bytes.
            # Sending a body with http.client races that early close on Windows,
            # where its separate body send can abort before getresponse(). Read
            # the unsolicited rejection instead, proving no header worker was
            # needed and leaving no unread client bytes that could cause a reset.
            with socket.create_connection(('127.0.0.1', relay.server_port), timeout=2) as rejected:
                response = http.client.HTTPResponse(rejected)
                response.begin()
                assert response.status == 503 and json.loads(response.read()) == {'status': 'busy'}
            assert calls == [] and relay._admission._value == 0
        finally:
            for connection in sockets: connection.close()
        deadline = time.monotonic() + 1
        while relay._admission._value < relay_module.MAX_CONNECTIONS and time.monotonic() < deadline:
            time.sleep(0.005)
        assert relay._admission._value == relay_module.MAX_CONNECTIONS
        code, _, raw = request(relay)
        assert code == 202 and json.loads(raw) == {'status': 'accepted'} and len(calls) == 1


@pytest.mark.parametrize('kwargs', [{'port': -1}, {'port': 65536}, {'port': True},
    {'workbench_port': 0}, {'workbench_port': 65536}, {'workbench_port': False},
    {'secret': ''}, {'secret': None}, {'secret': 'x' * 4097}, {'secret': '\ud800'}])
def test_invalid_ports_or_secret_are_rejected_without_binding(kwargs):
    arguments = {'port': 0, 'workbench_port': 8787, 'secret': SECRET, **kwargs}
    with pytest.raises(ValueError): relay_module.make_relay(**arguments)


def test_loopback_only_and_self_forward_port_is_rejected_after_binding():
    with relay_to(1) as relay:
        assert relay.server_address[0] == '127.0.0.1'
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
    with pytest.raises(ValueError): relay_module.make_relay(port=port, workbench_port=port, secret=SECRET)


def test_receiver_unavailable_has_minimal_unknown_outcome_without_exception_details():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
    with relay_to(port) as relay:
        code, _, raw = request(relay)
        assert code == 502 and json.loads(raw) == {'status': 'outcome_unknown'}
        assert b'127.0.0.1' not in raw and b'Connection' not in raw
