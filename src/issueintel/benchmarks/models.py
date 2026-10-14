"""Normalize upstream rows without exposing reference solutions to investigators."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Mapping


def utc_timestamp(value: Any, *, optional: bool = False) -> str | None:
    """Upstream naive timestamps are documented UTC; they are PR timestamps."""
    if value is None or value == "":
        if optional:
            return None
        raise ValueError("Missing upstream pull-request timestamp")
    if isinstance(value, datetime):
        timestamp = value
    elif isinstance(value, date):
        timestamp = datetime.combine(value, datetime.min.time())
    else:
        timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def string_list(value: Any, field_name: str) -> list[str]:
    """Support both original JSON-string columns and current Arrow list columns."""
    if value is None or value == "":
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError(f"{field_name} must be a list or a JSON list") from error
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be a list")
    return [str(item) for item in value]


def _required_text(row: Mapping[str, Any], name: str) -> str:
    value = row.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing or invalid upstream field: {name}")
    return value


@dataclass(frozen=True)
class UpstreamRecord:
    """One source instance, with intentionally separate public and gold projections."""

    source: str
    source_revision: str
    instance_id: str
    repository: str
    revision: str
    problem_statement: str
    pull_created_at: str
    issue_numbers: tuple[str, ...] = ()
    issue_url: str | None = None
    issue_created_at: str | None = None
    parent_instance_id: str | None = None
    gold: Mapping[str, Any] = field(default_factory=dict, repr=False)

    @property
    def case_id(self) -> str:
        return f"{self.source}:{self.instance_id}"

    @classmethod
    def from_mapping(
        cls, row: Mapping[str, Any], *, source: str, source_revision: str
    ) -> "UpstreamRecord":
        if source not in {"verified", "live"}:
            raise ValueError(f"Unknown benchmark source: {source}")
        instance_id = _required_text(row, "instance_id")
        repository = _required_text(row, "repo")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("Invalid repository owner/name")
        revision = _required_text(row, "base_commit")
        if not re.fullmatch(r"[0-9a-fA-F]{40}", revision):
            raise ValueError("base_commit must be a full Git SHA")
        issue_numbers = tuple(string_list(row.get("issue_numbers"), "issue_numbers"))
        if any(not number.isdigit() for number in issue_numbers):
            raise ValueError("issue_numbers must contain positive issue numbers")
        if any(int(number) <= 0 for number in issue_numbers):
            raise ValueError("issue_numbers must contain positive issue numbers")
        issue_url = row.get("issue_url")
        if issue_url is not None and not isinstance(issue_url, str):
            raise ValueError("issue_url must be a string")
        if not issue_url and len(issue_numbers) == 1:
            issue_url = f"https://github.com/{repository}/issues/{issue_numbers[0]}"
        evaluation_fields = (
            "version", "environment_setup_commit", "eval_type", "image", "image_key",
            "log_parser", "eval_script", "test_cmds", "commit_url", "commit_urls",
        )
        gold = {
            "patch": str(row.get("patch") or ""),
            "test_patch": str(row.get("test_patch") or ""),
            "fail_to_pass": string_list(row.get("FAIL_TO_PASS"), "FAIL_TO_PASS"),
            "pass_to_pass": string_list(row.get("PASS_TO_PASS"), "PASS_TO_PASS"),
            "hints_text": str(row.get("hints_text") or ""),
            "all_hints_text": str(row.get("all_hints_text") or ""),
            "difficulty": row.get("difficulty"),
            "evaluation": {key: row[key] for key in evaluation_fields if key in row},
        }
        return cls(
            source=source,
            source_revision=source_revision,
            instance_id=instance_id,
            repository=repository,
            revision=revision,
            problem_statement=_required_text(row, "problem_statement"),
            # Neither source's created_at field is an issue creation timestamp.
            pull_created_at=utc_timestamp(row.get("created_at")),
            issue_numbers=issue_numbers,
            issue_url=issue_url or None,
            issue_created_at=utc_timestamp(row.get("issue_created_at"), optional=True),
            parent_instance_id=row.get("parent_instance_id"),
            gold=gold,
        )

    def public_projection(self, split: str) -> dict[str, Any]:
        if split not in {"main_dev", "main_test", "temporal_candidate"}:
            raise ValueError(f"Unknown benchmark split: {split}")
        return {
            "case_id": self.case_id,
            "instance_id": self.instance_id,
            "repository": self.repository,
            "revision": self.revision,
            "problem_statement": self.problem_statement,
            "issue_url": self.issue_url,
            "issue_created_at": self.issue_created_at,
            "pull_created_at": self.pull_created_at,
            "issue_numbers": list(self.issue_numbers),
            "split": split,
        }

    def gold_projection(self) -> dict[str, Any]:
        return {"case_id": self.case_id, "instance_id": self.instance_id, **self.gold}
