"""Реконсиляция артефактов (DESIGN.md §10). Всё офлайн: ни браузера, ни сети, ни qBittorrent.

Побочные эффекты подменяются `_FakeDeps`, который пишет реальные файлы в tmp и
записывает список вызовов — так видно, ходили ли мы в сеть и звали ли recheck.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rutracker_grab import state
from rutracker_grab.reconcile import COVER_NAME, MHTML_NAME, reconcile, torrent_name
from rutracker_grab.torrent import QbitAlreadyPresent, torrent_infohash_v1

LEAF = "Название (Реж) [2026] [WEB-DL 2160p, SDR]"
CLEAN = "Название (Реж) [2026] [WEB-DL 2160p, SDR]"
QBIT_SAVE = f"/torrents-active/{LEAF}"
TOPIC_ID = "6870556"

_INFO = b"d6:lengthi3e4:name3:foo12:piece lengthi16384e6:pieces0:e"
TORRENT_BYTES = b"d8:announce8:http://x4:info" + _INFO + b"e"
INFOHASH = torrent_infohash_v1(TORRENT_BYTES)


class _FakeDeps:
    def __init__(
        self, *, in_qbit=False, add_exc=None, add_hash=None, has_cover=True,
        torrent_bytes=TORRENT_BYTES,
    ):
        self._bytes = torrent_bytes
        self._in_qbit = in_qbit
        self._add_exc = add_exc
        self._add_hash = add_hash
        self._has_cover = has_cover
        self.calls: list[str] = []
        self.rechecked: list[str] = []
        self.add_args: tuple | None = None

    def fetch_torrent(self) -> bytes:
        self.calls.append("fetch")
        return self._bytes

    def save_page(self, target: Path) -> Path:
        self.calls.append("save_page")
        dest = target / MHTML_NAME
        dest.write_bytes(b"From: <Snapshot>\r\n")
        return dest

    def save_cover(self, target: Path) -> Path | None:
        self.calls.append("save_cover")
        if not self._has_cover:
            return None
        dest = target / COVER_NAME
        dest.write_bytes(b"\xff\xd8jpeg")
        return dest

    def in_qbit(self, infohash: str) -> bool:
        self.calls.append("in_qbit")
        return self._in_qbit

    def add(self, torrent_path: Path, save_path: str, rename: str) -> str | None:
        self.calls.append("add")
        self.add_args = (torrent_path, save_path, rename)
        if self._add_exc is not None:
            raise self._add_exc
        return self._add_hash

    def recheck(self, torrent_hash: str) -> None:
        self.calls.append("recheck")
        self.rechecked.append(torrent_hash)


def _run(target: Path, deps: _FakeDeps, *, force: bool = False):
    return reconcile(
        target=target,
        leaf=LEAF,
        clean=CLEAN,
        qbit_save=QBIT_SAVE,
        topic_id=TOPIC_ID,
        deps=deps,
        force=force,
    )


@pytest.fixture
def target(tmp_path: Path) -> Path:
    # Папка с `[...]` в имени — находится через exists(), не через glob (CLAUDE.md).
    return tmp_path / LEAF


def _make_all_artifacts(target: Path, *, torrent_hash: str = INFOHASH) -> None:
    target.mkdir(parents=True)
    (target / MHTML_NAME).write_bytes(b"From: <Snapshot>\r\n")
    (target / COVER_NAME).write_bytes(b"\xff\xd8jpeg")
    (target / torrent_name(LEAF)).write_bytes(TORRENT_BYTES)
    state.write_manifest(
        target, topic_id=TOPIC_ID, clean_title=CLEAN, torrent_hash=torrent_hash
    )


# --- состояние 1: артефакты на месте, из qBittorrent раздачу удалили -----------

def test_manifest_present_but_not_in_qbit_adds_and_rechecks(target):
    _make_all_artifacts(target)
    deps = _FakeDeps(in_qbit=False, add_hash=INFOHASH)

    report = _run(target, deps)

    assert report.skipped is False
    assert "add" in deps.calls
    assert deps.rechecked == [INFOHASH]  # папка была -> контент на месте -> recheck
    assert "fetch" not in deps.calls  # .torrent взят с диска
    assert report.qbit == "добавлен на паузе + recheck"
    assert report.mhtml == "ok (существует)"
    assert report.folder == "ok (существует)"


# --- состояние 2: папку снесли, раздача в qBittorrent осталась ----------------

def test_folder_missing_but_in_qbit_recreates_artifacts_and_rechecks(target):
    deps = _FakeDeps(in_qbit=True)

    report = _run(target, deps)

    assert report.skipped is False
    assert deps.calls.count("fetch") == 1  # .torrent на диске нет -> качаем
    assert "add" not in deps.calls  # раздача уже в qBittorrent
    assert deps.rechecked == [INFOHASH]  # контент удалён вместе с папкой
    assert report.folder == "создана"
    assert report.mhtml == "создан"
    assert report.cover == "создан"
    assert (target / MHTML_NAME).exists()
    assert (target / COVER_NAME).exists()
    assert (target / torrent_name(LEAF)).exists()
    assert state.read_manifest(target)["topic_id"] == TOPIC_ID


# --- состояние 3: всё на месте -> skipped, без recheck ------------------------

def test_all_present_and_in_qbit_is_skipped(target):
    _make_all_artifacts(target)
    deps = _FakeDeps(in_qbit=True)

    report = _run(target, deps)

    assert report.skipped is True
    assert deps.rechecked == []  # при skipped recheck не делаем
    assert "add" not in deps.calls
    assert "fetch" not in deps.calls
    assert "save_page" not in deps.calls
    assert report.infohash == INFOHASH


# --- состояние 4: --force перезаписывает всё ---------------------------------

def test_force_rewrites_everything(target):
    _make_all_artifacts(target)
    deps = _FakeDeps(in_qbit=True, add_hash=INFOHASH)

    report = _run(target, deps, force=True)

    assert report.skipped is False
    # --force не спрашивает qBittorrent о дубле — иначе печатал бы skipped (баг §10).
    assert "in_qbit" not in deps.calls
    assert deps.calls.count("fetch") == 1  # .torrent перекачан, а не взят с диска
    assert "save_page" in deps.calls and "save_cover" in deps.calls
    assert "add" in deps.calls
    assert deps.rechecked == [INFOHASH]  # папка была -> recheck
    assert report.mhtml == "перезаписан"
    assert report.cover == "перезаписан"
    assert report.torrent.startswith("перекачан")
    assert report.manifest == "обновлён"


def test_force_survives_409_from_add(target):
    _make_all_artifacts(target)
    deps = _FakeDeps(add_exc=QbitAlreadyPresent("уже добавлена (409)"))

    report = _run(target, deps, force=True)

    assert report.skipped is False  # 409 — не ошибка и не skipped
    assert report.qbit == "уже был (409) + recheck"
    assert deps.rechecked == [INFOHASH]


# --- состояние 5: .torrent на диске -> сеть не дёргаем ------------------------

def test_torrent_from_disk_never_hits_network(target):
    target.mkdir(parents=True)
    (target / torrent_name(LEAF)).write_bytes(TORRENT_BYTES)
    deps = _FakeDeps(in_qbit=False, add_hash=INFOHASH)

    report = _run(target, deps)

    assert "fetch" not in deps.calls
    assert report.torrent == f"ok (с диска, hash={INFOHASH[:8]})"
    assert report.infohash == INFOHASH


# --- состояние 6: 409 от torrents_add — не ошибка -----------------------------

def test_conflict_409_is_not_an_error(target):
    deps = _FakeDeps(in_qbit=False, add_exc=QbitAlreadyPresent("дубль", "abc123def456"))

    report = _run(target, deps)  # папки нет -> всё создаём, add -> 409

    assert report.skipped is False
    assert report.qbit == "уже был (409) + recheck"
    assert report.torrent_hash == "abc123def456"  # хеш из исключения, если он есть
    assert deps.rechecked == ["abc123def456"]
    assert state.read_manifest(target)["torrent_hash"] == "abc123def456"


# --- частные случаи ----------------------------------------------------------

def test_fresh_grab_does_not_recheck(target):
    """Папки не было и раздачи не было — качать с нуля, recheck не нужен."""
    deps = _FakeDeps(in_qbit=False, add_hash=INFOHASH)

    report = _run(target, deps)

    assert "add" in deps.calls
    assert deps.rechecked == []
    assert report.qbit == "добавлен на паузе"
    assert report.manifest == "обновлён"


def test_missing_mhtml_only_leaves_torrent_and_qbit_alone(target):
    """Разъехались только метаданные: досоздаём mhtml, qBittorrent не трогаем."""
    _make_all_artifacts(target)
    (target / MHTML_NAME).unlink()
    deps = _FakeDeps(in_qbit=True)

    report = _run(target, deps)

    assert report.skipped is False
    assert report.mhtml == "создан"
    assert report.cover == "ok (существует)"
    assert "add" not in deps.calls
    assert deps.rechecked == []  # контент на месте — пересчитывать нечего
    assert report.qbit == "ok (уже в qBittorrent)"


def test_page_without_poster_reports_missing_cover(target):
    deps = _FakeDeps(in_qbit=False, add_hash=INFOHASH, has_cover=False)

    report = _run(target, deps)

    assert report.cover == "нет постера на странице"
    assert not (target / COVER_NAME).exists()


def test_manifest_refreshed_when_hash_diverges(target):
    """Раздачи в qBittorrent нет -> не skipped; заодно чиним хеш в манифесте."""
    _make_all_artifacts(target, torrent_hash="устаревший")
    deps = _FakeDeps(in_qbit=False, add_hash=INFOHASH)

    report = _run(target, deps)

    assert report.skipped is False
    assert report.manifest == "обновлён"
    assert state.read_manifest(target)["torrent_hash"] == INFOHASH


def test_rerun_updates_hash_and_keeps_first_grabbed_at(target):
    """Раздачу перезалили: хеш и updated_at меняются, first_grabbed_at держится."""
    _run(target, _FakeDeps(in_qbit=False, add_hash=INFOHASH))
    first = state.read_manifest(target)
    assert first["first_grabbed_at"] == first["updated_at"]  # первый прогон

    # Новый .torrent на рутрекере -> другой словарь info -> другой infohash.
    new_info = b"d6:lengthi4e4:name3:bar12:piece lengthi16384e6:pieces0:e"
    updated_bytes = b"d8:announce8:http://x4:info" + new_info + b"e"
    new_hash = torrent_infohash_v1(updated_bytes)
    report = _run(target, _FakeDeps(torrent_bytes=updated_bytes, add_hash=new_hash), force=True)

    second = state.read_manifest(target)
    assert report.manifest == "обновлён"
    assert second["torrent_hash"] == new_hash != first["torrent_hash"]
    assert second["updated_at"] != first["updated_at"]
    assert second["first_grabbed_at"] == first["first_grabbed_at"]


def test_skipped_ignores_stale_manifest_hash(target):
    """Всё на месте и раздача в очереди -> skipped, манифест не переписываем."""
    _make_all_artifacts(target, torrent_hash="устаревший")
    deps = _FakeDeps(in_qbit=True)

    assert _run(target, deps).skipped is True
    assert state.read_manifest(target)["torrent_hash"] == "устаревший"