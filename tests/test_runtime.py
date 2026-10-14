from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, date
from decimal import Decimal
from io import BytesIO
import json

import pytest

from issueintel.runtime.budget import BudgetDenied, BudgetLedger, BudgetPolicy, ModelPrice
from issueintel.runtime.config import RuntimeConfig
from issueintel.investigation.providers import GroqProvider, ProviderError, _NoCredentialRedirect


def price():
    return ModelPrice("openai/gpt-oss-120b", Decimal("0.15"), Decimal("0.60"), "2026-10-12", "https://console.groq.com/docs/models", 131072, 65536)


def ledger(tmp_path, tokens=1000, dollars="2"):
    return BudgetLedger(tmp_path / "budget.sqlite", BudgetPolicy(tokens, Decimal(dollars)),
                        clock=lambda: datetime(2026, 10, 12, tzinfo=timezone.utc))


def test_daily_bound_survives_restart_and_failures(tmp_path):
    first = ledger(tmp_path)
    reservation = first.reserve(price(), 500, 300)
    first.reconcile(reservation, price(), None, failed=True)
    with pytest.raises(BudgetDenied, match="Daily"):
        ledger(tmp_path).reserve(price(), 100, 101)


def test_atomic_concurrent_reservations(tmp_path):
    guard = ledger(tmp_path)
    def reserve():
        try:
            guard.reserve(price(), 100, 100)
            return True
        except BudgetDenied:
            return False
    with ThreadPoolExecutor(max_workers=10) as executor:
        allowed = list(executor.map(lambda _: reserve(), range(20)))
    assert sum(allowed) == 5
    assert guard.status()["daily_tokens_charged_or_reserved"] == 1000


def test_monthly_amount_and_rounding(tmp_path):
    guard = ledger(tmp_path, tokens=100000, dollars="0.00006")
    guard.reserve(price(), 100, 50)  # $0.000045
    with pytest.raises(BudgetDenied, match="Monthly"):
        guard.reserve(price(), 100, 50)
    assert price().micro_usd(1, 1) == 1


def test_daily_tokens_reset_but_monthly_cost_does_not(tmp_path):
    guard = ledger(tmp_path, dollars="0.0002")
    guard.reserve(price(), 500, 100)
    guard.clock = lambda: datetime(2026, 10, 13, tzinfo=timezone.utc)
    with pytest.raises(BudgetDenied, match="Monthly"):
        guard.reserve(price(), 500, 100)


def test_usage_settles_and_double_settlement_rejected(tmp_path):
    guard = ledger(tmp_path)
    reservation = guard.reserve(price(), 400, 300)
    guard.reconcile(reservation, price(), {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150})
    assert guard.status()["daily_tokens_charged_or_reserved"] == 150
    with pytest.raises(BudgetDenied, match="settled"):
        guard.reconcile(reservation, price(), {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})


def test_provider_bound_breach_locks_ledger(tmp_path):
    guard = ledger(tmp_path)
    reservation = guard.reserve(price(), 100, 100)
    with pytest.raises(BudgetDenied, match="above reserved"):
        guard.reconcile(reservation, price(), {"prompt_tokens": 101, "completion_tokens": 1, "total_tokens": 102})
    with pytest.raises(BudgetDenied, match="locked"):
        guard.reserve(price(), 1, 1)


@pytest.mark.parametrize("tokens,dollars", [(0,"2"),(-1,"2"),(True,"2"),(100,"0"),(100,"Infinity"),(100,"NaN")])
def test_missing_or_unlimited_policy_denied(tokens, dollars):
    with pytest.raises(BudgetDenied):
        BudgetPolicy(tokens, Decimal(dollars))


def test_missing_stale_price_denied(tmp_path):
    path = tmp_path / "prices.json"
    path.write_text(json.dumps({"checked_at": "2020-01-01", "currency": "USD", "source": "https://console.groq.com/docs/models", "models": {}}))
    with pytest.raises(BudgetDenied):
        ModelPrice.load(path, "unknown", today=date(2026,10,12))


def configured(tmp_path):
    path = tmp_path / "price.json"
    path.write_text(json.dumps({"checked_at": datetime.now(timezone.utc).date().isoformat(), "currency": "USD", "source": "https://console.groq.com/docs/models", "models": {price().model: {"input_per_million":"0.15","output_per_million":"0.60","context_tokens":131072,"max_output_tokens":65536}}}))
    return RuntimeConfig(tmp_path, price().model, 2048, BudgetPolicy(200000, Decimal("2")), path, tmp_path / "guard.sqlite")


def test_key_and_output_checks_before_network(tmp_path, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    provider = GroqProvider(configured(tmp_path), transport=lambda *a, **k: pytest.fail("network called"))
    with pytest.raises(BudgetDenied, match="GROQ_API_KEY"):
        provider.complete_json([{"role":"user","content":"hello"}])
    monkeypatch.setenv("GROQ_API_KEY", "test-local-key")
    with pytest.raises(BudgetDenied, match="per-call"):
        provider.complete_json([{"role":"user","content":"hello"}], 3000)


def test_provider_usage_and_json_response(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-local-key")
    config = configured(tmp_path)
    def response(request, **kwargs):
        body = json.loads(request.data)
        assert body["max_completion_tokens"] == 2048
        assert "tools" not in body and body["stream"] is False
        return BytesIO(json.dumps({"choices":[{"finish_reason":"stop","message":{"content":"{\"ok\":true}"}}],"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}).encode())
    assert GroqProvider(config, transport=response).complete_json([{"role":"user","content":"JSON"}]) == {"ok": True}
    assert config.ledger().status()["daily_tokens_charged_or_reserved"] == 15


def test_provider_failure_keeps_bound_and_redacts_error(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-local-key")
    config = configured(tmp_path)
    def fail(*a, **k):
        raise RuntimeError("test-local-key secret response")
    with pytest.raises(ProviderError) as caught:
        GroqProvider(config, transport=fail).complete_json([{"role":"user","content":"hello"}])
    assert "test-local-key" not in str(caught.value)
    assert config.ledger().status()["daily_tokens_charged_or_reserved"] > 2048


def test_inconsistent_usage_never_refunds_to_zero(tmp_path):
    guard = ledger(tmp_path)
    reservation = guard.reserve(price(), 400, 300)
    with pytest.raises(BudgetDenied):
        guard.reconcile(reservation, price(), {"prompt_tokens":0,"completion_tokens":0,"total_tokens":10000})
    assert guard.status()["daily_tokens_charged_or_reserved"] == 700
    assert guard.status()["locked"] is True
    # Connections have been closed; this must also work on Windows.
    guard.path.unlink()


def test_provider_redirect_never_forwards_credentials():
    with pytest.raises(ProviderError, match="redirects"):
        _NoCredentialRedirect().redirect_request(None, None, 302, "redirect", {}, "https://unrelated.example/")


def test_explicit_unlimited_authorization_is_distinct_from_missing_limits(tmp_path):
    with pytest.raises(BudgetDenied):
        BudgetPolicy(None,None)
    guard = BudgetLedger(tmp_path / "authorized.sqlite",BudgetPolicy(None,None,tokens_unlimited_authorized=True,usd_unlimited_authorized=True))
    for _ in range(3):
        guard.reserve(price(),100000,10000)
    assert guard.status()["daily_tokens_charged_or_reserved"] == 330000
    assert guard.status()["monthly_usd_limit"] is None


def test_unlimited_config_requires_matching_author_record(tmp_path):
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs/runtime.toml").write_text('provider="groq"\nmodel="openai/gpt-oss-120b"\nmax_output_tokens=2048\nprice_file="configs/prices.json"\nledger="data/ledger.sqlite"\ntokens_unlimited_authorized=true\nusd_unlimited_authorized=true\n')
    with pytest.raises(BudgetDenied):
        RuntimeConfig.load(tmp_path)
    (tmp_path / "configs/author_decisions.json").write_text(json.dumps({"model":"openai/gpt-oss-120b","scope":"necessary_issueintel_rebuild_work","tokens_unlimited_authorized":True,"usd_unlimited_authorized":True}))
    assert RuntimeConfig.load(tmp_path).policy.monthly_usd is None


def test_structured_output_schema_is_sent_and_reserved(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "synthetic-key")
    config = configured(tmp_path)
    def response(request, **kwargs):
        body=json.loads(request.data)
        assert body["response_format"]["type"] == "json_schema"
        assert body["response_format"]["json_schema"]["strict"] is True
        assert body["response_format"]["json_schema"]["schema"]["properties"]["id"]["enum"] == ["exact_evidence_id"]
        return BytesIO(json.dumps({"choices":[{"finish_reason":"stop","message":{"content":"{\"id\":\"exact_evidence_id\"}"}}],"usage":{"prompt_tokens":11,"completion_tokens":8,"total_tokens":19}}).encode())
    result=GroqProvider(config,transport=response).complete_structured([{"role":"user","content":"Return JSON"}],schema={"type":"object","properties":{"id":{"type":"string","enum":["exact_evidence_id"]}},"required":["id"],"additionalProperties":False})
    assert result["id"] == "exact_evidence_id"
