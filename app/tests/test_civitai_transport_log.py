# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/civitai-integration.md
# ──────────────────────────────────────────────────────────────────────────────
"""Tests for the CivitAI transport log (buffered JSONL telemetry)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from atelierai.civitai.transport_log import (
    CivitaiTransportLog,
    sanitize_url,
)


@pytest.fixture()
def log(tmp_path: Path) -> CivitaiTransportLog:
    """Fresh transport log rooted in a temp dir, with forced flush after each test."""
    instance = CivitaiTransportLog(root=tmp_path)
    yield instance
    instance.flush()


class TestSanitizeUrl:
    def test_strips_query_string(self) -> None:
        assert (
            sanitize_url("https://civitai.com/api/trpc/image.list?token=abc&x=1")
            == "https://civitai.com/api/trpc/image.list"
        )

    def test_url_without_query_unchanged(self) -> None:
        assert sanitize_url("https://civitai.com/api/trpc/image.list") == (
            "https://civitai.com/api/trpc/image.list"
        )

    def test_none_passthrough(self) -> None:
        assert sanitize_url(None) is None

    def test_empty_string(self) -> None:
        assert sanitize_url("") == ""


class TestRecordAndFlush:
    def test_roundtrip(self, log: CivitaiTransportLog, tmp_path: Path) -> None:
        entry = {
            "request_type": "trpc",
            "url": "https://civitai.com/api/trpc/image.list?token=secret",
            "queue_wait_seconds": 0.123,
        }
        log.record(entry)
        assert log.pending_count == 1
        log.flush()
        assert log.pending_count == 0

        entries = log.read_entries()
        assert len(entries) == 1
        assert entries[0]["request_type"] == "trpc"
        assert "token=secret" not in entries[0]["url"]
        assert entries[0]["url"] == "https://civitai.com/api/trpc/image.list"
        # timestamp auto-filled
        assert entries[0]["timestamp"]

    def test_timestamp_filled_from_entry_date(self, log: CivitaiTransportLog) -> None:
        log.record({"timestamp": "2024-01-02T10:00:00+00:00", "url": None})
        log.flush()
        entries = log.read_entries(date="2024-01-02")
        assert len(entries) == 1

    def test_read_entries_date_filter(self, log: CivitaiTransportLog) -> None:
        log.record({"timestamp": "2024-01-02T10:00:00+00:00"})
        log.record({"timestamp": "2024-01-03T10:00:00+00:00"})
        log.flush()
        assert len(log.read_entries(date="2024-01-02")) == 1
        assert len(log.read_entries(date="2024-01-03")) == 1
        assert len(log.read_entries(date="2024-01-04")) == 0
        assert len(log.read_entries()) == 2

    def test_multiple_entries_same_day_append(self, log: CivitaiTransportLog) -> None:
        for i in range(5):
            log.record({"timestamp": "2024-01-02T10:00:00+00:00", "seq": i})
        log.flush()
        entries = log.read_entries(date="2024-01-02")
        assert [e["seq"] for e in entries] == [0, 1, 2, 3, 4]

    def test_daily_file_naming(self, log: CivitaiTransportLog, tmp_path: Path) -> None:
        log.record({"timestamp": "2024-01-02T10:00:00+00:00"})
        log.flush()
        expected = tmp_path / "civitai_transport_2024-01-02.jsonl"
        assert expected.exists()
        content = expected.read_text(encoding="utf-8")
        assert content.count("\n") == 1
        parsed = json.loads(content.strip())
        assert parsed["timestamp"] == "2024-01-02T10:00:00+00:00"

    def test_corrupt_lines_skipped_on_read(
        self, log: CivitaiTransportLog, tmp_path: Path
    ) -> None:
        log.record({"timestamp": "2024-01-02T10:00:00+00:00", "ok": True})
        log.flush()
        target = tmp_path / "civitai_transport_2024-01-02.jsonl"
        with target.open("a", encoding="utf-8") as handle:
            handle.write("{not json}\n")
        entries = log.read_entries(date="2024-01-02")
        assert len(entries) == 1
        assert entries[0]["ok"] is True


class TestDisabled:
    def test_disabled_via_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CIVITAI_TRANSPORT_LOG", "0")
        instance = CivitaiTransportLog(root=tmp_path)
        assert instance.enabled is False
        instance.record({"url": "https://civitai.com/x"})
        instance.flush()
        assert instance.read_entries() == []

    def test_enabled_by_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CIVITAI_TRANSPORT_LOG", raising=False)
        instance = CivitaiTransportLog(root=tmp_path)
        assert instance.enabled is True


class TestFailureSafety:
    def test_unwritable_root_does_not_raise(self, tmp_path: Path) -> None:
        # Point the log at a path occupied by a regular file — mkdir fails.
        blocker = tmp_path / "blocker"
        blocker.write_text("not a dir", encoding="utf-8")
        instance = CivitaiTransportLog(root=blocker / "sub")
        instance.record({"timestamp": "2024-01-02T10:00:00+00:00"})
        instance.flush()  # must not raise
        assert instance.read_entries() == []

    def test_record_swallows_bad_entry(self, log: CivitaiTransportLog) -> None:
        # Objects that break json.dumps at flush time must not raise record().
        class Unserializable:
            pass

        log.record({"timestamp": "2024-01-02T10:00:00+00:00", "bad": Unserializable()})
        log.flush()  # default=str handles it; no raise either way
        entries = log.read_entries()
        assert len(entries) == 1


class TestPrune:
    def test_prune_old_files(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CIVITAI_TRANSPORT_LOG_MAX_FILES", "3")
        # _MAX_LOG_FILES is read at import time; emulate small cap by writing
        # 5 daily files directly and pruning via a fresh flush.
        for day in range(1, 6):
            path = tmp_path / f"civitai_transport_2024-01-0{day}.jsonl"
            path.write_text(f'{{"seq": {day}}}\n', encoding="utf-8")

        instance = CivitaiTransportLog(root=tmp_path)
        instance.record({"timestamp": "2024-01-05T10:00:00+00:00", "seq": 5})
        # Flush triggers prune under the import-time cap (14). Force the
        # small-cap behavior by patching the module constant.
        import atelierai.civitai.transport_log as tl

        original = tl._MAX_LOG_FILES
        tl._MAX_LOG_FILES = 3
        try:
            instance.flush()
            remaining = sorted(p.name for p in tmp_path.glob("civitai_transport_*.jsonl"))
            assert len(remaining) == 3
            assert remaining == [
                "civitai_transport_2024-01-03.jsonl",
                "civitai_transport_2024-01-04.jsonl",
                "civitai_transport_2024-01-05.jsonl",
            ]
        finally:
            tl._MAX_LOG_FILES = original


class TestModuleSingleton:
    def test_get_transport_log_singleton(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import atelierai.civitai.transport_log as tl

        monkeypatch.setattr(tl, "_TRANSPORT_LOG_REF", [None])
        first = tl.get_transport_log()
        second = tl.get_transport_log()
        assert first is second

    def test_record_transport_event_uses_singleton(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import atelierai.civitai.transport_log as tl

        instance = CivitaiTransportLog(root=tmp_path)
        monkeypatch.setattr(tl, "_TRANSPORT_LOG_REF", [instance])
        tl.record_transport_event(
            {"timestamp": "2024-01-02T10:00:00+00:00", "via": "module"}
        )
        instance.flush()
        entries = instance.read_entries()
        assert len(entries) == 1
        assert entries[0]["via"] == "module"


def test_no_query_strings_in_any_written_entry(tmp_path: Path) -> None:
    instance = CivitaiTransportLog(root=tmp_path)
    instance.record({"url": "https://image.civitai.com/a.png?token=t1"})
    instance.record({"url": "https://civitai.com/api/trpc/x?token=t2"})
    instance.flush()
    for entry in instance.read_entries():
        assert "?" not in (entry.get("url") or "")
