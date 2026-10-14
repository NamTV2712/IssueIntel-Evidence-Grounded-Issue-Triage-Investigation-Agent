from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def write_json(path: Path | str, value: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as temp:
            temporary = temp.name
            temp.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
            temp.flush()
            os.fsync(temp.fileno())
        for attempt in range(5):
            try:
                os.replace(temporary, path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.025 * 2 ** attempt)
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)


def write_progress(path: Path | str, value: Any) -> str | None:
    """Convenience output only; authoritative receipts must use write_json.

    A locked Windows progress file cannot invalidate an already committed stage.
    Returns the written path, or None when both convenience writes failed.
    """
    path = Path(path)
    try:
        write_json(path, value)
        return str(path)
    except OSError:
        fallback = path.with_name(path.stem + "." + digest(value)[:16] + ".json")
        try:
            write_json(fallback, value)
            return str(fallback)
        except OSError:
            return None


def read_jsonl(path: Path | str) -> list[dict]:
    with Path(path).open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def load_local_env(path: Path | str) -> None:
    """No shell evaluation/interpolation; do not overwrite process credentials."""
    path = Path(path)
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if not key.replace("_", "a").isalnum():
            continue
        if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)
