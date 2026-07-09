"""Манифест и идемпотентность (DESIGN.md §10). Всё офлайн, реальная запись в tmp.

Проверяем: манифест есть -> skipped; --force игнорирует манифест; папка с `[...]`
находится через exists() (не glob).
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
    assert manifest["added_at"]  # проставлен


def test_read_missing_returns_none(tmp_path):
    assert state.read_manifest(tmp_path) is None


def test_already_grabbed_matches_topic(tmp_path):
    state.write_manifest(tmp_path, topic_id="42", clean_title="X", torrent_hash="h")
    assert state.is_already_grabbed(tmp_path, "42") is True
    assert state.is_already_grabbed(tmp_path, "99") is False  # другой topic_id


def test_should_skip_manifest_present(tmp_path):
    state.write_manifest(tmp_path, topic_id="42", clean_title="X", torrent_hash="h")
    # Манифест есть и topic совпал -> skipped.
    assert state.should_skip(tmp_path, "42", force=False) is True
    # --force игнорирует манифест -> перезаписываем.
    assert state.should_skip(tmp_path, "42", force=True) is False


def test_should_skip_no_manifest(tmp_path):
    assert state.should_skip(tmp_path, "42", force=False) is False


def test_manifest_in_folder_with_brackets(tmp_path):
    # leaf с [...] — находим через exists(), а не glob (инвариант CLAUDE.md).
    folder = tmp_path / "Название (Реж) [2026] [WEB-DL 2160p, SDR]"
    folder.mkdir()
    state.write_manifest(folder, topic_id="6870556", clean_title="X", torrent_hash="h")
    assert state.is_already_grabbed(folder, "6870556") is True
