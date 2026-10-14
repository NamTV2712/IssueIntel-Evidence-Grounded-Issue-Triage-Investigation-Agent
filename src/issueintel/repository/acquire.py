"""Explicit, read-only GitHub acquisition; never crawl whole issue histories.

Repositories are bare caches of requested shallow revisions. No working tree,
target imports, hooks or tests are executed. Caches can be deleted and fetched
again; annotation and evaluation artifacts must be stored separately.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


@dataclass(frozen=True)
class GitHubReference:
    owner: str
    repo: str
    issue_number: int | None = None

    @property
    def repository(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def repository_url(self) -> str:
        return f"https://github.com/{self.repository}"

    @property
    def issue_url(self) -> str | None:
        return f"{self.repository_url}/issues/{self.issue_number}" if self.issue_number else None

    def to_dict(self) -> dict:
        return {**asdict(self), "repository": self.repository,
                "repository_url": self.repository_url, "issue_url": self.issue_url}


def parse_github_reference(reference: str) -> GitHubReference:
    if not isinstance(reference, str) or reference != reference.strip() or not reference:
        raise ValueError("Require a GitHub HTTPS URL or owner/name")
    if any(ord(char) < 32 for char in reference) or "\\" in reference or "%" in reference:
        raise ValueError("Unsafe GitHub reference")
    has_url = "://" in reference
    if has_url:
        parsed = urlparse(reference)
        if (parsed.scheme != "https" or parsed.netloc.lower() != "github.com"
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("Only credential-free github.com HTTPS URLs are allowed")
        parts = parsed.path.strip("/").split("/")
    else:
        parts = reference.split("/")
    issue_number = None
    if has_url and len(parts) == 4 and parts[2] == "issues" and re.fullmatch(r"[1-9][0-9]*", parts[3]):
        issue_number = int(parts[3])
    elif len(parts) != 2:
        raise ValueError("Require a repository URL or a single GitHub issue URL")
    owner, repo = parts[:2]
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", owner):
        raise ValueError("Invalid GitHub owner")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", repo) or repo in {".", ".."} or repo.startswith("-"):
        raise ValueError("Invalid GitHub repository name")
    return GitHubReference(owner, repo, issue_number)


def _validate_revision(revision: str) -> str:
    if (not isinstance(revision, str) or not re.fullmatch(r"[A-Za-z0-9_./-]+", revision)
            or revision.startswith(("-", "/")) or revision.endswith(("/", ".lock"))
            or ".." in revision or "//" in revision
            or any(part == "." for part in revision.split("/"))):
        raise ValueError("Require an explicit safe commit SHA or Git ref; no implicit latest revision")
    return revision


@dataclass(frozen=True)
class AcquiredRepository:
    path: Path
    repository: str
    commit_sha: str
    requested_revision: str
    source_url: str
    acquired_at: str
    acquisition: str = "bare_shallow_requested_revision"

    def to_dict(self) -> dict:
        return {**asdict(self), "path": str(self.path)}


def _run_git(path: Path, arguments: list[str], timeout: int, runner=None,
             *, allow_failure: bool = False):
    environment = os.environ.copy()
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    command = ["git", "-c", "core.hooksPath=", "-c", "credential.helper=",
               "-c", "http.extraHeader=", "-c", "protocol.file.allow=never",
               "-C", str(path), *arguments]
    try:
        result = (runner or subprocess.run)(command, capture_output=True, check=False,
                                             timeout=timeout, env=environment)
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("Repository acquisition exceeded its configured timeout") from exc
    if result.returncode and not allow_failure:
        # Never include stderr: global Git configuration or a credential helper
        # could accidentally place sensitive account data in its error message.
        raise ValueError(f"Git repository acquisition failed (exit {result.returncode})")
    return result


def acquire_repository(reference: str, revision: str, cache_dir: str | Path | None = None,
                       *, timeout: int = 120, runner=None) -> AcquiredRepository:
    """Fetch only an explicitly requested revision into a reusable bare cache.

    Default cache location is ``./data/repos`` from the project working
    directory. Historical requests should pass a full commit SHA. Named refs
    are allowed only when explicitly supplied and resolve to a recorded SHA.
    Publication availability is not inferred from the fetch or Git dates.
    ``runner`` is an injectable subprocess.run-compatible test seam.
    """
    source = parse_github_reference(reference)
    revision = _validate_revision(revision)
    if timeout <= 0:
        raise ValueError("Acquisition timeout must be positive")
    cache = (Path(cache_dir) if cache_dir is not None else Path.cwd() / "data/repos").resolve()
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / f"{source.owner.lower()}__{source.repo.lower()}.git"
    if target.resolve().parent != cache:
        raise ValueError("Repository cache escaped the intended directory")
    remote = source.repository_url + ".git"
    if not target.exists():
        staging = Path(tempfile.mkdtemp(prefix="pending-", dir=cache))
        try:
            _run_git(staging, ["init", "--bare", "--quiet", "--template="], timeout, runner)
            _run_git(staging, ["remote", "add", "origin", remote], timeout, runner)
            staging.replace(target)
        finally:
            if staging.exists():
                if staging.resolve().parent != cache or staging.is_symlink():
                    raise ValueError("Unsafe temporary repository path")
                shutil.rmtree(staging)
    if _run_git(target, ["rev-parse", "--is-bare-repository"], timeout, runner).stdout.strip() != b"true":
        raise ValueError("Cache must be a bare repository")
    configured = _run_git(target, ["config", "--get", "remote.origin.url"], timeout, runner).stdout.decode("utf-8").strip()
    if configured.lower() != remote.lower():
        raise ValueError("Cached repository origin differs from the requested source")
    requested_sha = bool(re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", revision))
    cached = (_run_git(target, ["cat-file", "-e", revision + "^{commit}"], timeout, runner,
                       allow_failure=True).returncode == 0) if requested_sha else False
    if not cached:
        _run_git(target, ["fetch", "--depth=1", "--no-tags", "--no-recurse-submodules",
                          "--", "origin", revision], timeout, runner)
    selected = revision if requested_sha else "FETCH_HEAD"
    commit = _run_git(target, ["rev-parse", "--verify", "--end-of-options", selected + "^{commit}"],
                      timeout, runner).stdout.decode("ascii").strip()
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
        raise ValueError("Git did not resolve a full commit object ID")
    if requested_sha and commit != revision.lower():
        raise ValueError("Fetched commit differs from the explicitly requested SHA")
    _run_git(target, ["update-ref", "refs/issueintel/" + commit, commit], timeout, runner)
    return AcquiredRepository(target, source.repository, commit, revision, remote,
                              datetime.now(timezone.utc).isoformat())


ensure_repository = acquire_repository


class _TrustedGitHubRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        parsed = urlparse(new_url)
        if (parsed.scheme != "https" or parsed.netloc.lower() != "api.github.com"
                or parsed.username or parsed.password):
            raise ValueError("Refuse a GitHub API redirect outside the trusted origin")
        return super().redirect_request(request, response, code, message, headers, new_url)


def _open_request(request: Request, timeout: int):
    return build_opener(_TrustedGitHubRedirect()).open(request, timeout=timeout)


def open_github_request(request: Request, timeout: int = 30):
    """Shared GET transport that never redirects credentials outside api.github.com."""
    parsed = urlparse(request.full_url)
    if parsed.scheme != "https" or parsed.netloc.lower() != "api.github.com" or request.get_method() != "GET":
        raise ValueError("Only explicit GitHub API GET requests are allowed")
    return _open_request(request, timeout)


def fetch_github_issue(reference: str, *, token: str | None = None, timeout: int = 30,
                       max_response_bytes: int = 2000000) -> dict:
    """Fetch exactly one public issue; current body is not a historical version."""
    source = parse_github_reference(reference)
    if source.issue_number is None:
        raise ValueError("Require a single GitHub issue URL")
    if timeout <= 0 or max_response_bytes <= 0:
        raise ValueError("Issue request limits must be positive")
    token = os.environ.get("GITHUB_TOKEN", "") if token is None else token
    if any(ord(char) < 32 for char in token):
        raise ValueError("Invalid GitHub token header")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "IssueIntel-readonly"}
    if token:
        headers["Authorization"] = "Bearer " + token
    api_url = f"https://api.github.com/repos/{source.repository}/issues/{source.issue_number}"
    request = Request(api_url, headers=headers, method="GET")
    with _open_request(request, timeout) as response:
        raw = response.read(max_response_bytes + 1)
    if len(raw) > max_response_bytes:
        raise ValueError("GitHub issue response exceeds configured size limit")
    payload = json.loads(raw.decode("utf-8"))
    if payload.get("pull_request"):
        raise ValueError("Requested GitHub record is a pull request, not an issue")
    canonical = parse_github_reference(payload.get("html_url", ""))
    if canonical.repository.lower() != source.repository.lower() or canonical.issue_number != source.issue_number:
        raise ValueError("GitHub issue response belongs to a different repository or issue")
    title, body, created_at = payload.get("title"), payload.get("body") or "", payload.get("created_at")
    if not isinstance(title, str) or not isinstance(body, str) or not isinstance(created_at, str):
        raise ValueError("GitHub issue response is missing required text/timestamp fields")
    timestamp = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError("GitHub issue created_at must include a timezone")
    return {
        "repository": source.repository, "number": source.issue_number, "url": canonical.issue_url,
        "title": title, "body": body, "problem_statement": title + "\n\n" + body,
        "created_at": created_at,
        "provenance": {
            "source_url": api_url, "response_sha256": sha256(raw).hexdigest(),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "body_version_at_creation_verified": False,
            "limitation": "Current issue text may contain edits after created_at; no edit history was fetched.",
        },
    }
