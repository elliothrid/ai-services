"""Скачивание dl.php + добавление в qBittorrent (DESIGN.md §8, §10, §12).

- Торрент качаем ТЕМ ЖЕ Playwright-контекстом (те же cookie/прокси), с `Referer`
  на страницу темы — иначе слетает сессия.
- qBittorrent — НАПРЯМУЮ по LAN, без SOCKS5-прокси (§6a): отдельный HTTP-клиент.

Слой ошибок разделён по месту сбоя (иначе 409-дубль от `torrents_add` маскировался
под «отклонил вход», т.к. `Conflict409Error` — подкласс `APIConnectionError`):
- `auth_log_in` не удался        -> `QbitAuthError`  (недоступен / отклонил вход);
- `torrents_add` вернул 409      -> `QbitAlreadyPresent` (дубль -> skipped, не ошибка);
- `torrents_add` иначе не удался -> `QbitRejected`.
"""

from __future__ import annotations

import hashlib
import re
from contextlib import contextmanager
from pathlib import Path

import qbittorrentapi
from playwright.sync_api import BrowserContext

from . import config

_DL_URL = "https://rutracker.org/forum/dl.php?t={tid}"
_TOPIC_URL = "https://rutracker.org/forum/viewtopic.php?t={tid}"
_TID_RE = re.compile(r"[?&]t=(\d+)")


class TorrentNotBittorrent(RuntimeError):
    """dl.php вернул не торрент — обычно истёкшая сессия (-> `--login`), §12."""


class QbitAuthError(RuntimeError):
    """qBittorrent недоступен или отклонил вход (auth_log_in), §12."""


class QbitRejected(RuntimeError):
    """qBittorrent реально отклонил добавление раздачи, §12."""


class QbitAlreadyPresent(RuntimeError):
    """Раздача уже в qBittorrent (409 / дубль по хешу) — это skipped, не ошибка (§10)."""

    def __init__(self, message: str, torrent_hash: str | None = None):
        super().__init__(message)
        self.torrent_hash = torrent_hash


# --- dl.php -----------------------------------------------------------------

def topic_id_from_url(url: str) -> str:
    """Вынуть `<id>` из `...viewtopic.php?t=<id>`."""
    m = _TID_RE.search(url)
    if not m:
        raise ValueError(f"не найден topic id (t=<...>) в url: {url!r}")
    return m.group(1)


def fetch_torrent_bytes(context: BrowserContext, topic_id: str) -> bytes:
    """Скачать `dl.php?t=<id>` и вернуть байты, провалидировав bencode (без записи)."""
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
    return body


def download_torrent(context: BrowserContext, topic_id: str, dest: Path) -> Path:
    """Скачать `.torrent` и сохранить в `dest`."""
    dest.write_bytes(fetch_torrent_bytes(context, topic_id))
    return dest


def torrent_infohash_v1(data: bytes) -> str:
    """SHA1 сырых байтов словаря `info` = infohash v1 (тот же хеш, что у qBittorrent)."""
    if data[:1] != b"d":
        raise ValueError("не bencode-словарь верхнего уровня")
    i = 1
    while data[i : i + 1] != b"e":
        key, i = _bdecode(data, i)
        value_start = i
        _, i = _bdecode(data, i)
        if key == b"info":
            return hashlib.sha1(data[value_start:i]).hexdigest()
    raise ValueError("в торренте нет словаря info")


def _bdecode(data: bytes, i: int) -> tuple[object, int]:
    """Минимальный bencode-декодер: вернуть (значение, индекс_после)."""
    c = data[i : i + 1]
    if c == b"i":  # integer
        end = data.index(b"e", i)
        return int(data[i + 1 : end]), end + 1
    if c == b"l":  # list
        i += 1
        items = []
        while data[i : i + 1] != b"e":
            item, i = _bdecode(data, i)
            items.append(item)
        return items, i + 1
    if c == b"d":  # dict
        i += 1
        d = {}
        while data[i : i + 1] != b"e":
            key, i = _bdecode(data, i)
            value, i = _bdecode(data, i)
            d[key] = value
        return d, i + 1
    if c.isdigit():  # byte string: <len>:<bytes>
        colon = data.index(b":", i)
        length = int(data[i:colon])
        start = colon + 1
        return data[start : start + length], start + length
    raise ValueError(f"битый bencode на позиции {i}")


# --- qBittorrent (напрямую по LAN, §6a) -------------------------------------

@contextmanager
def _logged_in_client():
    """Клиент qBittorrent с явным логином. Сбой входа -> QbitAuthError (не путать с add)."""
    client = qbittorrentapi.Client(
        host=config.QBIT_WEBUI,
        username=config.QBIT_USER,
        password=config.QBIT_PASSWORD,
    )
    try:
        client.auth_log_in()
    except qbittorrentapi.exceptions.APIError as exc:
        raise QbitAuthError(f"qBittorrent недоступен или отклонил вход: {exc}") from exc
    try:
        yield client
    finally:
        try:
            client.auth_log_out()
        except Exception:  # noqa: BLE001 — logout best-effort
            pass


def torrent_in_qbit(torrent_hash: str) -> bool:
    """Есть ли раздача с таким хешем в qBittorrent (дубль-проверка до записи, §10)."""
    with _logged_in_client() as client:
        found = client.torrents_info(torrent_hashes=torrent_hash)
    return len(found) > 0


def recheck_torrent(torrent_hash: str) -> None:
    """Force recheck: qBittorrent сверит уже лежащие на диске файлы (§10).

    Нужен после добавления раздачи в существующую папку — иначе qBittorrent считает
    контент отсутствующим и качает его заново поверх готового.
    """
    with _logged_in_client() as client:
        try:
            client.torrents_recheck(torrent_hashes=torrent_hash)
        except qbittorrentapi.exceptions.APIError as exc:
            raise QbitRejected(f"qBittorrent отклонил recheck: {exc}") from exc


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
    """Добавить `.torrent` в qBittorrent на паузе. Вернуть torrent_hash.

    409 (дубль) -> QbitAlreadyPresent (skipped, не ошибка). Прочие сбои add ->
    QbitRejected. Сбой входа обрабатывается в `_logged_in_client` (QbitAuthError).
    """
    with _logged_in_client() as client:
        try:
            result = client.torrents_add(
                torrent_files=torrent_path.read_bytes(),
                save_path=save_path,
                rename=rename,             # поле «Rename torrent» (§8)
                is_paused=True,            # сначала на паузе, для обкатки (§8)
                content_layout="Original",  # сериалы оставляем как есть (§8)
            )
        except qbittorrentapi.exceptions.Conflict409Error as exc:
            raise QbitAlreadyPresent(f"qBittorrent: раздача уже добавлена (409): {exc}") from exc
        except qbittorrentapi.exceptions.APIError as exc:
            raise QbitRejected(f"qBittorrent отклонил добавление: {exc}") from exc
    return check_add_result(result)
