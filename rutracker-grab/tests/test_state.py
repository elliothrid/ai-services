"""Чтение и запись манифеста `.grab.json` (DESIGN.md §10). Офлайн, реальная запись в tmp.

Решения о `skipped`/recheck живут в `reconcile.py` — см. `test_reconcile.py`.
Здесь только roundtrip и то, что папка с `[...]` находится через exists() (не glob).
"""

from __future__ import annotations

from rutracker_grab import config, state


def test_write_then_read_roundtrip(tmp_path):
    path = state.write_manifest(
        tmp_path,
        topic_id="6870556",
        clean_title="Ешь. Молись. Худей [2026]",
        torrent_hash="8d36abc",
    )
    assert path.name == ".grab.json"

    manifest = state.read_manifest(tmp_path)
    assert manifest["topic_id"] == "6870556"
    assert manifest["clean_title"] == "Ешь. Молись. Худей [2026]"
    assert manifest["torrent_hash"] == "8d36abc"
    assert manifest["rules_version"] == config.RULES_VERSION
    # Первый прогон: обе метки времени — время этого прогона.
    assert manifest["first_grabbed_at"] == manifest["updated_at"]


def test_rewrite_keeps_first_grabbed_at(tmp_path):
    state.write_manifest(
        tmp_path, topic_id="42", clean_title="X", torrent_hash="old", now="2026-01-01T00:00:00+00:00"
    )
    state.write_manifest(
        tmp_path, topic_id="42", clean_title="X", torrent_hash="new", now="2026-07-09T12:00:00+00:00"
    )

    manifest = state.read_manifest(tmp_path)
    assert manifest["first_grabbed_at"] == "2026-01-01T00:00:00+00:00"
    assert manifest["updated_at"] == "2026-07-09T12:00:00+00:00"
    assert manifest["torrent_hash"] == "new"


def test_legacy_added_at_becomes_first_grabbed_at(tmp_path):
    # Манифесты первых версий несли `added_at` — переносим его, а не теряем.
    state.manifest_path(tmp_path).write_text(
        '{"topic_id": "42", "added_at": "2025-12-31T23:00:00+00:00"}', encoding="utf-8"
    )
    state.write_manifest(tmp_path, topic_id="42", clean_title="X", torrent_hash="h")

    assert state.read_manifest(tmp_path)["first_grabbed_at"] == "2025-12-31T23:00:00+00:00"


def test_read_missing_returns_none(tmp_path):
    assert state.read_manifest(tmp_path) is None


def test_already_grabbed_matches_topic(tmp_path):
    state.write_manifest(tmp_path, topic_id="42", clean_title="X", torrent_hash="h")
    assert state.is_already_grabbed(tmp_path, "42") is True
    assert state.is_already_grabbed(tmp_path, "99") is False  # другой topic_id


def test_manifest_in_folder_with_brackets(tmp_path):
    # leaf с [...] — находим через exists(), а не glob (инвариант CLAUDE.md).
    folder = tmp_path / "Название (Реж) [2026] [WEB-DL 2160p, SDR]"
    folder.mkdir()
    state.write_manifest(folder, topic_id="6870556", clean_title="X", torrent_hash="h")
    assert state.is_already_grabbed(folder, "6870556") is True
