"""Atomic, restart-safe bounds for paid calls. No implicit unlimited setting."""
from __future__ import annotations

import json
import math
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from typing import Callable


class BudgetDenied(RuntimeError):
    pass


def positive_decimal(value: object, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except Exception as error:
        raise BudgetDenied(f"Invalid {field}") from error
    if not result.is_finite() or result <= 0:
        raise BudgetDenied(f"{field} must be a finite positive amount")
    return result


@dataclass(frozen=True)
class BudgetPolicy:
    daily_total_tokens: int | None
    monthly_usd: Decimal | None
    daily_usd: Decimal | None = None
    daily_requests: int | None = None
    tokens_unlimited_authorized: bool = False
    usd_unlimited_authorized: bool = False

    def __post_init__(self):
        if type(self.tokens_unlimited_authorized) is not bool or type(self.usd_unlimited_authorized) is not bool:
            raise BudgetDenied("Explicit authorization flags must be boolean")
        if self.daily_total_tokens is None and not self.tokens_unlimited_authorized:
            raise BudgetDenied("Missing token limit without explicit authorization")
        if self.daily_total_tokens is not None and (type(self.daily_total_tokens) is not int or self.daily_total_tokens <= 0):
            raise BudgetDenied("daily_total_tokens must be a positive integer")
        if self.monthly_usd is None and not self.usd_unlimited_authorized:
            raise BudgetDenied("Missing USD limit without explicit authorization")
        if self.monthly_usd is not None:
            positive_decimal(self.monthly_usd, "monthly_usd")
        if self.daily_usd is not None:
            positive_decimal(self.daily_usd, "daily_usd")
        if self.daily_requests is not None and (type(self.daily_requests) is not int or self.daily_requests <= 0):
            raise BudgetDenied("daily_requests must be positive when supplied")


@dataclass(frozen=True)
class ModelPrice:
    model: str
    input_per_million: Decimal
    output_per_million: Decimal
    checked_at: str
    source: str
    context_tokens: int
    max_output_tokens: int

    @classmethod
    def load(cls, path: Path | str, model: str, *, today: date | None = None):
        today = today or datetime.now(timezone.utc).date()
        try:
            facts = json.loads(Path(path).read_text(encoding="utf-8"))
            checked = date.fromisoformat(facts["checked_at"])
            row = facts["models"][model]
            if facts["currency"] != "USD" or not facts["source"].startswith("https://console.groq.com/"):
                raise ValueError("unsupported price provenance")
            if not 0 <= (today - checked).days <= 30:
                raise ValueError("price facts need reverification (30-day window)")
            price = cls(model, positive_decimal(row["input_per_million"], "input price"),
                        positive_decimal(row["output_per_million"], "output price"),
                        checked.isoformat(), facts["source"], row["context_tokens"], row["max_output_tokens"])
            if type(price.context_tokens) is not int or type(price.max_output_tokens) is not int:
                raise ValueError("invalid model limits")
            return price
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise BudgetDenied(f"Missing or invalid dated price for {model}") from error

    def micro_usd(self, input_tokens: int, output_tokens: int) -> int:
        # Per-million USD and micro-USD cancel; round up, never float down.
        return int((input_tokens * self.input_per_million + output_tokens * self.output_per_million).to_integral_value(rounding=ROUND_CEILING))


class BudgetLedger:
    def __init__(self, path: Path | str, policy: BudgetPolicy, *, clock: Callable | None = None):
        self.path, self.policy = Path(path), policy
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS calls (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, day TEXT NOT NULL, month TEXT NOT NULL, model TEXT NOT NULL, input_bound INTEGER NOT NULL, output_bound INTEGER NOT NULL, charged_tokens INTEGER NOT NULL, charged_micro_usd INTEGER NOT NULL, status TEXT NOT NULL, price_date TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS call_context (id TEXT PRIMARY KEY, category TEXT NOT NULL, stage TEXT NOT NULL, reasoning_effort TEXT NOT NULL, escalation_reason TEXT, diagnostic INTEGER NOT NULL, input_tokens INTEGER, output_tokens INTEGER)")
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise BudgetDenied("Budget ledger integrity failure")

    @contextmanager
    def _connect(self):
        connection = None
        try:
            connection = sqlite3.connect(self.path, timeout=30)
            connection.execute("PRAGMA busy_timeout=30000")
            with connection:
                yield connection
        except sqlite3.Error as error:
            raise BudgetDenied("Budget ledger unavailable") from error
        finally:
            if connection is not None:
                connection.close()

    def reserve(self, price: ModelPrice, input_bound: int, output_bound: int, *, context=None) -> str:
        if context is not None:
            if (context.get("category") != "ARTIFACT_GENERATION_COST"
                    or context.get("reasoning_effort") not in {"medium", "high"}
                    or not isinstance(context.get("stage"), str)):
                raise BudgetDenied("Invalid routed call accounting context")
        if any(type(value) is not int or value <= 0 for value in (input_bound, output_bound)):
            raise BudgetDenied("Invalid token reservation")
        if input_bound + output_bound > price.context_tokens or output_bound > price.max_output_tokens:
            raise BudgetDenied("Reservation exceeds model context/output limits")
        now = self.clock().astimezone(timezone.utc)
        day, month = now.date().isoformat(), now.strftime("%Y-%m")
        tokens, cost = input_bound + output_bound, price.micro_usd(input_bound, output_bound)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM calls WHERE status='breach' LIMIT 1").fetchone():
                raise BudgetDenied("Provider usage exceeded a bound; ledger is locked for review")
            used_tokens, daily_cost, requests = connection.execute("SELECT COALESCE(SUM(charged_tokens),0),COALESCE(SUM(charged_micro_usd),0),COUNT(*) FROM calls WHERE day=?", (day,)).fetchone()
            monthly_cost = connection.execute("SELECT COALESCE(SUM(charged_micro_usd),0) FROM calls WHERE month=?", (month,)).fetchone()[0]
            if self.policy.daily_total_tokens is not None and used_tokens + tokens > self.policy.daily_total_tokens:
                raise BudgetDenied("Daily total token limit reached")
            if self.policy.monthly_usd is not None and Decimal(monthly_cost + cost) / 1000000 > self.policy.monthly_usd:
                raise BudgetDenied("Monthly USD limit reached")
            if self.policy.daily_usd is not None and Decimal(daily_cost + cost) / 1000000 > self.policy.daily_usd:
                raise BudgetDenied("Daily USD limit reached")
            if self.policy.daily_requests is not None and requests + 1 > self.policy.daily_requests:
                raise BudgetDenied("Daily request limit reached")
            reservation = uuid.uuid4().hex
            connection.execute("INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?,?)", (reservation, now.isoformat(), day, month, price.model, input_bound, output_bound, tokens, cost, "reserved", price.checked_at))
            if context is not None:
                connection.execute("INSERT INTO call_context VALUES (?,?,?,?,?,?,NULL,NULL)",
                    (reservation, context["category"], context["stage"], context["reasoning_effort"],
                     context.get("escalation_reason"), int(bool(context.get("diagnostic")))))
        return reservation

    def reconcile(self, reservation: str, price: ModelPrice, usage: dict | None, *, failed: bool = False):
        """Failures/missing usage retain the full bound; no optimistic refund."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT model,input_bound,output_bound,status FROM calls WHERE id=?", (reservation,)).fetchone()
            if row is None or row[3] != "reserved" or row[0] != price.model:
                raise BudgetDenied("Unknown, mismatched or settled reservation")
            if failed or not isinstance(usage, dict) or not usage:
                connection.execute("UPDATE calls SET status=? WHERE id=?", ("failed_bound" if failed else "missing_usage_bound", reservation))
                return
            prompt, completion, total = usage.get("prompt_tokens"), usage.get("completion_tokens"), usage.get("total_tokens")
            observed_overrun = (type(prompt) is int and prompt > row[1]) or (type(completion) is int and completion > row[2])
            if any(type(value) is not int or value < 0 for value in (prompt, completion, total)):
                connection.execute("UPDATE calls SET status=? WHERE id=?", ("breach" if observed_overrun else "missing_usage_bound", reservation))
                if not observed_overrun:
                    return
                anomaly = True
                breach = True
            elif prompt == 0 or total != prompt + completion:
                connection.execute("UPDATE calls SET status='breach' WHERE id=?", (reservation,))
                anomaly = True
                breach = observed_overrun
            else:
                anomaly = False
                breach = observed_overrun
            if not anomaly:
                connection.execute("UPDATE calls SET charged_tokens=?,charged_micro_usd=?,status=? WHERE id=?", (prompt + completion, price.micro_usd(prompt, completion), "breach" if breach else "settled", reservation))
                connection.execute("UPDATE call_context SET input_tokens=?,output_tokens=? WHERE id=?", (prompt, completion, reservation))
        if breach or anomaly:
            raise BudgetDenied("Provider reported usage above reserved bound; further calls blocked")

    def status(self) -> dict:
        now = self.clock().astimezone(timezone.utc)
        with self._connect() as connection:
            rows = connection.execute("SELECT day,month,charged_tokens,charged_micro_usd,status FROM calls").fetchall()
        return {"period_timezone": "UTC", "daily_token_limit": self.policy.daily_total_tokens,
                "monthly_usd_limit": str(self.policy.monthly_usd) if self.policy.monthly_usd is not None else None,
                "tokens_unlimited_authorized":self.policy.tokens_unlimited_authorized,
                "usd_unlimited_authorized":self.policy.usd_unlimited_authorized,
                "daily_tokens_charged_or_reserved": sum(r[2] for r in rows if r[0] == now.date().isoformat()),
                "monthly_usd_charged_or_reserved": str(Decimal(sum(r[3] for r in rows if r[1] == now.strftime("%Y-%m"))) / 1000000),
                "calls": len(rows), "locked": any(r[4] == "breach" for r in rows)}
