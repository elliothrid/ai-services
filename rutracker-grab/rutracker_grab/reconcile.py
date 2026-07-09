"""Реконсиляция раздачи: проверить каждый артефакт и досоздать недостающее (§10).

Раньше идемпотентность работала по принципу «всё или ничего»: есть манифест — не
трогаем ничего. Это ломалось в трёх случаях: `--force` упирался в дубль-проверку
qBittorrent; удалённая из qBittorrent раздача не восстанавливалась; удалённая папка
не пересоздавалась.

Теперь состояние — шесть независимых признаков (`folder`, `mhtml`, `cover`,
`torrent`, `manifest`, `in_qbit`). Каждый недостающий досоздаётся, существующий не
трогается (если нет `--force`). `skipped` печатается, только когда на месте всё
сразу И раздача есть в qBittorrent.

Про сеть: `.torrent` с диска читается как есть — infohash считается из файла, в
`dl.php` не ходим. Скачиваем только если файла нет (или `--force`).

Про recheck: если контент на диске уже лежит, а раздачу мы (пере)добавляем,
qBittorrent начнёт качать её заново — поэтому после добавления зовём
`torrents_recheck`. Полная таблица — в `_needs_recheck`.

ВАЖНО: имя папки (leaf) содержит `[...]` — искать только через `Path.exists()`,
никаких `glob`/`fnmatch`/`Path.glob()` (инвариант в CLAUDE.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from . import state
from .errors import CoverUnavailable, QbitAlreadyPresent
from .torrent import torrent_infohash_v1

MHTML_NAME = "About.mhtml"
COVER_NAME = "folder.jpg"


def torrent_name(leaf: str) -> str:
    """Имя файла раздачи внутри папки: `<leaf>.torrent`."""
    return f"{leaf}.torrent"


class Deps(Protocol):
    """Побочные эффекты, вынесенные наружу — чтобы тесты шли офлайн и без браузера."""

    def fetch_torrent(self) -> bytes: ...
    def save_page(self, target: Path) -> Path: ...
    def save_cover(self, target: Path) -> Path | None: ...
    def in_qbit(self, infohash: str) -> bool: ...
    def add(self, torrent_path: Path, save_path: str, rename: str) -> str | None: ...
    def recheck(self, torrent_hash: str) -> None: ...


@dataclass(frozen=True)
class Artifacts:
    """Что уже лежит в папке раздачи (снимок на начало прогона)."""

    folder: bool
    mhtml: bool
    cover: bool
    torrent: bool
    manifest: bool

    @property
    def all_present(self) -> bool:
        return all((self.folder, self.mhtml, self.cover, self.torrent, self.manifest))


def inspect(target: Path, leaf: str) -> Artifacts:
    """Снять состояние артефактов. Только `exists()` — в `leaf` есть `[...]`."""
    return Artifacts(
        folder=target.exists(),
        mhtml=(target / MHTML_NAME).exists(),
        cover=(target / COVER_NAME).exists(),
        torrent=(target / torrent_name(leaf)).exists(),
        manifest=state.manifest_path(target).exists(),
    )


@dataclass
class Report:
    """Что сделано по каждому артефакту (для печати) + итоговые хеши."""

    skipped: bool
    infohash: str
    torrent_hash: str
    folder: str
    mhtml: str
    cover: str
    torrent: str
    qbit: str
    manifest: str
    rechecked: bool = False

    def lines(self) -> list[str]:
        rows = (
            ("folder", self.folder),
            ("mhtml", self.mhtml),
            ("cover", self.cover),
            ("torrent", self.torrent),
            ("qbit", self.qbit),
            ("manifest", self.manifest),
        )
        return [f"{name + ':':<10}{value}" for name, value in rows]


_OK = "ok (существует)"


def _short(infohash: str) -> str:
    return infohash[:8]


def _needs_recheck(*, qbit_action: str, folder_existed: bool) -> bool:
    """Нужен ли `torrents_recheck` — когда диск и qBittorrent разошлись.

    - `added` + папка была        -> да: контент, возможно, уже лежит, иначе qBittorrent
      начнёт качать заново;
    - `added` + папки не было     -> нет: качать с нуля и так правильно;
    - `already` + папки не было   -> да: раздача есть, а контент удалили вместе с папкой;
    - `already` + папка была      -> нет: расходились только метаданные (mhtml/манифест);
    - `conflict` (409)            -> да: раздача уже была, а мы только что переписали папку.
    """
    if qbit_action == "added":
        return folder_existed
    if qbit_action == "already":
        return not folder_existed
    return qbit_action == "conflict"


def reconcile(
    *,
    target: Path,
    leaf: str,
    clean: str,
    qbit_save: str,
    topic_id: str,
    deps: Deps,
    force: bool = False,
) -> Report:
    """Досоздать недостающие артефакты и довести раздачу до qBittorrent."""
    before = inspect(target, leaf)
    folder_existed = before.folder
    torrent_path = target / torrent_name(leaf)

    # 1) Байты торрента: с диска, если файл есть (в сеть не идём). Хеш нужен до add.
    from_disk = before.torrent and not force
    data = torrent_path.read_bytes() if from_disk else deps.fetch_torrent()
    infohash = torrent_infohash_v1(data)

    # 2) Есть ли раздача в qBittorrent. При --force не спрашиваем: всё равно добавляем.
    in_qbit = False if force else deps.in_qbit(infohash)

    # 3) Всё на месте и раздача в очереди -> skipped (и recheck не делаем).
    if in_qbit and before.all_present:
        return Report(
            skipped=True,
            infohash=infohash,
            torrent_hash=infohash,
            folder=_OK,
            mhtml=_OK,
            cover=_OK,
            torrent=f"ok (с диска, hash={_short(infohash)})",
            qbit=f"ok (уже в qBittorrent, hash={_short(infohash)})",
            manifest=_OK,
        )

    # 4) Папка и файлы: недостающее создаём, существующее не трогаем (если не --force).
    target.mkdir(parents=True, exist_ok=True)
    folder_status = _OK if folder_existed else "создана"

    if before.mhtml and not force:
        mhtml_status = _OK
    else:
        deps.save_page(target)
        mhtml_status = "перезаписан" if before.mhtml else "создан"

    if before.cover and not force:
        cover_status = _OK
    else:
        try:
            saved = deps.save_cover(target)
        except CoverUnavailable as exc:
            # Постер — не повод терять раздачу: доводим тему до конца без folder.jpg.
            # Следующий прогон увидит, что файла нет, и попробует снова (§6).
            cover_status = f"пропущен ({exc})"
        else:
            if saved is None:
                cover_status = "нет постера на странице"
            else:
                cover_status = "перезаписан" if before.cover else "создан"

    if from_disk:
        torrent_status = f"ok (с диска, hash={_short(infohash)})"
    else:
        torrent_path.write_bytes(data)
        verb = "перекачан" if before.torrent else "скачан"
        torrent_status = f"{verb} (hash={_short(infohash)})"

    # 5) qBittorrent: добавляем, если раздачи там нет. 409 — не ошибка, а «уже есть».
    torrent_hash = infohash
    if in_qbit:
        qbit_action = "already"
        qbit_status = "ok (уже в qBittorrent)"
    else:
        try:
            returned = deps.add(torrent_path, qbit_save, clean)
        except QbitAlreadyPresent as exc:
            qbit_action = "conflict"
            qbit_status = "уже был (409)"
            torrent_hash = exc.torrent_hash or infohash
        else:
            qbit_action = "added"
            qbit_status = "добавлен на паузе"
            torrent_hash = returned or infohash

    rechecked = _needs_recheck(qbit_action=qbit_action, folder_existed=folder_existed)
    if rechecked:
        deps.recheck(torrent_hash)
        qbit_status += " + recheck"

    # 6) Манифест пишем всегда: раздача могла обновиться, и старый хеш/дата протухли.
    #    `first_grabbed_at` при этом сохраняется — за это отвечает `state.write_manifest`.
    state.write_manifest(
        target, topic_id=topic_id, clean_title=clean, torrent_hash=torrent_hash
    )
    manifest_status = "обновлён"

    return Report(
        skipped=False,
        infohash=infohash,
        torrent_hash=torrent_hash,
        folder=folder_status,
        mhtml=mhtml_status,
        cover=cover_status,
        torrent=torrent_status,
        qbit=qbit_status,
        manifest=manifest_status,
        rechecked=rechecked,
    )