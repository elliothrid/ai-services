"""Скачивание dl.php + добавление в qBittorrent (DESIGN.md §8).

- Торрент качаем ТЕМ ЖЕ Playwright-контекстом (те же cookie/прокси), с `Referer`
  на страницу темы — иначе слетает сессия.
- qBittorrent — НАПРЯМУЮ по LAN, без SOCKS5-прокси (§6a): отдельный HTTP-клиент.
"""

from __future__ import annotations

import re
from pathlib import Path

import qbittorrentapi
from playwright.sync_api import BrowserContext

from . import config

_DL_URL = "https://rutracker.org/forum/dl.php?t={tid}"
_TOPIC_URL = "https://rutracker.org/forum/viewtopic.php?t={tid}"
_TID_RE = re.compile(r"[?&]t=(\d+)")


class TorrentNotBittorrent(RuntimeError):
    """dl.php вернул не торрент — обычно истёкшая сессия (-> `--login`), §12."""


class QbitRejected(RuntimeError):
    """qBittorrent не принял раздачу (креды/связь/отказ), §12."""


def topic_id_from_url(url: str) -> str:
    """Вынуть `<id>` из `...viewtopic.php?t=<id>`."""
    m = _TID_RE.search(url)
    if not m:
        raise ValueError(f"не найден topic id (t=<...>) в url: {url!r}")
    return m.group(1)


def download_torrent(context: BrowserContext, topic_id: str, dest: Path) -> Path:
    """Скачать `dl.php?t=<id>` и сохранить `.torrent` в `dest`. Провалидировать bencode."""
    resp = context.request.get(
        _DL_URL.format(tid=topic_id),
        headers={"Referer": _TOPIC_URL.format(tid=topic_id)},
    )
    body = resp.body()
    ctype = resp.headers.get("content-type", "").lower()
    is_torrent = "application/x-bittorrent" in ctype or body[:1] == b"d"
    if not resp.ok or not is_torrent:
        raise TorrentNotBittorrent(
            f"dl.php вернул не торрент (HTTP {resp.status}, content-type {ctype!r}) — "
            "вероятно, сессия истекла.\n"
            "Запустите вход заново: python -m rutracker_grab.fetch --login"
        )
    dest.write_bytes(body)
    return dest


def _field(obj: object, name: str, default: int = 0) -> object:
    """Достать поле из ответа qbittorrent-api (dict или AttrDict-объект)."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def check_add_result(result: object) -> str | None:
    """Проверить ответ `torrents_add`; вернуть torrent_hash или упасть QbitRejected.

    - Новый формат (qbittorrent-api 2026.x): `TorrentsAddedMetadata` с полями
      `success_count`/`failure_count`/`added_torrent_ids`. Успех = success>=1 и
      failure==0; хеш = `added_torrent_ids[0]` (пригодится для идемпотентности §10).
    - Старый формат: строка `"Ok."` (хеша нет — возвращаем None).
    """
    if isinstance(result, str):
        if result == "Ok.":
            return None
        raise QbitRejected(f"qBittorrent отклонил добавление: {result!r}")

    success = _field(result, "success_count")
    failure = _field(result, "failure_count")
    added = _field(result, "added_torrent_ids", default=None) or []
    if success >= 1 and failure == 0:
        return added[0] if added else None
    raise QbitRejected(
        f"qBittorrent не принял раздачу: success_count={success}, "
        f"failure_count={failure}, ответ={result!r}"
    )


def add_to_qbit(torrent_path: Path, save_path: str, rename: str) -> str | None:
    """Добавить `.torrent` в qBittorrent на паузе. Вернуть torrent_hash или упасть."""
    try:
        with qbittorrentapi.Client(
            host=config.QBIT_WEBUI,
            username=config.QBIT_USER,
            password=config.QBIT_PASSWORD,
        ) as client:
            result = client.torrents_add(
                torrent_files=torrent_path.read_bytes(),
                save_path=save_path,
                rename=rename,             # поле «Rename torrent» (§8)
                is_paused=True,            # сначала на паузе, для обкатки (§8)
                content_layout="Original",  # сериалы оставляем как есть (§8)
            )
    except qbittorrentapi.exceptions.APIError as exc:
        raise QbitRejected(f"qBittorrent недоступен или отклонил вход: {exc}") from exc
    return check_add_result(result)
