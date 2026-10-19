"""Rejected local requests still deliver HTTP errors when bodies arrive later."""
from http.client import HTTPConnection
import json
import time

import pytest

from test_workbench import local_server


class DelayedBodyConnection(HTTPConnection):
    """Model headers and body arriving in separate network writes."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._headers_sent = False

    def send(self, data):
        if self._headers_sent:
            # On Windows, an early server close can reset the socket while the
            # ordinary HTTP client is still sending its bounded request body.
            time.sleep(.03)
        else:
            self._headers_sent = True
        return super().send(data)


@pytest.mark.parametrize(('denial', 'expected'), [
    ('missing_token', 'Local session token required'),
    ('foreign_origin', 'Local origin required'),
    ('foreign_host', 'Local origin required'),
])
def test_denied_post_consumes_bounded_delayed_body_without_parsing_or_dispatch(
        tmp_path, monkeypatch, denial, expected):
    with local_server(tmp_path) as server:
        def forbidden(*args, **kwargs):
            pytest.fail('Denied request reached an investigation or provider')

        monkeypatch.setattr(server.workbench, 'submit', forbidden)
        server.workbench.provider_factory = forbidden
        headers = {'Content-Type': 'application/json'}
        if denial != 'missing_token':
            headers['X-IssueIntel-Token'] = server.session_token
        if denial == 'foreign_origin':
            headers['Origin'] = 'https://invalid.test'
        if denial == 'foreign_host':
            headers['Host'] = 'invalid.test'
        # Intentionally malformed JSON: authentication denial must retain its
        # status, without parsing source content or invoking application code.
        body = '{not-json:' + 'x' * 96000
        connection = DelayedBodyConnection('127.0.0.1', server.server_port, timeout=3)
        try:
            connection.request('POST', '/api/investigations', body=body, headers=headers)
            response = connection.getresponse()
            assert response.status == 403
            assert json.loads(response.read()) == {'error': expected}
        finally:
            connection.close()
        assert server.workbench.history() == []
