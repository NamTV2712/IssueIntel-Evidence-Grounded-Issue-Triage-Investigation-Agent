"""Evaluator-only localization labels derived from a gold unified diff.

Gold patch files and pre-patch spans are localization proxies. They are not
ground-truth explanations of a causal mechanism. This module is deliberately
not imported by the product's repository or investigation builders.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import re

from issueintel.repository.graph import CodeGraph, build_graph
from issueintel.repository.snapshot import RepositorySnapshot, validate_repo_path, source_lines


@dataclass(frozen=True)
class GoldLocalization:
    files: tuple[str, ...]
    changed_lines: dict[str, tuple[int, ...]]
    insertion_anchors: dict[str, tuple[int, ...]]
    symbols: tuple[str, ...]
    unavailable_files: tuple[str, ...]
    patch_sha256: str
    hunk_alignments: tuple[dict, ...] = ()
    limitations: tuple[str, ...] = (
        "Patch-derived localization is a proxy, not a causal root-cause label.",
        "Added files have no pre-patch symbols; inserted lines use old-side anchors.",
        "Unavailable files remain in the file denominator rather than being silently removed.",
    )

    def to_dict(self) -> dict:
        return asdict(self)


def _path(value: str, *, strip_prefix: bool = True) -> str | None:
    value = value.split("\t", 1)[0]
    if value == "/dev/null":
        return None
    if value.startswith('"'):
        if not value.endswith('"'):
            raise ValueError("Malformed quoted Git patch path")
        body = value[1:-1]
        escaped = bytearray()
        index = 0
        translations = {"t": b"\t", "n": b"\n", "r": b"\r", "\\": b"\\", '"': b'"'}
        while index < len(body):
            if body[index] != "\\":
                escaped.extend(body[index].encode("utf-8"))
                index += 1
                continue
            index += 1
            octal = re.match(r"[0-7]{1,3}", body[index:])
            if octal:
                escaped.append(int(octal.group(), 8))
                index += len(octal.group())
            elif index < len(body) and body[index] in translations:
                escaped.extend(translations[body[index]])
                index += 1
            else:
                raise ValueError("Unsupported quoted patch path escape")
        value = escaped.decode("utf-8")
    if strip_prefix and value.startswith(("a/", "b/")):
        value = value[2:]
    return validate_repo_path(value)


def contextless_insertion_paths(patch: str) -> set[str]:
    """Paths whose insertion anchors have no old-side context to align."""
    old_path = current_path = None
    result = set()
    for line in source_lines(patch):
        if line.startswith("diff --git "):
            old_path = current_path = None
        elif line.startswith("--- "):
            old_path = _path(line[4:])
        elif line.startswith("+++ "):
            current_path = old_path or _path(line[4:])
        elif current_path and re.match(r"@@ -\d+,0 \+\d+(?:,\d+)? @@", line):
            result.add(current_path)
    return result


def extract_gold_localization(patch: str, snapshot: RepositorySnapshot,
                              graph: CodeGraph | None = None) -> GoldLocalization:
    """Parse old-side deletion lines and insertion anchors without applying a patch."""
    snapshot.verify()
    graph = graph or build_graph(snapshot)
    if graph.snapshot_sha256 != snapshot.snapshot_sha256:
        raise ValueError("Gold localization graph belongs to a different snapshot")
    # Git/Python line numbers count LF, not form-feed or Unicode paragraph breaks.
    base_lines = {file.path: source_lines(file.text) for file in snapshot.files}
    files: set[str] = set()
    changed: dict[str, set[int]] = {}
    anchors: dict[str, set[int]] = {}
    old_path: str | None = None
    current_path: str | None = None
    in_hunk = False
    old_line = 0
    last_deleted: int | None = None
    old_remaining = new_remaining = 0
    saw_header = False
    patch_lines = source_lines(patch)
    alignments = []
    for patch_index, line in enumerate(patch_lines):
        if in_hunk and old_remaining == 0 and new_remaining == 0:
            in_hunk = False
        if line.startswith("diff --git "):
            if in_hunk:
                raise ValueError("Truncated unified diff hunk")
            old_path = current_path = None
            continue
        if not in_hunk and line.startswith("--- "):
            old_path = _path(line[4:])
            saw_header = True
            continue
        if not in_hunk and line.startswith("+++ "):
            new_path = _path(line[4:])
            current_path = old_path or new_path
            if current_path:
                files.add(current_path)
            continue
        if not in_hunk and line.startswith("rename from "):
            current_path = _path(line[len("rename from "):], strip_prefix=False)
            if current_path:
                files.add(current_path)
            continue
        if line.startswith("@@ "):
            if in_hunk:
                raise ValueError("Truncated unified diff hunk")
            match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            if not match or not current_path:
                raise ValueError("Malformed or unbound unified diff hunk")
            old_line = int(match[1])
            old_remaining = int(match[2]) if match[2] is not None else 1
            new_remaining = int(match[4]) if match[4] is not None else 1
            if current_path in base_lines and old_remaining:
                # Upstream patches can apply with an offset to their designated
                # base. Anchor the COMPLETE old hunk exactly, never fuzzy-match.
                old_content = []
                scanned_old = scanned_new = 0
                for item in patch_lines[patch_index + 1:]:
                    if scanned_old == old_remaining and scanned_new == new_remaining:
                        break
                    if item.startswith("\\ No newline at end of file"):
                        continue
                    if not item or item[0] not in " +-":
                        break
                    if item[0] in " -":
                        old_content.append(item[1:]); scanned_old += 1
                    if item[0] in " +":
                        scanned_new += 1
                if scanned_old != old_remaining or scanned_new != new_remaining:
                    raise ValueError("Truncated unified diff hunk")
                file_lines = base_lines[current_path]
                expected_index = old_line - 1
                if file_lines[expected_index:expected_index + len(old_content)] != old_content:
                    matches = [index for index in range(len(file_lines) - len(old_content) + 1)
                               if file_lines[index:index + len(old_content)] == old_content]
                    if len(matches) != 1:
                        raise ValueError(f"Gold patch old-side content differs from base: no unique exact hunk alignment in {current_path}")
                    aligned_start = matches[0] + 1
                    alignments.append({"path": current_path, "declared_start": old_line,
                        "aligned_start": aligned_start, "offset": aligned_start - old_line,
                        "method": "unique_exact_complete_old_hunk"})
                    old_line = aligned_start
            last_deleted = None
            in_hunk = True
            continue
        if in_hunk:
            if line.startswith("\\ No newline at end of file"):
                continue
            if not line or line[0] not in " +-":
                raise ValueError("Malformed unified diff hunk line")
            if line[0] in " -" and current_path in base_lines:
                file_lines = base_lines[current_path]
                if (old_line <= 0 or old_line > len(file_lines)
                        or file_lines[old_line - 1] != line[1:]):
                    raise ValueError(f"Gold patch old-side content differs from base snapshot at {current_path}:{old_line}")
            if line[0] == "-":
                changed.setdefault(current_path, set()).add(old_line)
                last_deleted = old_line
                old_line += 1
                old_remaining -= 1
            elif line[0] == "+":
                anchors.setdefault(current_path, set()).add(max(1, last_deleted if last_deleted is not None else old_line))
                new_remaining -= 1
            else:
                last_deleted = None
                old_line += 1
                old_remaining -= 1
                new_remaining -= 1
            if min(old_remaining, new_remaining) < 0:
                raise ValueError("Unified diff hunk contains more lines than declared")
    if in_hunk and (old_remaining or new_remaining):
        raise ValueError("Truncated unified diff hunk")
    if patch.strip() and not saw_header and not files:
        raise ValueError("Require a textual unified diff with file paths")
    present = {file.path for file in snapshot.files}
    symbols: set[str] = set()
    for path in files:
        affected = changed.get(path, set()) | anchors.get(path, set())
        for line_number in affected:
            candidates = [node for node in graph.nodes if node.path == path and node.kind != "file"
                          and node.start_line <= line_number <= node.end_line]
            if candidates:
                minimum = min(node.end_line - node.start_line for node in candidates)
                symbols.update(node.id for node in candidates
                               if node.end_line - node.start_line == minimum)
    return GoldLocalization(
        tuple(sorted(files)), {path: tuple(sorted(lines)) for path, lines in sorted(changed.items())},
        {path: tuple(sorted(lines)) for path, lines in sorted(anchors.items())},
        tuple(sorted(symbols)), tuple(sorted(files - present)), sha256(patch.encode("utf-8")).hexdigest(),
        tuple(alignments),
    )
