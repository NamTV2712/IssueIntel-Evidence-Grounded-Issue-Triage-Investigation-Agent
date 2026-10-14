"""Synthetic Git fixtures test isolation and semantics without target execution."""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import time
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

from issueintel.benchmarks.localization import extract_gold_localization
from issueintel.repository.graph import build_graph
from issueintel.repository import snapshot as snapshot_module
from issueintel.repository.snapshot import SourceFile, build_snapshot, validate_repo_path


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.email", "synthetic@example.test")
        self.git("config", "user.name", "Synthetic fixture")
        self.git("config", "core.autocrlf", "false")
        (self.repo / "pkg").mkdir()
        (self.repo / "pkg/__init__.py").write_text("", encoding="utf-8")
        (self.repo / "pkg/helpers.py").write_text(
            "def normalize(value):\n    return value.strip()\n", encoding="utf-8")
        (self.repo / "pkg/main.py").write_text(
            "from .helpers import normalize\n\nclass Base:\n    pass\n\n"
            "class Handler(Base):\n    def handle(self, value):\n        return normalize(value)\n",
            encoding="utf-8")
        (self.repo / "malformed.py").write_text("def broken(\n", encoding="utf-8")
        (self.repo / "binary.bin").write_bytes(b"\0opaque\xff")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "base")
        self.base = self.git("rev-parse", "HEAD").strip()

    def git(self, *args, data=None):
        environment = os.environ.copy()
        environment["GIT_AUTHOR_DATE"] = "2001-01-01T00:00:00+00:00"
        environment["GIT_COMMITTER_DATE"] = "2001-01-01T00:00:00+00:00"
        return subprocess.run(["git", "-C", str(self.repo), *args], input=data,
                              capture_output=True, check=True, env=environment).stdout.decode("utf-8")

    def snapshot(self, **kwargs):
        return build_snapshot(self.repo, self.base, **kwargs)

    def test_pinned_tree_ignores_working_tree_and_later_fix(self):
        (self.repo / "pkg/helpers.py").write_text(
            "def normalize(value):\n    return value.lower().strip()\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "later fix")
        snapshot = self.snapshot()
        code = next(file.text for file in snapshot.files if file.path == "pkg/helpers.py")
        self.assertNotIn("lower", code)
        self.assertEqual(snapshot.commit_sha, self.base)
        self.assertFalse(snapshot.availability["historical_certified"])
        self.assertIn(snapshot.committer_time, ("2001-01-01T00:00:00+00:00", "2001-01-01T00:00:00Z"))
        self.assertNotEqual(self.git("rev-parse", "HEAD").strip(), snapshot.commit_sha)
        snapshot.verify()

    def test_git_timestamp_does_not_authorize_strict_temporal_snapshot(self):
        with self.assertRaisesRegex(ValueError, "independently verified"):
            self.snapshot(protocol="issue_time", cutoff="2002-01-01T00:00:00Z")

    def test_availability_receipt_binds_revision_and_strict_cutoff(self):
        receipt = {"commit_sha": self.base, "verified": True,
                   "available_at": "2001-01-02T00:00:00Z", "source_id": "synthetic-publication-receipt"}
        snapshot = self.snapshot(protocol="issue_time", cutoff="2001-01-03T00:00:00Z", availability=receipt)
        self.assertTrue(snapshot.availability["historical_certified"])
        with self.assertRaisesRegex(ValueError, "strictly before"):
            self.snapshot(protocol="issue_time", cutoff=receipt["available_at"], availability=receipt)
        with self.assertRaisesRegex(ValueError, "different commit"):
            self.snapshot(availability={**receipt, "commit_sha": "a" * 40})
        with self.assertRaisesRegex(ValueError, "timezone"):
            self.snapshot(cutoff="2002-01-01")

    def test_self_consistent_snapshot_digest_does_not_override_temporal_invariants(self):
        snapshot = self.snapshot()
        def resign(**changes):
            changed = replace(snapshot, **changes)
            payload = changed.to_dict()
            payload.pop("snapshot_sha256")
            return replace(changed, snapshot_sha256=snapshot_module._digest(payload))
        for availability in (
            {"status": "unknown", "historical_certified": True},
            {"status": "caller_verified_receipt", "historical_certified": True},
            {"status": "caller_verified_receipt", "historical_certified": True,
             "source_id": "fake", "receipt_sha256": "a" * 64, "available_at": "2002-01-01T00:00:00Z"},
        ):
            with self.subTest(availability=availability), self.assertRaises(ValueError):
                resign(protocol="issue_time", cutoff="2002-01-01T00:00:00Z", availability=availability).verify()
        with self.assertRaisesRegex(ValueError, "independently verified"):
            resign(protocol="issue_time", cutoff="2002-01-01T00:00:00Z").verify()

    def test_no_symlinks_submodules_binary_or_code_execution(self):
        marker = self.repo / "executed.marker"
        (self.repo / "unsafe.py").write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).write_text('BAD')\n", encoding="utf-8")
        self.git("add", "unsafe.py")
        blob = self.git("hash-object", "-w", "--stdin", data=b"../../outside.py").strip()
        self.git("update-index", "--add", "--cacheinfo", f"120000,{blob},linked.py")
        self.git("update-index", "--add", "--cacheinfo", f"160000,{self.base},submodule")
        self.git("commit", "-q", "-m", "special objects")
        snapshot = build_snapshot(self.repo, "HEAD")
        graph = build_graph(snapshot)
        reasons = {item["path"]: item["reason"] for item in snapshot.skipped}
        self.assertEqual(reasons["linked.py"], "symlink_not_followed")
        self.assertEqual(reasons["submodule"], "submodule_not_followed")
        self.assertEqual(reasons["binary.bin"], "binary")
        self.assertFalse(marker.exists())
        self.assertTrue(any(node.path == "unsafe.py" for node in graph.nodes))

    def test_snapshot_bounds_and_tamper_detection(self):
        limited = self.snapshot(max_files=1, max_file_bytes=100, max_total_bytes=100)
        self.assertLessEqual(len(limited.files), 1)
        self.assertTrue(any(item["reason"].endswith("limit") for item in limited.skipped))
        snapshot = self.snapshot()
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            replace(snapshot, tree_sha="a" * 40).verify()
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            SourceFile("safe.py", "changed", "a" * 64)
        snapshot.availability["status"] = "tampered"
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            snapshot.verify()

    def test_500_source_blobs_use_one_verified_batch_process(self):
        for number in range(500):
            (self.repo / f"generated_{number:03d}.py").write_text(
                f"def function_{number}():\n    return {number}\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "500 synthetic blobs")
        started = time.perf_counter()
        with patch.object(snapshot_module, "_git", wraps=snapshot_module._git) as calls:
            snapshot = build_snapshot(self.repo, "HEAD")
        elapsed = time.perf_counter() - started
        batches = [call for call in calls.call_args_list if call.args[1:3] == ("cat-file", "--batch")]
        self.assertEqual(len(batches), 1)
        self.assertEqual(len(calls.call_args_list), 5)
        self.assertEqual(len([file for file in snapshot.files if file.path.startswith("generated_")]), 500)
        self.assertTrue(any(file.text.replace("\r\n", "\n") == "def function_499():\n    return 499\n" for file in snapshot.files))
        snapshot.verify()
        print(f"500-source-blob snapshot: {elapsed:.3f}s, one blob batch, five Git processes")

    def test_batch_object_bytes_and_framing_are_verified(self):
        raw = b"safe content"
        blob = snapshot_module.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()
        output = f"{blob} blob {len(raw)}\n".encode() + raw + b"\n"
        with patch.object(snapshot_module, "_git", return_value=output):
            self.assertEqual(snapshot_module._read_blobs(self.repo, {blob: len(raw)}), {blob: raw})
        changed = output.replace(b"safe content", b"evil content")
        with patch.object(snapshot_module, "_git", return_value=changed):
            with self.assertRaisesRegex(ValueError, "content-addressed"):
                snapshot_module._read_blobs(self.repo, {blob: len(raw)})
        with patch.object(snapshot_module, "_git", return_value=output[:-1]):
            with self.assertRaisesRegex(ValueError, "Truncated"):
                snapshot_module._read_blobs(self.repo, {blob: len(raw)})

    def test_hostile_paths_are_rejected_before_graph_or_filesystem_use(self):
        for path in ("../escape.py", "/absolute.py", "C:/outside.py", "a\\b.py", "a/../b.py", ".git/config"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_repo_path(path)

    def test_ast_symbols_typed_edges_and_parser_failures(self):
        graph = build_graph(self.snapshot())
        symbols = {node.qualname: node for node in graph.nodes if node.kind != "file"}
        self.assertIn("Handler.handle", symbols)
        self.assertEqual(symbols["Handler.handle"].start_line, 7)
        self.assertEqual(symbols["Handler.handle"].end_line, 8)
        edges = graph.edges
        self.assertTrue(any(edge.kind == "inherits" and edge.target == symbols["Base"].id for edge in edges))
        self.assertTrue(any(edge.kind == "calls" and edge.target == symbols["normalize"].id for edge in edges))
        self.assertTrue(any(edge.kind == "contains" and edge.target == symbols["Handler.handle"].id for edge in edges))
        self.assertEqual(graph.parse_errors[0]["path"], "malformed.py")
        self.assertEqual(build_graph(self.snapshot()).to_dict(), graph.to_dict())
        json.dumps(graph.to_dict())

    def test_unresolved_and_ambiguous_calls_remain_unknown(self):
        (self.repo / "ambiguous.py").write_text(
            "def target():\n    pass\ndef target():\n    pass\ndef run(obj):\n"
            "    target()\n    obj.dynamic()\n", encoding="utf-8")
        self.git("add", "ambiguous.py")
        self.git("commit", "-q", "-m", "ambiguous")
        graph = build_graph(build_snapshot(self.repo, "HEAD"))
        calls = [edge for edge in graph.edges if edge.kind == "calls" and "ambiguous.py" in edge.source]
        self.assertEqual({edge.reference for edge in calls}, {"target", "obj.dynamic"})
        self.assertTrue(all(edge.target is None and edge.resolution == "unresolved" for edge in calls))

    def test_gold_labels_stay_outside_graph_and_use_old_side(self):
        snapshot = self.snapshot()
        graph_before = build_graph(snapshot)
        patch = (
            "diff --git a/pkg/helpers.py b/pkg/helpers.py\n--- a/pkg/helpers.py\n+++ b/pkg/helpers.py\n"
            "@@ -1,2 +1,2 @@\n def normalize(value):\n-    return value.strip()\n+    return value.lower().strip()\n"
            "diff --git a/new.py b/new.py\n--- /dev/null\n+++ b/new.py\n"
            "@@ -0,0 +1,2 @@\n+def future_solution():\n+    return True\n"
        )
        labels = extract_gold_localization(patch, snapshot, graph_before)
        self.assertEqual(labels.files, ("new.py", "pkg/helpers.py"))
        self.assertEqual(labels.changed_lines["pkg/helpers.py"], (2,))
        self.assertEqual(labels.unavailable_files, ("new.py",))
        self.assertTrue(any("normalize" in symbol for symbol in labels.symbols))
        self.assertEqual(build_graph(snapshot).to_dict(), graph_before.to_dict())
        self.assertNotIn("future_solution", json.dumps(graph_before.to_dict()))

    def test_gold_offsets_require_unique_complete_old_context(self):
        snapshot = self.snapshot()
        patch = "--- a/pkg/helpers.py\n+++ b/pkg/helpers.py\n@@ -9,2 +9,2 @@\n def normalize(value):\n-    return value.strip()\n+    return value.lower().strip()\n"
        labels = extract_gold_localization(patch, snapshot)
        self.assertEqual(labels.changed_lines["pkg/helpers.py"], (2,))
        self.assertEqual(labels.hunk_alignments[0]["offset"], -8)
        self.assertEqual(labels.hunk_alignments[0]["method"], "unique_exact_complete_old_hunk")
        with self.assertRaisesRegex(ValueError, "differs from base"):
            extract_gold_localization(patch.replace("return value.strip()", "return nonexistent()"), snapshot)

    def test_gold_pure_insertion_quoted_path_and_bad_hunks(self):
        snapshot = self.snapshot()
        insertion = '--- "a/pkg/helpers.py"\n+++ "b/pkg/helpers.py"\n@@ -1,0 +2,1 @@\n+    value = str(value)\n'
        labels = extract_gold_localization(insertion, snapshot)
        self.assertEqual(labels.changed_lines, {})
        self.assertEqual(labels.insertion_anchors, {"pkg/helpers.py": (1,)})
        self.assertTrue(labels.symbols)
        with self.assertRaisesRegex(ValueError, "Unsafe repository path"):
            extract_gold_localization("--- a/../secret\n+++ b/../secret\n", snapshot)
        with self.assertRaisesRegex(ValueError, "Truncated"):
            extract_gold_localization("--- a/pkg/helpers.py\n+++ b/pkg/helpers.py\n@@ -1,2 +1,2 @@\n def normalize(value):\n", snapshot)
        with self.assertRaisesRegex(ValueError, "differs from base"):
            extract_gold_localization("--- a/pkg/helpers.py\n+++ b/pkg/helpers.py\n@@ -1,1 +1,1 @@\n-incorrect base\n+changed\n", snapshot)
        other = replace(snapshot, snapshot_sha256="0" * 64)
        with self.assertRaises(ValueError):
            extract_gold_localization(insertion, other)


if __name__ == "__main__":
    unittest.main()
