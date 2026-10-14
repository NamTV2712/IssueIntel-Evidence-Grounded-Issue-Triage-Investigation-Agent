from dataclasses import dataclass
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import tomllib
import json

from .budget import BudgetDenied, BudgetLedger, BudgetPolicy, ModelPrice, positive_decimal


@dataclass(frozen=True)
class RuntimeConfig:
    root: Path
    model: str
    max_output_tokens: int
    policy: BudgetPolicy
    price_file: Path
    ledger_file: Path
    reasoning_effort: str = "low"

    @classmethod
    def routed(cls, root, route):
        """Explicit new authorization; existing recipe defaults stay unchanged."""
        if route.model is None:
            raise BudgetDenied("Deterministic eligibility must not construct a provider")
        root = Path(root).resolve()
        authorization = json.loads((root / "configs/strict_dev_authorization.json").read_text(encoding="utf-8"))
        parent = json.loads((root / "configs/author_decisions.json").read_text(encoding="utf-8"))
        if (authorization.get("scope") != "necessary_issueintel_strict_dev_work"
                or authorization.get("provider") != "groq" or route.model not in authorization.get("models", [])
                or authorization.get("model_routing_version") != route.version
                or authorization.get("request_attachment_sha256") in {None, "pending_recording"}
                or authorization.get("evaluation_authorized") is not False):
            raise BudgetDenied("Route has no matching explicit author authorization")
        for flag in ("tokens_unlimited_authorized", "usd_unlimited_authorized"):
            if authorization.get(flag) is not True or parent.get(flag) is not True:
                raise BudgetDenied("Unlimited route flags lack matching author decisions")
        if route.reasoning_effort not in {"medium", "high"}:
            raise BudgetDenied("Invalid routed effort")
        base = cls.load(root)
        return replace(base, model=route.model, reasoning_effort=route.reasoning_effort,
                       price_file=root / "configs/strict_dev_prices.json")

    @classmethod
    def load(cls, root: Path | str):
        root = Path(root).resolve()
        try:
            row = tomllib.loads((root / "configs/runtime.toml").read_text(encoding="utf-8"))
            if row["provider"] != "groq" or row["model"] != "openai/gpt-oss-120b":
                raise BudgetDenied("Provider/model has no recorded spending authorization")
            output = row["max_output_tokens"]
            if type(output) is not int or output <= 0:
                raise BudgetDenied("Invalid max_output_tokens")
            tokens_unlimited = row.get("tokens_unlimited_authorized",False)
            usd_unlimited = row.get("usd_unlimited_authorized",False)
            if tokens_unlimited or usd_unlimited:
                decisions = json.loads((root / "configs/author_decisions.json").read_text(encoding="utf-8"))
                if decisions.get("model") != row["model"] or decisions.get("scope") != "necessary_issueintel_rebuild_work":
                    raise BudgetDenied("Unlimited configuration lacks a matching author decision")
                if tokens_unlimited and decisions.get("tokens_unlimited_authorized") is not True:
                    raise BudgetDenied("No author authorization to remove token cap")
                if usd_unlimited and decisions.get("usd_unlimited_authorized") is not True:
                    raise BudgetDenied("No author authorization to remove USD cap")
            policy = BudgetPolicy(row.get("daily_total_tokens"), positive_decimal(row["monthly_usd"], "monthly_usd") if "monthly_usd" in row else None,
                positive_decimal(row["daily_usd"], "daily_usd") if "daily_usd" in row else None, row.get("daily_requests"),
                tokens_unlimited, usd_unlimited)
            paths = [(root / row[key]).resolve() for key in ("price_file", "ledger")]
            if any(not path.is_relative_to(root) for path in paths):
                raise BudgetDenied("Runtime paths must stay inside the project")
            return cls(root, row["model"], output, policy, *paths)
        except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError) as error:
            raise BudgetDenied("Runtime limits are missing or invalid") from error

    def ledger(self):
        return BudgetLedger(self.ledger_file, self.policy)

    def price(self):
        return ModelPrice.load(self.price_file, self.model)
