"""A loopback webhook-only endpoint for an independently configured HTTPS tunnel.

Only authenticated raw GitHub deliveries reach the fixed local Workbench route.
This module reads no configuration or environment, persists nothing, retries
nothing, exposes no Workbench response content and serves no application UI.
"""
from hashlib import sha256
import hmac
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import socket
import threading
import time


ROUTE = '/api/github/webhook'
MAX_BODY_BYTES = 2_000_000
MAX_HEADER_BYTES = 16_384
MAX_REQUEST_LINE = 2_048
MAX_RESPONSE_BYTES = 65_536
MAX_CONNECTIONS = 4
REQUEST_SECONDS = 8.0
IDLE_SECONDS = 2.0
_SIGNATURE = re.compile(r'sha256=[a-f0-9]{64}')
_EVENT = re.compile(r'[a-z][a-z0-9_]{0,63}')
_DELIVERY = re.compile(r'[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}')
_HEADER_NAME = re.compile(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+")


def _port(value, *, listen=False):
    if type(value) is not int or not (0 if listen else 1) <= value <= 65535:
        raise ValueError('Choose a valid loopback port')
    return value


class _HeaderReader:
    def __init__(self, handler):
        self.handler = handler
        self.remaining = MAX_HEADER_BYTES

    def readline(self, size=-1):
        if self.remaining <= 0:
            raise http.client.HTTPException('Header bound exceeded')
        line = self.handler._read_line(min(self.remaining, size if size > 0 else self.remaining))
        self.remaining -= len(line)
        if not line or not line.endswith(b'\r\n'):
            raise http.client.HTTPException('Invalid header framing')
        if line != b'\r\n':
            if line[:1] in (b' ', b'\t'):
                raise http.client.HTTPException('Folded headers are unsupported')
            name, separator, value = line[:-2].partition(b':')
            if (not separator or _HEADER_NAME.fullmatch(name) is None
                    or any(char != 9 and not 32 <= char <= 126 for char in value)):
                raise http.client.HTTPException('Invalid header syntax')
        return line


class _RelayServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = True
    request_queue_size = MAX_CONNECTIONS * 2

    def __init__(self, address, handler):
        self._admission = threading.BoundedSemaphore(MAX_CONNECTIONS)
        super().__init__(address, handler)

    def process_request(self, request, client_address):
        # Admission occurs before ThreadingMixIn creates a worker. Slow headers
        # therefore cannot produce an unbounded number of request threads.
        if not self._admission.acquire(blocking=False):
            try:
                request.settimeout(0.1)
                body = b'{"status":"busy"}'
                request.sendall(b'HTTP/1.0 503 Service Unavailable\r\nConnection: close\r\n'
                    b'Content-Type: application/json\r\nContent-Length: ' + str(len(body)).encode() +
                    b'\r\nCache-Control: no-store\r\n\r\n' + body)
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._admission.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._admission.release()

    def handle_error(self, request, client_address):
        pass  # No request path, payload, headers or exception bodies in logs.


def make_relay(*, port=8788, workbench_port=8787, secret):
    """Create a bounded loopback server; the caller owns its lifetime/secret."""
    port = _port(port, listen=True)
    workbench_port = _port(workbench_port)
    try:
        secret_bytes = secret.encode('utf-8') if type(secret) is str and len(secret) <= 4096 else b''
    except UnicodeError:
        secret_bytes = b''
    if not 0 < len(secret_bytes) <= 4096:
        raise ValueError('Configure a nonempty bounded webhook secret')

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.0'

        def setup(self):
            super().setup()
            self._deadline = time.monotonic() + REQUEST_SECONDS
            self._input = self.rfile

        def log_message(self, *args):
            pass

        def _remaining(self):
            remaining = self._deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Request deadline')
            return remaining

        def _read_line(self, maximum):
            # BufferedReader.readline can stay blocked on a slow trickle beyond
            # its socket idle timeout. Bound each byte against the total clock.
            line = bytearray()
            while len(line) <= maximum:
                self.connection.settimeout(min(IDLE_SECONDS, self._remaining()))
                char = self._input.read1(1)
                if not char:
                    return bytes(line)
                line.extend(char)
                if char == b'\n':
                    if len(line) > maximum:
                        break
                    return bytes(line)
            raise http.client.HTTPException('Line bound exceeded')

        def _respond(self, code, status):
            self.close_connection = True
            raw = json.dumps({'status': status}, separators=(',', ':')).encode('ascii')
            try:
                # Even a client that stops reading cannot extend an exhausted
                # eight-second request deadline into an unbounded response send.
                self.connection.settimeout(0.25)
                self.send_response_only(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.send_header('Connection', 'close')
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                if getattr(self, 'command', None) != 'HEAD':
                    self.wfile.write(raw)
            except OSError:
                pass

        def send_error(self, code, message=None, explain=None):
            self._respond(404 if code == 501 else code, 'not_found' if code == 501 else 'invalid_request')

        def parse_request(self):
            if (not self.raw_requestline.endswith(b'\r\n')
                    or self.raw_requestline.rstrip(b'\r\n').split(b' ')[-1:] not in ([b'HTTP/1.0'], [b'HTTP/1.1'])):
                self._respond(400, 'invalid_request')
                return False
            original = self.rfile
            self.rfile = _HeaderReader(self)
            try:
                return super().parse_request()
            finally:
                self.rfile = original

        def handle_one_request(self):
            self.close_connection = True
            # Errors/timeouts can occur before BaseHTTPRequestHandler has parsed
            # a request line; its response helper still needs these defaults.
            self.request_version = 'HTTP/1.0'
            self.command = None
            try:
                self.raw_requestline = self._read_line(MAX_REQUEST_LINE)
                if not self.raw_requestline:
                    return
                if not self.parse_request():
                    return
                self.close_connection = True
                if self.command != 'POST' or self.path != ROUTE:
                    self._respond(404, 'not_found')
                    return
                self._webhook()
            except (TimeoutError, socket.timeout):
                self._respond(408, 'request_timeout')
            except (http.client.HTTPException, ValueError):
                self._respond(400, 'invalid_request')
            except OSError:
                self.close_connection = True

        def _single(self, name):
            values = self.headers.get_all(name) or []
            if len(values) != 1:
                raise ValueError('Required single header')
            return values[0]

        def _webhook(self):
            length = self._single('Content-Length')
            content_type = self._single('Content-Type')
            host = self._single('Host')
            signature = self._single('X-Hub-Signature-256')
            event = self._single('X-GitHub-Event')
            delivery = self._single('X-GitHub-Delivery')
            if (self.headers.get_all('Transfer-Encoding') or self.headers.get_all('Expect')
                    or not host or len(host) > 255 or any(char.isspace() for char in host)
                    or content_type.split(';')[0].strip().lower() != 'application/json'
                    or re.fullmatch(r'[1-9][0-9]{0,6}', length) is None
                    or _SIGNATURE.fullmatch(signature) is None or _EVENT.fullmatch(event) is None
                    or _DELIVERY.fullmatch(delivery) is None):
                self._respond(400, 'invalid_request')
                return
            size = int(length)
            if size > MAX_BODY_BYTES:
                self._respond(413, 'body_too_large')
                return
            chunks = []
            remaining = size
            while remaining:
                self.connection.settimeout(min(IDLE_SECONDS, self._remaining()))
                chunk = self.rfile.read1(min(remaining, 65536))
                if not chunk:
                    self._respond(400, 'invalid_request')
                    return
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b''.join(chunks)
            expected = 'sha256=' + hmac.new(secret_bytes, raw, sha256).hexdigest()
            if not hmac.compare_digest(expected, signature):
                self._respond(403, 'invalid_signature')
                return
            self._forward(raw, signature, event, delivery)

        def _forward(self, raw, signature, event, delivery):
            upstream = http.client.HTTPConnection('127.0.0.1', workbench_port, timeout=self._remaining())
            deadline = None
            expired = threading.Event()
            try:
                upstream.connect()
                upstream_socket = upstream.sock
                def stop_upstream():
                    expired.set()
                    try: upstream_socket.shutdown(socket.SHUT_RDWR)
                    except OSError: pass
                # Guard http.client's internal header/chunk parsing too, which
                # can otherwise extend idle timeouts with a slow response.
                deadline = threading.Timer(self._remaining(), stop_upstream)
                deadline.daemon = True
                deadline.start()
                upstream.putrequest('POST', ROUTE, skip_accept_encoding=True)
                for name, value in {'Content-Type': 'application/json', 'Content-Length': str(len(raw)),
                        'X-Hub-Signature-256': signature, 'X-GitHub-Event': event,
                        'X-GitHub-Delivery': delivery}.items():
                    upstream.putheader(name, value)
                upstream.endheaders(raw)
                response = upstream.getresponse()
                drained = 0
                while drained < MAX_RESPONSE_BYTES and not response.isclosed():
                    upstream_socket.settimeout(self._remaining())
                    chunk = response.read1(min(65536, MAX_RESPONSE_BYTES - drained))
                    if not chunk:
                        break
                    drained += len(chunk)
                if expired.is_set():
                    raise TimeoutError('Receiver deadline')
                if 200 <= response.status < 300:
                    self._respond(202, 'accepted')
                elif 400 <= response.status < 600:
                    self._respond(response.status, 'receiver_error')
                else:
                    self._respond(502, 'receiver_error')
            except (TimeoutError, socket.timeout):
                self._respond(504, 'outcome_unknown')
            except (OSError, http.client.HTTPException):
                self._respond(504 if expired.is_set() else 502, 'outcome_unknown')
            finally:
                if deadline is not None:
                    deadline.cancel()
                upstream.close()

    server = _RelayServer(('127.0.0.1', port), Handler)
    if server.server_port == workbench_port:
        server.server_close()
        raise ValueError('Relay and Workbench must use different loopback ports')
    return server


def serve_relay(*, port, workbench_port, secret):
    """Serve until interrupted, without discovering config or starting tunnels."""
    server = make_relay(port=port, workbench_port=workbench_port, secret=secret)
    print('IssueIntel webhook relay: http://127.0.0.1:'+str(server.server_port)+ROUTE,flush=True)
    try:
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
