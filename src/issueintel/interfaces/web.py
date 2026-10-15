"""Bounded localhost HTTP interface; no filesystem browsing or remote binding."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from urllib.parse import urlsplit
import hmac
import json
import secrets
import os
import sqlite3

from issueintel.investigation.workbench import Workbench

STATIC = {'/': ('index.html', 'text/html; charset=utf-8'),
          '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
          '/style.css': ('style.css', 'text/css; charset=utf-8')}
STATIC['/comparison.css'] = ('comparison.css', 'text/css; charset=utf-8')
STATIC['/locales.js'] = ('locales.js', 'text/javascript; charset=utf-8')
STATIC['/preferences.js'] = ('preferences.js', 'text/javascript; charset=utf-8')


def make_server(root, *, host='127.0.0.1', port=8787, workbench=None):
    if host not in {'127.0.0.1', 'localhost'} or type(port) is not int or not 0 <= port <= 65535:
        raise ValueError('The demo binds to localhost only')
    workbench = workbench or Workbench(root)
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Request paths and source content are not written to logs.

        def _send(self, status, raw, mime='application/json; charset=utf-8', *, download=None):
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
            if download:
                self.send_header('Content-Disposition', f'attachment; filename="{download}"')
            self.end_headers()
            self.wfile.write(raw)

        def _json(self, status, value):
            self._send(status, json.dumps(value, ensure_ascii=False, allow_nan=False).encode())

        def _guard(self, *, api=False):
            hosts = self.headers.get_all('Host') or []
            allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            origin = self.headers.get('Origin')
            if (len(hosts) != 1 or hosts[0] not in allowed
                    or (origin is not None and origin not in {'http://'+x for x in allowed})):
                self._json(403, {'error': 'Local origin required'})
                return False
            if api and not hmac.compare_digest(self.headers.get('X-IssueIntel-Token', ''), token):
                self._json(403, {'error': 'Local session token required'})
                return False
            return True

        def do_GET(self):
            route = urlsplit(self.path).path
            if not self._guard(api=route.startswith('/api/')):
                return
            try:
                if route in STATIC:
                    name, mime = STATIC[route]
                    raw = files('issueintel.interfaces').joinpath('static', name).read_bytes()
                    if name == 'index.html':
                        raw = raw.replace(b'__SESSION_TOKEN__', token.encode())
                    self._send(200, raw, mime)
                elif route == '/api/catalog':
                    self._json(200, workbench.catalog())
                elif route == '/api/jobs':
                    self._json(200, workbench.history())
                elif route.startswith('/api/jobs/'):
                    parts = route.split('/')[3:]
                    if len(parts) == 1:
                        self._json(200, workbench.job(parts[0]))
                    elif len(parts) == 2 and parts[1] == 'intake':
                        self._json(200, workbench.intake(parts[0]))
                    elif len(parts) == 2 and parts[1] == 'plan':
                        self._json(200, workbench.plan(parts[0]))
                    elif len(parts) == 2 and parts[1] in {'pack.json', 'report.md'}:
                        raw = workbench.result_bytes(parts[0], parts[1])
                        self._send(200, raw, 'application/json; charset=utf-8' if parts[1] == 'pack.json' else 'text/markdown; charset=utf-8', download=parts[1])
                    elif len(parts)==3 and parts[1]=='stages':
                        raw=workbench.stage_bytes(parts[0],parts[2])
                        self._send(200,raw,download=parts[2]+'.json')
                    else:
                        self._json(404, {'error': 'Route not found'})
                else:
                    self._json(404, {'error': 'Route not found'})
            except FileNotFoundError:
                self._json(404, {'error': 'Local data or job not found'})
            except (ValueError, KeyError, OSError, sqlite3.DatabaseError):
                self._json(409, {'error': 'Local data integrity check failed'})

        def do_POST(self):
            route = urlsplit(self.path).path
            if not self._guard(api=route != '/api/github/webhook'):
                return
            lengths = self.headers.get_all('Content-Length') or []
            if (self.headers.get('Transfer-Encoding') is not None or len(lengths) != 1
                    or not lengths[0].isdigit() or not 0 < int(lengths[0]) <= 512000
                    or self.headers.get('Content-Type', '').split(';')[0] != 'application/json'):
                self._json(400, {'error': 'A bounded JSON body is required'})
                return
            try:
                raw = self.rfile.read(int(lengths[0]))
                if route == '/api/github/webhook':
                    if not os.environ.get('GITHUB_WEBHOOK_SECRET'):
                        self._json(503, {'error':'GitHub webhook is not configured'})
                        return
                    job = workbench.receive_webhook(raw, signature=self.headers.get('X-Hub-Signature-256'),
                        event=self.headers.get('X-GitHub-Event'), delivery=self.headers.get('X-GitHub-Delivery'),
                        secret=os.environ['GITHUB_WEBHOOK_SECRET'])
                    self._json(202, job)
                    return
                request = json.loads(raw)
                if route == '/api/investigations':
                    self._json(202, workbench.submit(request))
                elif route == '/api/comparisons':
                    self._json(200, workbench.compare(request))
                elif route == '/api/related':
                    self._json(200, workbench.related(request))
                elif route.startswith('/api/jobs/') and route.endswith('/plan'):
                    if len(route.split('/')) != 5:
                        raise ValueError('Invalid plan request')
                    self._json(200, workbench.plan(route.split('/')[3],request))
                elif route.startswith('/api/jobs/') and route.endswith('/cancel'):
                    if request != {} or len(route.split('/')) != 5:
                        raise ValueError('Invalid cancellation request')
                    self._json(200, workbench.cancel(route.split('/')[3]))
                elif route.startswith('/api/jobs/') and route.endswith('/github-preview'):
                    if request != {} or len(route.split('/')) != 5:
                        raise ValueError('Invalid preview request')
                    self._json(200, workbench.preview_comment(route.split('/')[3]))
                elif route.startswith('/api/jobs/') and route.endswith('/github-publish'):
                    if len(route.split('/')) != 5:
                        raise ValueError('Invalid publication request')
                    self._json(200, workbench.publish_comment(route.split('/')[3], request))
                else:
                    self._json(404, {'error': 'Route not found'})
            except (ValueError, UnicodeError, KeyError, TypeError):
                self._json(400, {'error': 'Invalid investigation input'})
            except FileNotFoundError:
                self._json(404, {'error': 'Job not found'})
            except sqlite3.DatabaseError:
                self._json(409, {'error': 'Local plan storage is unavailable'})
            except RuntimeError:
                self._json(429, {'error': 'Queue is full; wait for a current investigation'})

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.workbench = workbench
    server.session_token = token
    return server


def serve(root, *, host='127.0.0.1', port=8787):
    server = make_server(root, host=host, port=port)
    print(f'IssueIntel: http://{host}:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.workbench.close()
