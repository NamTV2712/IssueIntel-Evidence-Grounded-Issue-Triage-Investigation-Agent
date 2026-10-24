from __future__ import annotations

import json
import os
import math
import ssl
from hashlib import sha256
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler

from issueintel.runtime.budget import BudgetDenied
from issueintel.runtime.config import RuntimeConfig


_ERROR_CODES = {'json_validate_failed', 'tool_use_failed', 'incomplete_json_output',
                'rate_limit_exceeded', 'invalid_api_key', 'invalid_request_error',
                'model_decommissioned', 'context_length_exceeded', 'model_not_found',
                'insufficient_quota', 'redirect_forbidden', 'response_size_limit'}
_TRANSPORT_TYPES = {'HTTPError', 'URLError', 'TimeoutError', 'ConnectionResetError',
                    'ConnectionAbortedError', 'ConnectionRefusedError', 'BrokenPipeError',
                    'SSLError', 'SSLCertVerificationError', 'RemoteDisconnected',
                    'IncompleteRead', 'BadStatusLine', 'JSONDecodeError', 'UnicodeDecodeError',
                    'OSError', 'RuntimeError', 'TypeError', 'ValueError', 'ProviderError',
                    'gaierror', 'other'}
_FAILURE_PHASES = {'request', 'response_read', 'response_json'}


def _transport_diagnostics(error, phase, *, status=None, byte_count=None, content_type='unknown'):
    """Allowlisted facts only: exception messages/reasons may contain secrets."""
    name = type(error).__name__
    result = {'failure_phase': phase,
              'transport_error_type': name if name in _TRANSPORT_TYPES else 'other'}
    if type(status) is int and 100 <= status <= 599:
        result['response_status'] = status
    if type(byte_count) is int and 0 <= byte_count <= 4_000_001:
        result['response_byte_count'] = byte_count
    if content_type in {'json', 'html', 'text', 'other', 'unknown'}:
        result['response_content_type'] = content_type
    reason = error.reason if isinstance(error, URLError) else error
    reason_name = type(reason).__name__
    if reason_name in _TRANSPORT_TYPES:
        result['transport_reason_type'] = reason_name
    if isinstance(reason, OSError):
        number = reason.errno
        if type(number) is int and -2**31 <= number < 2**31:
            result['transport_errno'] = number
    if isinstance(reason, ssl.SSLCertVerificationError):
        number = reason.verify_code
        if type(number) is int and 0 <= number < 2**31:
            result['ssl_verify_code'] = number
    return result


class ProviderError(RuntimeError):
    def __init__(self, message, *, status=None, retry_after=None, code=None, diagnostics=None):
        super().__init__(message)
        self.status, self.retry_after, self.code = status, retry_after, code
        self.diagnostics = dict(diagnostics) if isinstance(diagnostics,dict) else {}


def output_rejected(error):
    """Only output failures permit a caller-owned, bounded repair slot."""
    return isinstance(error, ProviderError) and (
        error.status == 400 and isinstance(error.code,str) and error.code in {'json_validate_failed', 'tool_use_failed'}
        or error.status is None and error.code == 'incomplete_json_output')


def failure_metadata(error):
    """Persist diagnostic categories, never upstream messages or source text."""
    row = {'error_type': type(error).__name__}
    if isinstance(error, ProviderError):
        row.update(http_status=error.status if type(error.status) is int and 100 <= error.status <= 599 else None,
                   provider_code=error.code if isinstance(error.code,str) and error.code in _ERROR_CODES else 'unknown',
                   terminal_output_error=output_rejected(error))
        delay = error.retry_after
        row['retry_after_seconds'] = delay if type(delay) in (int, float) and 0 <= delay <= 86400 and math.isfinite(delay) else None
        for key in ('failed_generation_present','failed_generation_is_json_object','native_call_marker','token_limit_hint'):
            if type(error.diagnostics.get(key)) is bool:row[key]=error.diagnostics[key]
        checksum=error.diagnostics.get('failed_generation_sha256')
        if isinstance(checksum,str) and re.fullmatch('[a-f0-9]{64}',checksum):row['failed_generation_sha256']=checksum
        for key in ('transport_error_type', 'transport_reason_type'):
            value = error.diagnostics.get(key)
            if isinstance(value, str) and value in _TRANSPORT_TYPES:
                row[key] = value
        phase = error.diagnostics.get('failure_phase')
        if isinstance(phase, str) and phase in _FAILURE_PHASES:
            row['failure_phase'] = phase
        content_type = error.diagnostics.get('response_content_type')
        if isinstance(content_type, str) and content_type in {'json', 'html', 'text', 'other', 'unknown'}:
            row['response_content_type'] = content_type
        for key, minimum, maximum in (('response_status', 100, 599),
                ('response_byte_count', 0, 4_000_001), ('transport_errno', -2**31, 2**31-1),
                ('ssl_verify_code', 0, 2**31-1)):
            value = error.diagnostics.get(key)
            if type(value) is int and minimum <= value <= maximum:
                row[key] = value
    return row


def _output_diagnostics(info):
    """Bounded derived flags/hash only; upstream text is discarded."""
    value=info.get('failed_generation');present=isinstance(value,(str,dict,list))
    text=value if isinstance(value,str) else json.dumps(value,ensure_ascii=False) if present else ''
    try:parsed=json.loads(text)
    except ValueError:parsed=None
    message=info.get('message','')
    return {'failed_generation_present':present,
            'failed_generation_sha256':sha256(text.encode()).hexdigest(),
            'failed_generation_is_json_object':isinstance(parsed,dict),
            'native_call_marker':bool(re.search(r'<(?:tool_call|function[=>])|assistant\s+to=|<\|(?:python_tag|channel)',text)),
            'token_limit_hint':isinstance(message,str) and bool(re.search(r'max[_ ](?:completion[_ ]tokens|tokens)|token limit|truncat',message,re.I))}


class _NoCredentialRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ProviderError("Provider redirects are forbidden", code='redirect_forbidden')


def _open_provider(request, timeout):
    return build_opener(_NoCredentialRedirect()).open(request, timeout=timeout)


def _input_token_bound(messages, schema=None):
    texts = [message["content"] for message in messages]
    if schema is not None:
        texts.append(json.dumps(schema,ensure_ascii=False))
    try:
        import tiktoken
        encoding = tiktoken.get_encoding("o200k_harmony")
        return sum(len(encoding.encode(text,disallowed_special=())) + 1024 for text in texts)
    except ImportError:
        return sum(len(text.encode("utf-8")) + 1024 for text in texts)


class GroqProvider:
    name = "groq"

    def __init__(self, config: RuntimeConfig, *, transport=None, call_context=None, on_receipt=None):
        self.config, self.model = config, config.model
        self.transport = transport or _open_provider
        self.call_context = call_context
        self.on_receipt = on_receipt

    def _publish_receipt(self, ledger, reservation):
        if self.on_receipt is not None:
            self.on_receipt(ledger.receipt(reservation))

    def __call__(self, messages, max_output_tokens=2048):
        return self.complete_json(messages, max_output_tokens)

    def complete_structured(self, messages, *, max_output_tokens=2048, schema):
        return self.complete_json(messages, max_output_tokens, schema=schema)

    def complete_function(self, messages, *, max_output_tokens=2048, schema):
        """Return one typed proposal; never execute a model-selected function."""
        return self.complete_json(messages,max_output_tokens,schema=schema,function_call=True)

    def complete_json(self, messages: list[dict], max_output_tokens: int = 2048, *, schema=None, function_call=False) -> dict:
        self.last_reservation = None
        self.last_usage = None
        key = os.environ.get("GROQ_API_KEY", "").strip()
        if not key:
            raise BudgetDenied("Add GROQ_API_KEY to local .env; never paste credentials into chat")
        if not messages or len(messages) > 8 or any(set(message) != {"role", "content"} or message["role"] not in {"system", "user", "assistant"} or not isinstance(message["content"], str) for message in messages):
            raise ProviderError("Only bounded text messages are supported")
        if type(max_output_tokens) is not int or not 0 < max_output_tokens <= self.config.max_output_tokens:
            raise BudgetDenied("Output request exceeds configured per-call bound")
        if schema is not None:
            if not isinstance(schema, dict):
                raise ProviderError("Structured schema must be an object")
        if type(function_call) is not bool or function_call and schema is None:
            raise ProviderError('A function proposal requires an explicit schema')
        # Native tokenizer + ample framing allowance; byte upper bound fallback
        # remains available in the dependency-free core. No hidden tool loops.
        input_bound = _input_token_bound(messages,schema)
        price, ledger = self.config.price(), self.config.ledger()
        reservation = ledger.reserve(price, input_bound, max_output_tokens, context=self.call_context)
        self.last_reservation = reservation
        self._publish_receipt(ledger, reservation)  # Persist ownership before HTTP.
        body = {"model": self.model, "messages": messages, "max_completion_tokens": max_output_tokens,
                "temperature": 0, "reasoning_effort": self.config.reasoning_effort, "include_reasoning": False,
                "response_format": {"type": "json_object"}, "stream": False}
        if schema is not None:
            body["response_format"] = {"type":"json_schema", "json_schema":{"name":"issueintel_response","strict":True,"schema":schema}}
        if function_call:
            body.pop('response_format')
            body['tools']=[{'type':'function','function':{'name':'issueintel_response',
                'description':'Submit one response object for local validation; the external program decides whether to execute any action.',
                'parameters':schema}}]
            body['tool_choice']={'type':'function','function':{'name':'issueintel_response'}}
            body['parallel_tool_calls']=False
        request = Request("https://api.groq.com/openai/v1/chat/completions", json.dumps(body).encode("utf-8"),
            {"Content-Type": "application/json", "Authorization": f"Bearer {key}",
             "User-Agent": "IssueIntel/0.1 (+local research)"}, method="POST")
        phase, response_status, response_size, content_type = 'request', None, None, 'unknown'
        try:
            with self.transport(request, timeout=60) as response:
                phase = 'response_read'
                response_status = getattr(response, 'status', None)
                headers = getattr(response, "headers", {}) or {}
                media_type = headers.get('content-type', '').split(';', 1)[0].strip().lower()
                content_type = ('json' if media_type in {'application/json', 'application/problem+json'} else
                    'html' if media_type == 'text/html' else 'text' if media_type == 'text/plain' else
                    'other' if media_type else 'unknown')
                payload = response.read(4_000_001)
                response_size = len(payload)
                self.last_rate_limits = {name:headers.get(name) for name in
                    ("x-ratelimit-limit-requests", "x-ratelimit-limit-tokens", "x-ratelimit-remaining-requests", "x-ratelimit-remaining-tokens", "x-ratelimit-reset-requests", "x-ratelimit-reset-tokens") if headers.get(name) is not None}
            if len(payload) > 4_000_000:
                raise ProviderError("Provider response exceeded size limit", code='response_size_limit')
            phase = 'response_json'
            result = json.loads(payload)
            if not isinstance(result, dict):
                raise ValueError('Provider response envelope must be an object')
        except Exception as error:
            ledger.reconcile(reservation, price, None, failed=True)
            self._publish_receipt(ledger, reservation)
            # Do not expose response bodies, request headers, key or source text.
            status = error.code if isinstance(error, HTTPError) else (
                type(error).__name__ if type(error).__name__ in _TRANSPORT_TYPES else 'other')
            error_code = error.code if isinstance(error, ProviderError) and error.code in _ERROR_CODES else None
            diagnostics = _transport_diagnostics(error, phase, status=response_status,
                byte_count=response_size, content_type=content_type)
            if isinstance(error,HTTPError):
                try:
                    info = json.loads(error.read(64000)).get("error", {})
                    diagnostics.update(_output_diagnostics(info))
                    value = info.get("code") or info.get("type")
                    if isinstance(value,str) and value in _ERROR_CODES:
                        error_code = value
                except (ValueError,TypeError,AttributeError):
                    pass
            retry_after = error.headers.get("retry-after") if isinstance(error, HTTPError) else None
            try:
                retry_after = float(retry_after) if retry_after is not None else None
            except ValueError:
                retry_after = None
            detail = f", {error_code}" if error_code else ""
            raise ProviderError(f"Groq call failed ({status}{detail}); bound retained, no automatic retry", status=status,retry_after=retry_after,code=error_code,diagnostics=diagnostics) from None
        self.last_usage = result.get("usage")
        try:
            ledger.reconcile(reservation, price, self.last_usage)
        finally:
            self._publish_receipt(ledger, reservation)
        try:
            choice = result["choices"][0]
            if function_call:
                calls=choice['message'].get('tool_calls',[])
                if (choice.get('finish_reason')!='tool_calls' or len(calls)!=1
                        or calls[0].get('type')!='function' or calls[0]['function']['name']!='issueintel_response'):
                    raise ValueError('Expected exactly one allowed function proposal')
                content=json.loads(calls[0]['function']['arguments'])
            else:
                if choice.get("finish_reason") != "stop" or choice['message'].get('tool_calls'):
                    raise ValueError("incomplete output")
                content = json.loads(choice["message"]["content"])
            if not isinstance(content, dict):
                raise ValueError("expected JSON object")
            return content
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise ProviderError("Provider did not return a complete JSON object", code='incomplete_json_output') from None
