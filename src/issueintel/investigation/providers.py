from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler

from issueintel.runtime.budget import BudgetDenied
from issueintel.runtime.config import RuntimeConfig


class ProviderError(RuntimeError):
    def __init__(self, message, *, status=None, retry_after=None, code=None):
        super().__init__(message)
        self.status, self.retry_after, self.code = status, retry_after, code


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

    def complete_json(self, messages: list[dict], max_output_tokens: int = 2048, *, schema=None) -> dict:
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
            if isinstance(error,HTTPError):
                try:
                    info = json.loads(error.read(16000)).get("error", {})
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
            raise ProviderError(f"Groq call failed ({status}{detail}); bound retained, no automatic retry", status=status,retry_after=retry_after,code=error_code) from None
        self.last_usage = result.get("usage")
        try:
            ledger.reconcile(reservation, price, self.last_usage)
        finally:
            self._publish_receipt(ledger, reservation)
        try:
            choice = result["choices"][0]
            if choice.get("finish_reason") != "stop":
                raise ValueError("incomplete output")
            content = json.loads(choice["message"]["content"])
            if not isinstance(content, dict):
                raise ValueError("expected JSON object")
            return content
        except (ValueError, KeyError, IndexError, TypeError):
            raise ProviderError("Provider did not return a complete JSON object") from None
