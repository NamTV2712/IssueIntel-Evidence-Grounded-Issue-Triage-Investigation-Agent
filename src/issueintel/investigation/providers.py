from __future__ import annotations

import json
import os
import math
from hashlib import sha256
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler

from issueintel.runtime.budget import BudgetDenied
from issueintel.runtime.config import RuntimeConfig


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
        codes = {'json_validate_failed', 'tool_use_failed', 'incomplete_json_output',
                 'rate_limit_exceeded', 'invalid_api_key', 'invalid_request_error',
                 'model_decommissioned', 'context_length_exceeded', 'model_not_found',
                 'insufficient_quota'}
        row.update(http_status=error.status if type(error.status) is int and 100 <= error.status <= 599 else None,
                   provider_code=error.code if isinstance(error.code,str) and error.code in codes else 'unknown',
                   terminal_output_error=output_rejected(error))
        delay = error.retry_after
        row['retry_after_seconds'] = delay if type(delay) in (int, float) and 0 <= delay <= 86400 and math.isfinite(delay) else None
        for key in ('failed_generation_present','failed_generation_is_json_object','native_call_marker','token_limit_hint'):
            if type(error.diagnostics.get(key)) is bool:row[key]=error.diagnostics[key]
        checksum=error.diagnostics.get('failed_generation_sha256')
        if isinstance(checksum,str) and re.fullmatch('[a-f0-9]{64}',checksum):row['failed_generation_sha256']=checksum
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
        raise ProviderError("Provider redirects are forbidden")


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
        try:
            with self.transport(request, timeout=60) as response:
                payload = response.read(4_000_001)
                headers = getattr(response, "headers", {})
                self.last_rate_limits = {name:headers.get(name) for name in
                    ("x-ratelimit-limit-requests", "x-ratelimit-limit-tokens", "x-ratelimit-remaining-requests", "x-ratelimit-remaining-tokens", "x-ratelimit-reset-requests", "x-ratelimit-reset-tokens") if headers.get(name) is not None}
            if len(payload) > 4_000_000:
                raise ProviderError("Provider response exceeded size limit")
            result = json.loads(payload)
        except Exception as error:
            ledger.reconcile(reservation, price, None, failed=True)
            self._publish_receipt(ledger, reservation)
            # Do not expose response bodies, request headers, key or source text.
            status = error.code if isinstance(error, HTTPError) else type(error).__name__
            error_code = None
            diagnostics = None
            if isinstance(error,HTTPError):
                try:
                    info = json.loads(error.read(64000)).get("error", {})
                    diagnostics = _output_diagnostics(info)
                    value = info.get("code") or info.get("type")
                    if isinstance(value,str) and len(value) < 100 and all(character.isalnum() or character in "_-" for character in value):
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
                if choice.get("finish_reason") != "stop":
                    raise ValueError("incomplete output")
                content = json.loads(choice["message"]["content"])
            if not isinstance(content, dict):
                raise ValueError("expected JSON object")
            return content
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise ProviderError("Provider did not return a complete JSON object", code='incomplete_json_output') from None
