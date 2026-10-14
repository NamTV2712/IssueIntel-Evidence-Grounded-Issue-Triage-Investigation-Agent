"""Run a registered recipe from its verified archived implementation.

Extraction never copies environment secrets or data. Archived code reads the
original workspace and durable ledger; it cannot silently adopt old labels into
a different recipe. Preparing/checking a runtime makes no model or network call.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import uuid
import zipfile

from issueintel.runtime.config import RuntimeConfig
from issueintel.runtime.io import canonical_json, digest, read_jsonl
from . import construction
from .store import verify_manifest


def _sha(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def _object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Recipe runtime metadata must be a JSON object")
    return value


def _safe(root: Path, path: Path) -> Path:
    return construction._safe(root, path)


def _name(name: str) -> None:
    path = Path(name)
    allowed = ((name.startswith("src/issueintel/") and name.endswith(".py"))
               or (name.startswith("configs/") and path.suffix in {".json", ".toml"})
               or name == "pyproject.toml")
    if (not allowed or "\\" in name or ":" in name or path.is_absolute()
            or ".." in path.parts or path.as_posix() != name or path.is_reserved()):
        raise ValueError("Recipe archive member path is not allowed")


def _registration(root: Path, identifier: str) -> tuple[dict, dict]:
    if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{64}", identifier):
        raise ValueError("recipe_sha256 must be a canonical SHA256")
    path = _safe(root, root / "data/annotations/recipes" / (identifier + ".json"))
    record = _object(path)
    recipe = record.get("recipe")
    required = {"recipe_sha256", "registered_at", "recipe", "registration_sha256", "source_archive"}
    if (not isinstance(recipe, dict) or record.get("recipe_sha256") != identifier or digest(recipe) != identifier
            or set(record) != required or not isinstance(record.get("registered_at"), str)
            or digest({key: value for key, value in record.items() if key != "registration_sha256"}) != record.get("registration_sha256")
            or recipe.get("schema_version") != "issueintel.annotation_recipe.v1"
            or recipe.get("mode") != "dataset_construction" or recipe.get("evaluation_performed") is not False
            or recipe.get("registered_before_generation") is not True or recipe.get("pilot_adoption") != "forbidden"
            or recipe.get("protocol") not in {"benchmark_base", "issue_time"}
            or recipe.get("source_inventory_sha256") != digest(recipe.get("source_hashes"))):
        raise ValueError("Recipe registration identity/structure is invalid")
    if not isinstance(recipe.get("source_hashes"), dict) or not recipe["source_hashes"]:
        raise ValueError("Recipe runtime source inventory is empty or invalid")
    manifest = verify_manifest(root)
    if _sha(root / "data/manifests/dataset.json") != recipe.get("dataset_manifest_sha256"):
        raise ValueError("Recipe source dataset manifest changed")
    cases = {row["case_id"]: row for row in read_jsonl(_safe(root, root / manifest["files"]["cases"]["path"]))}
    seen = set()
    for descriptor in recipe["cases"]:
        case = cases.get(descriptor["case_id"])
        if (case is None or case["case_id"] in seen or case["split"] != descriptor["split"]
                or digest(case) != descriptor["case_sha256"]):
            raise ValueError("Recipe runtime case/split binding changed")
        seen.add(case["case_id"])
    if not seen:
        raise ValueError("Recipe runtime has no registered cases")
    for path in (root / "data/annotations/recipe_assessments" / identifier).glob("*.json"):
        assessment = _object(_safe(root, path))
        if (digest(assessment) != path.stem or assessment.get("recipe_sha256") != identifier
                or assessment.get("registration_sha256") != record["registration_sha256"]
                or type(assessment.get("primary_eligible")) is not bool
                or assessment.get("evaluation_performed") is not False):
            raise ValueError("Recipe runtime assessment binding is invalid")
        if assessment["primary_eligible"] is False:
            raise ValueError("Diagnostic negative assessment forbids recipe continuation")
    reference = record.get("source_archive")
    if not isinstance(reference, dict):
        raise ValueError("Recipe continuation requires its original pre-generation source archive")
    archive_path = _safe(root, root / reference["path"])
    if archive_path.parent != root / "data/annotations/recipe_sources" or archive_path.stat().st_size > 64_000_000:
        raise ValueError("Recipe source archive path/size exceeds runtime bounds")
    with zipfile.ZipFile(archive_path) as archive:
        entries = archive.infolist()
        if (len(entries) > 10000 or sum(info.file_size for info in entries) > 64_000_000
                or any(info.file_size > 5_000_000 for info in entries)):
            raise ValueError("Recipe source archive exceeds runtime extraction bounds")
    construction._verify_source_archive(root, reference, recipe)
    descriptor = _object(_safe(root, root / reference["descriptor_path"]))
    for name in descriptor["files"]:
        _name(name)
    if any(descriptor["files"].get(name) != sha for name, sha in recipe["configuration"]["files"].items()):
        raise ValueError("Frozen configuration is not fully captured in the source archive")
    # This compares current workspace configuration, never current source code.
    # Changing a budget/model/source config requires an explicit new recipe or
    # restoration of its actual original configuration, not patched hashes.
    config = RuntimeConfig.load(root)
    if construction._config_fingerprint(root, config) != recipe["configuration"]:
        raise ValueError("Workspace configuration differs from the frozen recipe")
    return record, descriptor


def _inventory(directory: Path, root: Path) -> dict:
    result = {}
    pending = [directory]
    while pending:
        parent = pending.pop()
        for path in parent.iterdir():
            path = _safe(root, path)
            if path.is_dir():
                pending.append(path)
            elif path.is_file():
                result[path.relative_to(directory).as_posix()] = _sha(path)
            else:
                raise ValueError("Recipe runtime contains an unsupported filesystem object")
    return result


def _verify_extraction(root: Path, directory: Path, marker: dict) -> None:
    _safe(root, directory)
    expected = dict(marker["files"], **{"runtime.json": hashlib.sha256((canonical_json(marker) + "\n").encode()).hexdigest()})
    if _inventory(directory, root) != expected:
        raise ValueError("Immutable recipe runtime is missing, changed or has unexpected files")


def prepare_recipe_runtime(root: Path | str, recipe_sha256: str) -> dict:
    """Materialize the exact frozen src/config archive without modifying data."""
    root = Path(root).resolve()
    record, descriptor = _registration(root, recipe_sha256)
    reference = record["source_archive"]
    identifier = reference["inventory_sha256"]
    directory = _safe(root, root / "artifacts/recipe_runtimes" / identifier)
    marker = {"schema_version": "issueintel.recipe_runtime.v1", "inventory_sha256": identifier,
        "files": descriptor["files"], "source_archive": {key: reference[key] for key in ("path", "sha256", "bytes")},
        "source_descriptor": {"path": reference["descriptor_path"], "sha256": reference["descriptor_sha256"]},
        "environment_secrets_copied": False, "data_copied": False}
    with construction._worker_lock(directory.parent / ("." + identifier + ".lock"), wait=True):
        if directory.exists():
            _verify_extraction(root, directory, marker)
        else:
            temporary = _safe(root, directory.parent / (".preparing-" + identifier + "-" + uuid.uuid4().hex))
            temporary.mkdir(parents=True)
            with zipfile.ZipFile(_safe(root, root / reference["path"])) as archive:
                if len(archive.infolist()) > 10000 or sum(info.file_size for info in archive.infolist()) > 64_000_000:
                    raise ValueError("Recipe source archive exceeds runtime extraction bounds")
                for name in sorted(descriptor["files"]):
                    info = archive.getinfo(name)
                    if info.file_size > 5_000_000:
                        raise ValueError("Recipe source member exceeds runtime extraction bounds")
                    _name(name)
                    path = _safe(root, temporary / name)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    content = archive.read(name)
                    if hashlib.sha256(content).hexdigest() != descriptor["files"][name]:
                        raise ValueError("Recipe archive changed during extraction")
                    with path.open("xb") as handle:
                        handle.write(content)
            with (temporary / "runtime.json").open("xb") as handle:
                handle.write((canonical_json(marker) + "\n").encode())
            _verify_extraction(root, temporary, marker)
            # Both absolute paths are checked inside this workspace, and all
            # entries were checked for links/junctions before publication.
            _safe(root, temporary)
            _safe(root, directory)
            temporary.rename(directory)
            _verify_extraction(root, directory, marker)
    return {"recipe_sha256": recipe_sha256, "registration_sha256": record["registration_sha256"],
        "root": str(root), "runtime_directory": str(directory), "source_directory": str(directory / "src"),
        "inventory_sha256": identifier, "runtime_marker_sha256": _sha(directory / "runtime.json"),
        "source_archive_sha256": reference["sha256"],
        "ledger_file": str(root / record["recipe"]["configuration"]["runtime"]["ledger_file"]),
        "mode": "frozen_recipe_reproduction", "model_calls_started": False}


# Isolated standard-library bootstrap: authenticate extracted files BEFORE the
# first issueintel import. -I ignores PYTHONPATH/cwd, -B prevents new cache files.
_BOOTSTRAP = r'''
import hashlib,json,pathlib,sys
binding=json.loads(sys.argv[1]); action=sys.argv[2]; limit=int(sys.argv[3])
if "issueintel" in sys.modules: raise RuntimeError("Current package was imported before frozen bootstrap")
root=pathlib.Path(binding["root"]).resolve(); runtime=pathlib.Path(binding["runtime_directory"])
def safe(path):
    resolved=path.resolve()
    if not resolved.is_relative_to(root): raise ValueError("Frozen runtime path escapes workspace")
    current=path
    while current!=root:
        if current.is_symlink() or (hasattr(current,"is_junction") and current.is_junction()):
            raise ValueError("Frozen runtime path traverses a link/junction")
        current=current.parent
    return resolved
safe(runtime)
marker_path=safe(runtime/"runtime.json"); raw=marker_path.read_bytes()
if hashlib.sha256(raw).hexdigest()!=binding["runtime_marker_sha256"]: raise ValueError("Frozen runtime marker changed")
marker=json.loads(raw)
canonical=lambda value:json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()
if hashlib.sha256(canonical(marker["files"])).hexdigest()!=binding["inventory_sha256"]:
    raise ValueError("Frozen runtime source inventory changed")
expected=dict(marker["files"]);expected["runtime.json"]=binding["runtime_marker_sha256"]
found={};pending=[runtime]
while pending:
    for path in pending.pop().iterdir():
        path=safe(path)
        if path.is_dir(): pending.append(path)
        elif path.is_file(): found[path.relative_to(runtime).as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
        else: raise ValueError("Unsupported frozen runtime filesystem object")
if found!=expected: raise ValueError("Frozen runtime source contents changed")
assessment_directory=safe(root/"data/annotations/recipe_assessments"/binding["recipe_sha256"])
for path in assessment_directory.glob("*.json"):
    path=safe(path);assessment=json.loads(path.read_text(encoding="utf-8"))
    if (hashlib.sha256(canonical(assessment)).hexdigest()!=path.stem
            or assessment.get("recipe_sha256")!=binding["recipe_sha256"]
            or assessment.get("registration_sha256")!=binding["registration_sha256"]
            or type(assessment.get("primary_eligible")) is not bool
            or assessment.get("evaluation_performed") is not False):
        raise ValueError("Frozen recipe assessment binding changed")
    if assessment["primary_eligible"] is False: raise ValueError("Diagnostic negative assessment forbids recipe continuation")
source=safe(runtime/"src")
sys.path.insert(0,str(source))
from issueintel.benchmarks.construction import load_annotation_recipe
from issueintel.runtime.config import RuntimeConfig
record=load_annotation_recipe(root,binding["recipe_sha256"])
if record["registration_sha256"]!=binding["registration_sha256"]: raise ValueError("Original recipe registration changed")
if record["source_archive"]["sha256"]!=binding["source_archive_sha256"]: raise ValueError("Original recipe source archive changed")
config=RuntimeConfig.load(root)
if str(config.ledger_file)!=binding["ledger_file"]: raise ValueError("Frozen runtime selected a different durable ledger")
if action=="check":
    print(json.dumps({"recipe_sha256":binding["recipe_sha256"],"recipe_guard_passed":True,
        "source_directory":str(source),"root":str(root),"ledger_file":str(config.ledger_file),
        "model":config.model,"mode":"offline_recipe_check","model_calls_started":False}))
elif action=="annotate":
    from issueintel.interfaces.cli import main
    raise SystemExit(main(["--root",str(root),"annotate-dataset","--recipe",binding["recipe_sha256"],"--limit",str(limit)]))
else: raise ValueError("Unsupported frozen recipe action")
'''


def recipe_runtime_command(root: Path | str, recipe_sha256: str, *, check=False, limit=800,
                           python_executable: Path | str | None = None) -> list[str]:
    """Return argv; this helper never launches a worker or changes its ledger."""
    if type(check) is not bool or type(limit) is not int or not 1 <= limit <= 800:
        raise ValueError("Frozen recipe action requires a boolean check and limit between 1 and 800")
    binding = prepare_recipe_runtime(root, recipe_sha256)
    executable = str(python_executable or sys.executable)
    return [executable, "-I", "-B", "-c", _BOOTSTRAP, canonical_json(binding), "check" if check else "annotate", str(limit)]


def check_recipe_runtime(root: Path | str, recipe_sha256: str, *, python_executable=None, timeout=30) -> dict:
    """Run only the isolated offline recipe guard; no model/worker dispatch."""
    command = recipe_runtime_command(root, recipe_sha256, check=True, python_executable=python_executable)
    result = subprocess.run(command, cwd=str(Path(root).resolve()), capture_output=True, text=True,
                            encoding="utf-8", timeout=timeout, check=True)
    checked = json.loads(result.stdout)
    if checked.get("recipe_guard_passed") is not True or checked.get("model_calls_started") is not False:
        raise ValueError("Frozen recipe offline check returned invalid evidence")
    return checked
