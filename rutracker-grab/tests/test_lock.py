"""Замок профиля: один процесс на BROWSER_PROFILE_DIR (DESIGN.md §6a).

Офлайн, реальные файлы в tmp. Браузер не запускается — проверяем сам замок.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

from rutracker_grab.errors import BrowserBusy, GrabError
from rutracker_grab.lock import INFO_NAME, LOCK_NAME, profile_lock


def test_lock_is_taken_and_released(tmp_path):
    profile = tmp_path / "profile"

    with profile_lock(profile):
        assert (profile / LOCK_NAME).exists()
        assert str(os.getpid()) in (profile / INFO_NAME).read_text(encoding="utf-8")

    # После выхода замок свободен: берём снова без ошибки.
    with profile_lock(profile):
        pass
    assert not (profile / INFO_NAME).exists()


def test_creates_profile_dir(tmp_path):
    profile = tmp_path / "нет" / "такой"
    with profile_lock(profile):
        assert profile.is_dir()


def _run_child(code: str) -> subprocess.CompletedProcess:
    """Дочерний процесс с utf-8: иначе кириллица в сообщении падает на cp1251."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True, text=True, encoding="utf-8", env=env,
    )


def test_second_holder_is_rejected(tmp_path):
    """Второй процесс на том же профиле -> BrowserBusy, а не молчаливый NotLoggedIn."""
    profile = tmp_path / "profile"

    with profile_lock(profile):
        done = _run_child(
            f"""
            import sys
            sys.path.insert(0, {str(_repo_root())!r})
            from rutracker_grab.lock import profile_lock
            from rutracker_grab.errors import BrowserBusy
            try:
                with profile_lock({str(profile)!r}):
                    print("ACQUIRED")
            except BrowserBusy as exc:
                print("BUSY")
                print(exc)
            """
        )

    assert done.returncode == 0, done.stderr
    assert "BUSY" in done.stdout
    assert "ACQUIRED" not in done.stdout
    assert str(os.getpid()) in done.stdout  # в сообщении видно, кто держит


def test_lock_freed_after_child_dies(tmp_path):
    """Замок снимает ОС: после падения процесса «протухшего» файла не остаётся."""
    profile = tmp_path / "profile"
    done = _run_child(
        f"""
        import os, sys
        sys.path.insert(0, {str(_repo_root())!r})
        from rutracker_grab.lock import profile_lock
        with profile_lock({str(profile)!r}):
            os._exit(1)   # аварийный выход: finally не выполнится
        """
    )
    assert done.returncode == 1

    # Файл замка остался, но не заперт — берём без проблем.
    with profile_lock(profile):
        pass


def test_browser_busy_is_a_grab_error():
    # Батч ловит GrabError: занятый профиль не должен выглядеть багом в коде.
    assert issubclass(BrowserBusy, GrabError)


def _repo_root() -> str:
    from pathlib import Path

    return str(Path(__file__).resolve().parent.parent)


@pytest.fixture(autouse=True)
def _no_real_profile(monkeypatch, tmp_path):
    """Страховка: тест никогда не должен трогать настоящий профиль пользователя."""
    from rutracker_grab import config

    monkeypatch.setattr(config, "BROWSER_PROFILE_DIR", tmp_path / "never-used")