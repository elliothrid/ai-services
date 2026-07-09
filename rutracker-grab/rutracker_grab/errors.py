"""Классы ошибок пайплайна (DESIGN.md §12).

Все наследуют `GrabError` — батч ловит базовый класс и группирует по типу в сводке.
Модуль намеренно пустой на зависимости: его импортирует и `batch.py`, и `torrent.py`,
и `fetch.py`, поэтому тянуть сюда Playwright или qbittorrent-api нельзя.

`NotLoggedIn` — особая: она прерывает батч целиком (все остальные ссылки упадут с
той же ошибкой). Остальные изолируются на уровне одной темы.
"""

from __future__ import annotations


class GrabError(RuntimeError):
    """База для всех ожидаемых ошибок пайплайна (§12)."""


class NotLoggedIn(GrabError):
    """Сессия rutracker не залогинена — нужен `--login`. Прерывает батч."""


class BadLink(GrabError):
    """Строка входа не похожа на ссылку на тему (нет `t=<id>`)."""


class ParseAmbiguous(GrabError):
    """Заголовок не прошёл инварианты §11 — на диск не пишем, нужен интерактив (§9)."""


class PageLoadError(GrabError):
    """Страница не загрузилась или браузер отвалился (таймаут, обрыв SOCKS5).

    Оборачивает `playwright.sync_api.Error` (и его подкласс `TimeoutError`): без
    этого таймаут на одной теме пролетал мимо `except GrabError` и ронял весь батч.
    """


class CoverUnavailable(GrabError):
    """Постер найден на странице, но не скачался/не декодировался.

    Наружу из `reconcile` не выходит: постер — не повод терять раздачу, тема
    доводится до конца без `folder.jpg`, а следующий прогон его доберёт (§6, §10).
    """


class TorrentNotBittorrent(GrabError):
    """dl.php вернул не торрент — обычно истёкшая сессия (-> `--login`)."""


class FsError(GrabError):
    """Не удалось создать папку или записать артефакт (SMB отвалился, нет прав)."""


class QbitAuthError(GrabError):
    """qBittorrent недоступен или отклонил вход (auth_log_in)."""


class QbitRejected(GrabError):
    """qBittorrent реально отклонил добавление раздачи."""


class QbitAlreadyPresent(GrabError):
    """Раздача уже в qBittorrent (409 / дубль по хешу) — это skipped, не ошибка (§10).

    Наружу из `reconcile` не выходит: обрабатывается там же.
    """

    def __init__(self, message: str, torrent_hash: str | None = None):
        super().__init__(message)
        self.torrent_hash = torrent_hash