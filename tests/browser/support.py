"""Synthetic native-provider backend for browser tests; never a public server."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from io import BytesIO
import json
from pathlib import Path
import socket
import threading
import time
from urllib.error import URLError

from issueintel.interfaces.web import make_server
from issueintel.investigation.providers import GroqProvider
from issueintel.investigation.workbench import Workbench
from issueintel.runtime.budget import BudgetPolicy
from issueintel.runtime.config import RuntimeConfig


CATALOG = {'cases': [], 'accepted': 0, 'pending': None, 'original_total': None,
           'frozen_count': 0, 'checkpoint_sha256': None, 'evaluation_authorized': False}


def _denied(*_args, **_kwargs):
    raise ValueError('Browser fixture forbids external providers, acquisition and benchmark data')


def install_network_guard(monkeypatch):
    """Keep real localhost browser HTTP working; deny outbound Python sockets."""
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_resolve = socket.getaddrinfo

    def allowed(address):
        return (isinstance(address, tuple) and len(address) >= 2
                and address[0] in {'127.0.0.1', 'localhost', '::1'})

    def connect(stream, address):
        if not allowed(address):
            raise AssertionError('Browser fixture blocked a nonlocal socket connection')
        return original_connect(stream, address)

    def connect_ex(stream, address):
        if not allowed(address):
            raise AssertionError('Browser fixture blocked a nonlocal socket connection')
        return original_connect_ex(stream, address)

    def resolve(host, *args, **kwargs):
        if host not in {'127.0.0.1', 'localhost', '::1', None}:
            raise AssertionError('Browser fixture blocked external name resolution')
        return original_resolve(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, 'connect', connect)
    monkeypatch.setattr(socket.socket, 'connect_ex', connect_ex)
    monkeypatch.setattr(socket, 'getaddrinfo', resolve)


class SyntheticTransport:
    """Only generated responses cross this transport; native receipts still run."""
    def __init__(self):
        from test_claim_review_jobs import ClaimProvider
        self.model = ClaimProvider()
        self.calls = []
        self._lock = threading.Lock()
        self._fail_kind = None
        self._block_kind = None
        self._blocked = threading.Event()
        self._released = threading.Event()
        self._released.set()

    @property
    def call_count(self):
        with self._lock:
            return len(self.calls)

    def fail_next(self, kind='verifier'):
        if kind not in {'generation', 'verifier', 'questions', 'check_plan', 'claim_review', 'issue_relation'}:
            raise ValueError('Choose a synthetic provider stage')
        with self._lock:
            if self._fail_kind is not None:
                raise ValueError('A synthetic failure is already armed')
            self._fail_kind = kind

    def block_next(self, kind='generation'):
        if kind not in {'generation', 'verifier'}:
            raise ValueError('Choose generation or verifier')
        with self._lock:
            if self._block_kind is not None or not self._released.is_set():
                raise ValueError('A synthetic block is already armed')
            self._blocked.clear()
            self._released.clear()
            self._block_kind = kind

    def wait_blocked(self, timeout=10):
        if not self._blocked.wait(timeout):
            raise AssertionError('Synthetic provider did not reach the armed block')

    def release(self):
        with self._lock:
            self._block_kind = None
            self._released.set()

    @staticmethod
    def _kind(schema):
        properties = schema.get('properties', {})
        if 'questions' in properties:
            return 'questions'
        if 'checks' in properties:
            return 'check_plan'
        if 'classification' in properties:
            return 'issue_relation'
        if 'reviews' in properties:
            if 'claims' in properties['reviews'].get('items', {}).get('properties', {}):
                return 'claim_review'
            return 'verifier'
        return 'generation'

    def __call__(self, request, **_kwargs):
        from test_adaptive_question_jobs import adaptive_response
        from test_issue_relation_jobs import relation_response
        body = json.loads(request.data)
        schema = body['response_format']['json_schema']['schema']
        kind = self._kind(schema)
        with self._lock:
            self.calls.append({'kind': kind, 'index': len(self.calls) + 1})
            fail = self._fail_kind == kind
            if fail:
                self._fail_kind = None
            block = self._block_kind == kind
            if block:
                self._block_kind = None
        if block:
            self._blocked.set()
            if not self._released.wait(30):
                raise URLError('Synthetic provider block timed out')
        if fail:
            raise URLError('Synthetic ' + kind + ' transport failure')
        if kind in {'questions', 'issue_relation'}:
            payload = json.loads(body['messages'][1]['content'])
            answer = adaptive_response(payload) if kind == 'questions' else relation_response(payload)
        else:
            answer = self.model.complete_structured(body['messages'],
                max_output_tokens=body['max_completion_tokens'], schema=schema)
        return BytesIO(json.dumps({'choices': [{'finish_reason': 'stop',
            'message': {'content': json.dumps(answer)}}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}}).encode())


class BrowserBackend:
    def __init__(self, root, monkeypatch):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True)
        (self.root / 'data').mkdir()
        # The subprocess/pytest environment may inherit user credentials. Never
        # inspect their values; replace or remove every credential used here.
        for name in ('TYPESAFE_API_KEY', 'GITHUB_TOKEN', 'GH_TOKEN', 'GITHUB_WEBHOOK_SECRET',
                     'GITHUB_APP_ID', 'GITHUB_APP_PRIVATE_KEY'):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv('GROQ_API_KEY', 'synthetic-browser-transport-only')
        monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
        monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(self.root / 'empty-gitconfig'))
        (self.root / 'empty-gitconfig').write_text('', encoding='utf-8')
        install_network_guard(monkeypatch)
        self.transport = SyntheticTransport()
        price_file = self.root / 'synthetic-prices.json'
        price_file.write_text(json.dumps({'checked_at': datetime.now(timezone.utc).date().isoformat(),
            'currency': 'USD', 'source': 'https://console.groq.com/docs/models',
            'models': {'openai/gpt-oss-120b': {'input_per_million': '0.15',
                'output_per_million': '0.60', 'context_tokens': 131072, 'max_output_tokens': 65536}}}),
            encoding='utf-8')
        self.config = RuntimeConfig(self.root, 'openai/gpt-oss-120b', 8192,
            BudgetPolicy(10_000_000, Decimal('100')), price_file,
            self.root / 'synthetic-budget.sqlite', reasoning_effort='high')

        def config_for(root):
            if Path(root).resolve() != self.root:
                raise AssertionError('Browser fixture tried to load another runtime root')
            return self.config

        monkeypatch.setattr(RuntimeConfig, 'load', config_for)
        monkeypatch.setattr('issueintel.runtime.typesafe.TypesafeConfig.load', _denied)
        monkeypatch.setattr('issueintel.investigation.providers._open_provider', _denied)
        monkeypatch.setattr('issueintel.investigation.workbench.accepted_context', _denied)
        monkeypatch.setattr('issueintel.investigation.workbench.fetch_github_issue', _denied)
        monkeypatch.setattr('issueintel.investigation.workbench.acquire_repository', _denied)
        monkeypatch.setattr('issueintel.investigation.workbench.public_catalog', lambda _: deepcopy(CATALOG))
        self.wb = Workbench(self.root, automatic_sync=False, typesafe_factory=_denied,
            provider_factory=lambda cfg, **kwargs: GroqProvider(cfg, transport=self.transport, **kwargs))
        self._failure_lock = threading.Lock()
        self._fail_stage = None
        self._fail_export = False
        original_save = self.wb._save
        from issueintel.investigation import workbench
        original_export = workbench.export_pack

        def save(row, **changes):
            result = original_save(row, **changes)
            with self._failure_lock:
                fail = self._fail_stage is not None and changes.get('stage') == self._fail_stage
                if fail:
                    self._fail_stage = None
            if fail:
                raise OSError('Synthetic persisted stage boundary interruption')
            return result

        def export(*args, **kwargs):
            with self._failure_lock:
                fail = self._fail_export
                self._fail_export = False
            if fail:
                raise OSError('Synthetic export boundary interruption')
            return original_export(*args, **kwargs)

        monkeypatch.setattr(self.wb, '_save', save)
        monkeypatch.setattr(workbench, 'export_pack', export)
        case, self.repository = self.wb._example()
        self.revision = case['revision']
        self.server = make_server(self.root, port=0, workbench=self.wb)
        self.base_url = 'http://127.0.0.1:' + str(self.server.server_port)
        self.session_token = self.server.session_token
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True,
                                       name='issueintel-browser-fixture')
        self.thread.start()

    def local_request(self, text='Synthetic parser.parse_list fails on None input.', *, provider='groq', depth='quick'):
        return {'mode': 'local', 'repository': str(self.repository), 'revision': self.revision,
                'issue_text': text, 'provider': provider, 'depth': depth}

    def submit(self, request=None):
        return self.await_job(self.wb.submit(request or {'mode': 'example', 'provider': 'groq'})['id'])

    def await_job(self, identifier, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            row = self.wb.job(identifier)
            if row['status'] not in {'queued', 'running'}:
                return row
            time.sleep(.02)
        raise AssertionError('Synthetic browser job did not finish: ' + identifier)

    def fail_after_stage(self, stage):
        if type(stage) is not str or not stage or len(stage) > 100:
            raise ValueError('Choose a bounded stage name')
        with self._failure_lock:
            if self._fail_stage is not None:
                raise ValueError('A synthetic stage failure is already armed')
            self._fail_stage = stage

    def fail_next_export(self):
        with self._failure_lock:
            self._fail_export = True

    def close(self):
        self.transport.release()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.wb.close()
