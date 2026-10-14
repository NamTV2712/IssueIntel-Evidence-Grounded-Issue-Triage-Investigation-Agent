"""Read-only acquisition tests use synthetic local Git and mocked HTTP."""
from __future__ import annotations

import io
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from issueintel.repository import acquire


class AcquisitionTests(unittest.TestCase):
    def test_github_reference_accepts_only_constrained_public_identity(self):
        source = acquire.parse_github_reference("https://github.com/pytest-dev/pytest/issues/123")
        self.assertEqual(source.repository, "pytest-dev/pytest")
        self.assertEqual(source.issue_number, 123)
        self.assertEqual(acquire.parse_github_reference("pytest-dev/pytest.git").repository, "pytest-dev/pytest")
        bad = ("https://user:secret@github.com/a/b", "http://github.com/a/b", "https://evil.test/a/b",
               "https://github.com/a/b?token=secret", "https://github.com/a/b#fragment", "../outside",
               "https://github.com/a/b/../escape", "https://github.com/a/b%2f..", "-option/repo",
               "owner/repo/issues/1", "git@github.com:a/b", "https://github.com/a/b/issues/0")
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                acquire.parse_github_reference(value)

    def test_shallow_exact_fetch_and_cache_reuse_without_checkout(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            origin = root / "origin"
            origin.mkdir()
            def git(*args):
                return subprocess.run(["git", "-C", str(origin), *args], check=True,
                                      capture_output=True).stdout.decode().strip()
            git("init", "-q")
            git("config", "user.name", "Synthetic")
            git("config", "user.email", "synthetic@example.test")
            (origin / "code.py").write_text("raise RuntimeError('never execute')\n", encoding="utf-8")
            git("add", ".")
            git("commit", "-q", "-m", "base")
            commit = git("rev-parse", "HEAD")
            (origin / "code.py").write_text("raise RuntimeError('future')\n", encoding="utf-8")
            git("add", ".")
            git("commit", "-q", "-m", "future")
            calls = []
            def runner(command, **kwargs):
                calls.append(command[:])
                rewritten = command[:]
                if "fetch" in command:
                    rewritten[rewritten.index("protocol.file.allow=never")] = "protocol.file.allow=always"
                    rewritten[-2] = origin.as_uri()
                return subprocess.run(rewritten, **kwargs)
            repository = acquire.acquire_repository("owner/project", commit, root / "cache", runner=runner)
            self.assertEqual(repository.commit_sha, commit)
            self.assertFalse((repository.path / "code.py").exists())
            self.assertTrue((repository.path / "shallow").exists())
            fetch = next(command for command in calls if "fetch" in command)
            self.assertIn("--depth=1", fetch)
            self.assertIn("--no-recurse-submodules", fetch)
            self.assertIn("credential.helper=", fetch)
            self.assertEqual(fetch[-2:], ["origin", commit])
            calls.clear()
            again = acquire.acquire_repository("owner/project", commit, root / "cache", runner=runner)
            self.assertEqual(again.commit_sha, commit)
            self.assertFalse(any("fetch" in command for command in calls))
            self.assertEqual(json.loads(json.dumps(again.to_dict()))["repository"], "owner/project")

    def test_revision_options_and_implicit_latest_are_rejected(self):
        for revision in (None, "", "-option", "refs/../evil", "HEAD~1", ":evil", "refs/heads/main:evil", "main\n--depth=0"):
            with self.subTest(revision=revision), self.assertRaises(ValueError):
                acquire.acquire_repository("owner/project", revision)

    def test_timeout_and_error_output_do_not_leak_credentials(self):
        with TemporaryDirectory() as temp:
            def timeout(command, **kwargs):
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])
            with self.assertRaisesRegex(TimeoutError, "configured timeout"):
                acquire.acquire_repository("owner/project", "a" * 40, temp, runner=timeout)
            def error(command, **kwargs):
                return subprocess.CompletedProcess(command, 7, b"", b"super-secret-token")
            with self.assertRaises(ValueError) as context:
                acquire.acquire_repository("owner/project", "a" * 40, temp, runner=error)
            self.assertNotIn("super-secret-token", str(context.exception))
            self.assertFalse(list(Path(temp).glob("pending-*")))

    def test_single_issue_get_records_current_text_limitation(self):
        payload = {"html_url": "https://github.com/owner/project/issues/7", "number": 7,
                   "title": "bug", "body": "symptom", "created_at": "2026-01-01T00:00:00Z"}
        raw = json.dumps(payload).encode()
        with patch.object(acquire, "_open_request", return_value=io.BytesIO(raw)) as opener:
            issue = acquire.fetch_github_issue(payload["html_url"], token="synthetic-token")
        request = opener.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.full_url, "https://api.github.com/repos/owner/project/issues/7")
        self.assertEqual(issue["problem_statement"], "bug\n\nsymptom")
        self.assertFalse(issue["provenance"]["body_version_at_creation_verified"])
        self.assertNotIn("synthetic-token", json.dumps(issue))

    def test_issue_limits_pr_records_and_mismatched_identity_fail_closed(self):
        with patch.object(acquire, "_open_request", return_value=io.BytesIO(b"a" * 11)):
            with self.assertRaisesRegex(ValueError, "size limit"):
                acquire.fetch_github_issue("https://github.com/owner/project/issues/7", max_response_bytes=10)
        for payload in ({"pull_request": {"url": "https://api.github.com/pulls/7"}},
                        {"html_url": "https://github.com/other/project/issues/7"}):
            with patch.object(acquire, "_open_request", return_value=io.BytesIO(json.dumps(payload).encode())):
                with self.assertRaises(ValueError):
                    acquire.fetch_github_issue("https://github.com/owner/project/issues/7")
        handler = acquire._TrustedGitHubRedirect()
        with self.assertRaisesRegex(ValueError, "trusted origin"):
            handler.redirect_request(None, None, 302, "redirect", {}, "https://evil.test/steal")


if __name__ == "__main__":
    unittest.main()
