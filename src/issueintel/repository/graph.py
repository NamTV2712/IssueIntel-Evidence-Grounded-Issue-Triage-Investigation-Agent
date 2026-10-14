"""Deterministic, bounded Python AST graph over an immutable snapshot.

Containment is observed syntax. Imports, inheritance and calls are static
references, not proof that an execution reached a node. Dynamic dispatch and
ambiguous names remain unresolved; syntax failures remain visible in output.
"""
from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import PurePosixPath
import sys
import warnings

from .snapshot import RepositorySnapshot, source_lines


@dataclass(frozen=True)
class GraphNode:
    id: str
    kind: str
    path: str
    name: str
    qualname: str
    start_line: int
    end_line: int
    sha256: str
    text: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class GraphEdge:
    source: str
    target: str | None
    kind: str
    reference: str = ""
    resolution: str = "exact"
    inferred: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class CodeGraph:
    snapshot_sha256: str
    commit_sha: str
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    parse_errors: tuple[dict, ...]
    parser_version: str
    limitations: tuple[str, ...] = (
        "Python AST only; unsupported languages retain file nodes.",
        "Resolved calls/imports are static references, not observed runtime dependencies.",
        "Dynamic dispatch, wildcard imports and ambiguous identifiers may remain unresolved.",
    )

    def to_dict(self) -> dict:
        return asdict(self)


def _module(path: str) -> str:
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts and parts[0] == "src":
        parts.pop(0)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _expression(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except (ValueError, RecursionError):
        return "<unresolved-expression>"


def build_graph(snapshot: RepositorySnapshot) -> CodeGraph:
    """Build from snapshot bytes alone; this function never accepts gold labels."""
    snapshot.verify()
    nodes: dict[str, GraphNode] = {}
    edges: set[GraphEdge] = set()
    errors: list[dict] = []
    trees: dict[str, ast.AST] = {}
    symbol_ids: dict[tuple[str, int], str] = {}
    parents: dict[str, str] = {}
    symbols: dict[tuple[str, str], list[str]] = {}
    modules: dict[str, list[str]] = {}
    files = {file.path: file for file in snapshot.files}
    for file in snapshot.files:
        line_count = len(source_lines(file.text))
        node = GraphNode(f"file:{file.path}", "file", file.path,
                         PurePosixPath(file.path).name, _module(file.path),
                         1, max(1, line_count), file.sha256, file.text)
        nodes[node.id] = node
        if file.path.endswith(".py"):
            modules.setdefault(_module(file.path), []).append(node.id)
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", SyntaxWarning)
                    trees[file.path] = ast.parse(file.text, filename=file.path)
            except (SyntaxError, ValueError, RecursionError) as exc:
                errors.append({"path": file.path, "kind": type(exc).__name__,
                               "line": getattr(exc, "lineno", None), "message": str(exc)[:300]})

    class Definitions(ast.NodeVisitor):
        def __init__(self, path: str):
            self.path = path
            self.stack = [f"file:{path}"]

        def _symbol(self, node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
                    kind: str) -> None:
            parent = nodes[self.stack[-1]]
            qualname = node.name if parent.kind == "file" else parent.qualname + "." + node.name
            start = min([node.lineno] + [decorator.lineno for decorator in node.decorator_list])
            end = node.end_lineno or node.lineno
            text = "\n".join(source_lines(files[self.path].text)[start - 1:end])
            identifier = f"symbol:{self.path}:{qualname}:{start}"
            symbol = GraphNode(identifier, kind, self.path, node.name, qualname,
                               start, end, sha256(text.encode("utf-8")).hexdigest(), text)
            nodes[identifier] = symbol
            symbol_ids[(self.path, id(node))] = identifier
            symbols.setdefault((self.path, qualname), []).append(identifier)
            parents[identifier] = parent.id
            edges.add(GraphEdge(parent.id, identifier, "contains"))
            self.stack.append(identifier)
            self.generic_visit(node)
            self.stack.pop()

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self._symbol(node, "class")

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._symbol(node, "function")

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._symbol(node, "function")

    for path, tree in trees.items():
        Definitions(path).visit(tree)

    def unique(identifiers: list[str]) -> str | None:
        return identifiers[0] if len(identifiers) == 1 else None

    def module_target(name: str) -> str | None:
        return unique(modules.get(name, []))

    def import_target(module: str, member: str | None = None) -> str | None:
        if member is None:
            return module_target(module)
        target = module_target(module)
        if target:
            direct = unique(symbols.get((nodes[target].path, member), []))
            if direct:
                return direct
        return module_target(module + "." + member)

    aliases: dict[tuple[str, str], set[str | None]] = {}

    class ScopedVisitor(ast.NodeVisitor):
        def __init__(self, path: str):
            self.path = path
            self.stack = [f"file:{path}"]

        def _scope(self, node: ast.AST) -> None:
            self.stack.append(symbol_ids[(self.path, id(node))])
            self.generic_visit(node)
            self.stack.pop()

        visit_ClassDef = _scope
        visit_FunctionDef = _scope
        visit_AsyncFunctionDef = _scope

    class Imports(ScopedVisitor):
        def add(self, alias: str, reference: str, target: str | None) -> None:
            source = self.stack[-1]
            aliases.setdefault((source, alias), set()).add(target)
            edges.add(GraphEdge(source, target, "imports", reference,
                                "exact" if target else "unresolved", True))

        def visit_Import(self, node: ast.Import) -> None:
            for name in node.names:
                module = name.name if name.asname else name.name.split(".")[0]
                self.add(name.asname or module, name.name, module_target(module))

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            module = node.module or ""
            if node.level:
                package = _module(self.path).split(".")
                if PurePosixPath(self.path).name != "__init__.py":
                    package = package[:-1]
                up = node.level - 1
                prefix = package[:len(package) - up] if up <= len(package) else []
                module = ".".join(prefix + ([module] if module else []))
            for name in node.names:
                reference = module + "." + name.name
                target = None if name.name == "*" else import_target(module, name.name)
                self.add(name.asname or name.name, reference, target)

    for path, tree in trees.items():
        Imports(path).visit(tree)

    def resolve(path: str, scope: str, expression: ast.AST) -> str | None:
        if isinstance(expression, ast.Name):
            owner: str | None = scope
            while owner is not None:
                node = nodes[owner]
                prefix = "" if node.kind == "file" else node.qualname + "."
                matches = symbols.get((path, prefix + expression.id), [])
                if matches:
                    return unique(matches)
                imported = aliases.get((owner, expression.id))
                if imported:
                    return next(iter(imported)) if len(imported) == 1 else None
                owner = parents.get(owner)
            return None
        if isinstance(expression, ast.Attribute):
            if isinstance(expression.value, ast.Name) and expression.value.id in {"self", "cls"}:
                owner: str | None = scope
                while owner is not None:
                    node = nodes[owner]
                    if node.kind == "class":
                        return unique(symbols.get((path, node.qualname + "." + expression.attr), []))
                    owner = parents.get(owner)
                return None
            base = resolve(path, scope, expression.value)
            if base:
                node = nodes[base]
                prefix = "" if node.kind == "file" else node.qualname + "."
                return unique(symbols.get((node.path, prefix + expression.attr), []))
        return None

    class References(ScopedVisitor):
        def visit_Call(self, node: ast.Call) -> None:
            target = resolve(self.path, self.stack[-1], node.func)
            edges.add(GraphEdge(self.stack[-1], target, "calls", _expression(node.func),
                                "exact" if target else "unresolved", True))
            self.generic_visit(node)

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            identifier = symbol_ids[(self.path, id(node))]
            for base in node.bases:
                target = resolve(self.path, self.stack[-1], base)
                edges.add(GraphEdge(identifier, target, "inherits", _expression(base),
                                    "exact" if target else "unresolved", True))
            self._scope(node)

    for path, tree in trees.items():
        References(path).visit(tree)
    return CodeGraph(
        snapshot.snapshot_sha256, snapshot.commit_sha,
        tuple(sorted(nodes.values(), key=lambda node: node.id)),
        tuple(sorted(edges, key=lambda edge: (edge.source, edge.kind, edge.target or "", edge.reference))),
        tuple(errors), f"python-ast-{sys.version_info.major}.{sys.version_info.minor}",
    )
