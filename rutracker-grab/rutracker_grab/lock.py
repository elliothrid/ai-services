"""Блокировка профиля Chromium: один процесс на профиль (DESIGN.md §6a).

Два процесса на одном `BROWSER_PROFILE_DIR` не уживаются: второй Chromium не видит
cookie и молча рапортует `NotLoggedIn`, отправляя пользователя логиниться без
причины. Хуже — параллельная запись может испортить профиль.

Замок берём у ОС (`msvcrt.locking` на Windows, `fcntl.flock` на POSIX), а не через
PID-файл: ядро снимает его при завершении процесса, поэтому «протухших» замков после
падения или Ctrl+C не остаётся и проверять живость PID не нужно.

Windows блокирует и чтение залоченного диапазона, поэтому кто держит замок пишем в
отдельный файл `.grab.lock.info` — он читается свободно и нужен только для сообщения.
"""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path

from . import config
from .errors import BrowserBusy

LOCK_NAME = ".grab.lock"
INFO_NAME = ".grab.lock.info"

if os.name == "nt":
    import msvcrt

    def _try_lock(handle) -> bool:
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    def _unlock(handle) -> None:
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass

else:  # pragma: no cover — разработка и запуск идут на Windows
    import fcntl

    def _try_lock(handle) -> bool:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    def _unlock(handle) -> None:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass


def _describe_self() -> str:
    return f"pid={os.getpid()} cmd={' '.join(sys.argv)}"


def _read_holder(info_path: Path) -> str:
    try:
        return info_path.read_text(encoding="utf-8").strip()
    except OSError:
        return "<неизвестно>"


@contextmanager
def profile_lock(profile_dir: Path | None = None):
    """Занять профиль на время работы браузера. Занят другим -> `BrowserBusy`."""
    profile_dir = Path(profile_dir or config.BROWSER_PROFILE_DIR)
    profile_dir.mkdir(parents=True, exist_ok=True)
    lock_path = profile_dir / LOCK_NAME
    info_path = profile_dir / INFO_NAME

    handle = open(lock_path, "a+b")  # noqa: SIM115 — держим открытым, пока держим замок
    try:
        handle.seek(0)
        if not _try_lock(handle):
            raise BrowserBusy(
                f"профиль браузера занят другим процессом rutracker-grab: "
                f"{_read_holder(info_path)}\n"
                f"Профиль: {profile_dir}\n"
                "Дождитесь его завершения (или закройте окно браузера) и повторите."
            )
    except BrowserBusy:
        handle.close()
        raise

    try:
        info_path.write_text(_describe_self(), encoding="utf-8")
        yield
    finally:
        _unlock(handle)
        handle.close()
        try:
            info_path.unlink()
        except OSError:  # чужой процесс мог уже перехватить замок и переписать info
            pass