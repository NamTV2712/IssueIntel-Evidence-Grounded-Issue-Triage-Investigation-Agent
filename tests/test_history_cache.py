import gzip
import io
import json
import pytest
from issueintel.benchmarks import history
from issueintel.benchmarks.history_cache import cache_archive_events


def test_hour_cache_reuses_other_positive_event_without_network(tmp_path, monkeypatch):
    events = [{"type": "PushEvent", "repo": {"name": "owner/project"}, "created_at": "2025-01-01T00:00:00Z",
               "payload": {"head": str(n) * 40}} for n in (1, 2)]
    payload = gzip.compress(b"".join((json.dumps(e) + "\n").encode() for e in events))
    calls = []
    def transport(*a, **k):
        calls.append(1)
        return io.BytesIO(payload)
    monkeypatch.setattr(history.urllib.request, "urlopen", transport)
    with cache_archive_events(tmp_path, ["owner/project"]):
        second = history.scan_archive_hour("2025-01-01-0", lambda e: e["payload"]["head"] == "2" * 40)
        first = history.scan_archive_hour("2025-01-01-0", lambda e: e["payload"]["head"] == "1" * 40)
    assert second["match"]["event"] == events[1]
    assert first["match"]["event"] == events[0] and first["cache_hit"]
    assert len(calls) == 1 and first["absence_claim"] is False


def test_bounded_cache_miss_does_not_claim_global_absence(tmp_path, monkeypatch):
    payload = gzip.compress((json.dumps({"type": "PushEvent", "repo": {"name": "owner/project"},
            "payload": {"head": "a" * 40}, "created_at": "2025-01-01T00:00:00Z"}) + "\n").encode())
    monkeypatch.setattr(history.urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(payload))
    with cache_archive_events(tmp_path, ["owner/project"]):
        first = history.scan_archive_hour("2025-01-01-0", lambda e: False)
        second = history.scan_archive_hour("2025-01-01-0", lambda e: False)
    assert first["coverage_complete"] and second["cache_hit"] and second["match"] is None
    assert not second["absence_claim"]


def test_modified_cache_is_rejected(tmp_path, monkeypatch):
    payload = gzip.compress(b'{}\n')
    monkeypatch.setattr(history.urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(payload))
    with cache_archive_events(tmp_path, ["owner/project"]):
        history.scan_archive_hour("2025-01-01-0", lambda e: False)
        path = next((tmp_path / "data/development/history_cache").glob("*.batch.json"))
        path.write_text("{}")
        with pytest.raises(ValueError, match="bytes changed"):
            history.scan_archive_hour("2025-01-01-0", lambda e: False)
