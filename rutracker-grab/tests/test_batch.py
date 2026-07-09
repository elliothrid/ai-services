"""Батч: чтение списка, изоляция ошибок, прогресс и сводка (DESIGN.md §3, §12).

Всё офлайн: работу над темой изображает фейковый `grab`, вывод собирается в список.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from rutracker_grab.batch import (
    ERROR,
    OK,
    SEPARATOR,
    SKIPPED,
    read_links,
    run_batch,
    run_review_batch,
)
from rutracker_grab.errors import (
    FsError,
    NotLoggedIn,
    PageLoadError,
    ParseAmbiguous,
    QbitRejected,
)

T1 = "https://rutracker.org/forum/viewtopic.php?t=1111111"
T2 = "https://rutracker.org/forum/viewtopic.php?t=2222222"
T3 = "https://rutracker.org/forum/viewtopic.php?t=3333333"


def _report(*, skipped=False):
    return SimpleNamespace(skipped=skipped, lines=lambda: ["folder:   ok (существует)"])


def _plan(title="Название [2026]"):
    return SimpleNamespace(clean=title)


class _FakeGrab:
    """`grab(url, force=...)`: по url отдаёт заготовленный результат или бросает."""

    def __init__(self, outcomes: dict):
        self._outcomes = outcomes
        self.seen: list[str] = []
        self.forces: list[bool] = []

    def __call__(self, url: str, *, force: bool = False):
        self.seen.append(url)
        self.forces.append(force)
        outcome = self._outcomes[url]
        if isinstance(outcome, BaseException):  # не Exception: нужен и KeyboardInterrupt
            raise outcome
        return outcome


# --- read_links ---------------------------------------------------------------

def test_read_links_skips_comments_and_blanks(tmp_path):
    path = tmp_path / "links.txt"
    path.write_text(
        f"# уже забрана — ожидаем skipped\n{T1}\n\n"
        f"   \n# чистая новая\n  {T2}  \n",
        encoding="utf-8",
    )
    assert read_links(path) == [T1, T2]


def test_read_links_dedupes_by_topic_id(tmp_path):
    path = tmp_path / "links.txt"
    # Один и тот же t=1111111, записанный двумя способами -> одна работа.
    path.write_text(
        f"{T1}\nhttps://rutracker.org/forum/viewtopic.php?f=718&t=1111111\n{T1}\n{T2}\n",
        encoding="utf-8",
    )
    assert read_links(path) == [T1, T2]


def test_read_links_keeps_garbage_for_honest_error(tmp_path):
    path = tmp_path / "links.txt"
    path.write_text(f"{T1}\nне ссылка вовсе\n", encoding="utf-8")
    # Мусор не выбрасываем молча: пусть на нём упадёт BadLink и он попадёт в сводку.
    assert read_links(path) == [T1, "не ссылка вовсе"]


def test_read_links_strips_bom(tmp_path):
    # Блокнот и `Out-File -Encoding utf8` ставят BOM: без utf-8-sig первый `#`
    # перестаёт быть комментарием и уезжает в BadLink.
    path = tmp_path / "links.txt"
    path.write_bytes(f"﻿# комментарий\n{T1}\n".encode("utf-8"))
    assert read_links(path) == [T1]


def test_read_links_empty_file(tmp_path):
    path = tmp_path / "links.txt"
    path.write_text("# только комментарии\n\n", encoding="utf-8")
    assert read_links(path) == []


# --- изоляция ошибок ----------------------------------------------------------

def test_error_on_second_link_does_not_stop_third():
    grab = _FakeGrab({
        T1: (_plan("Первая [2026]"), _report()),
        T2: ParseAmbiguous("незнакомая скобка [XYZ]"),
        T3: (_plan("Третья [2019]"), _report(skipped=True)),
    })
    lines: list[str] = []

    summary = run_batch([T1, T2, T3], grab=grab, out=lines.append)

    assert grab.seen == [T1, T2, T3]  # третья обработана несмотря на падение второй
    assert [r.status for r in summary.results] == [OK, ERROR, SKIPPED]
    assert summary.exit_code() == 1  # были ошибки
    assert summary.aborted is False


def test_page_timeout_does_not_stop_batch():
    """Таймаут Playwright обёрнут в PageLoadError — тема падает, батч едет дальше."""
    grab = _FakeGrab({
        T1: PageLoadError("страница не открылась за 60 с"),
        T2: (_plan("Вторая [2020]"), _report()),
    })
    lines: list[str] = []

    summary = run_batch([T1, T2], grab=grab, out=lines.append)

    assert grab.seen == [T1, T2]
    assert [r.status for r in summary.results] == [ERROR, OK]
    assert summary.aborted is False
    assert "# --- PageLoadError (1) ---" in "\n".join(lines)


def test_unexpected_exception_does_not_stop_batch():
    """Необёрнутая ошибка библиотеки не должна уносить весь прогон (§12)."""
    grab = _FakeGrab({
        T1: RuntimeError("что-то совсем неожиданное"),
        T2: (_plan(), _report()),
    })
    lines: list[str] = []

    summary = run_batch([T1, T2], grab=grab, out=lines.append)

    assert grab.seen == [T1, T2]
    assert summary.results[0].error_type == "RuntimeError"  # видно, что стоит обернуть
    assert summary.results[1].status == OK
    assert summary.exit_code() == 1


def test_keyboard_interrupt_is_not_swallowed():
    """Ctrl+C должен останавливать батч, а не превращаться в строку сводки."""
    grab = _FakeGrab({T1: KeyboardInterrupt(), T2: (_plan(), _report())})

    with pytest.raises(KeyboardInterrupt):
        run_batch([T1, T2], grab=grab, out=lambda _: None)

    assert grab.seen == [T1]


def test_not_logged_in_aborts_batch():
    grab = _FakeGrab({
        T1: (_plan(), _report()),
        T2: NotLoggedIn("Не залогинены на rutracker.\nЗапустите вход: --login"),
        T3: (_plan(), _report()),
    })
    lines: list[str] = []

    summary = run_batch([T1, T2, T3], grab=grab, out=lines.append)

    assert grab.seen == [T1, T2]  # до третьей не дошли
    assert summary.aborted is True
    assert summary.exit_code() == 2
    assert any("батч прерван" in line for line in lines)
    # Многострочная подсказка ужимается до первой строки — прогресс остаётся однострочным.
    assert "\n" not in summary.results[1].progress_line(2, 3)


def test_error_types_are_grouped_in_summary():
    grab = _FakeGrab({
        T1: ParseAmbiguous("санитизация изменила имя"),
        T2: QbitRejected("failure_count=1"),
        T3: FsError("SMB недоступен"),
    })
    lines: list[str] = []

    summary = run_batch([T1, T2, T3], grab=grab, out=lines.append)
    text = "\n".join(lines)

    assert summary.counts[ERROR] == 3
    for error_type in ("ParseAmbiguous", "QbitRejected", "FsError"):
        assert f"# --- {error_type} (1) ---" in text
    # Блок готов к повторному запуску: ссылки — строками, причины — комментариями.
    for url in (T1, T2, T3):
        assert f"  {url}" in lines
    assert "  # SMB недоступен" in lines


# --- сводка и счётчики --------------------------------------------------------

def test_summary_counts_and_exit_code():
    grab = _FakeGrab({
        T1: (_plan(), _report()),
        T2: (_plan(), _report(skipped=True)),
        T3: (_plan(), _report()),
    })
    lines: list[str] = []

    summary = run_batch([T1, T2, T3], grab=grab, out=lines.append)

    assert summary.counts[OK] == 2
    assert summary.counts[SKIPPED] == 1
    assert summary.counts[ERROR] == 0
    assert summary.exit_code() == 0  # ошибок не было
    assert "итого: ok 2, skipped 1, error 0" in lines
    assert not any("проблемные ссылки" in line for line in lines)


def test_progress_line_shape():
    grab = _FakeGrab({T1: (_plan("Ешь. Молись. Худей [2026]"), _report())})
    lines: list[str] = []

    run_batch([T1], grab=grab, out=lines.append)

    assert lines[0] == "[1/1] t=1111111   ok       Ешь. Молись. Худей [2026]"


def test_announce_prints_header_before_work():
    """Вопросы интерактива должны идти под своей ссылкой, а не под предыдущей."""
    seen: list[str] = []

    def grab(url, *, force=False):
        seen.append(f"РАБОТА над {url}")
        return _plan("Первая [2026]"), _report()

    def record(line):
        seen.append(line)

    run_batch([T1], grab=grab, announce=True, out=record)

    # Порядок: заголовок -> работа (где спрашивают) -> строка результата.
    meaningful = [line for line in seen if line.strip()]
    assert meaningful[0] == "[1/1] t=1111111   разбор…"
    assert meaningful[1] == f"РАБОТА над {T1}"
    assert meaningful[2].endswith("Первая [2026]")


def test_separator_between_links_but_not_before_first():
    grab = _FakeGrab({T1: (_plan(), _report()), T2: (_plan(), _report())})
    lines: list[str] = []

    run_batch([T1, T2], grab=grab, announce=True, out=lines.append)

    # Разделитель ровно один — между ссылками, не перед первой и не после последней.
    assert lines.count(SEPARATOR) == 1
    assert lines.index(SEPARATOR) < lines.index("[2/2] t=2222222   разбор…")
    assert lines.index(SEPARATOR) > lines.index("[1/2] t=1111111   разбор…")


def test_no_announce_by_default():
    grab = _FakeGrab({T1: (_plan(), _report())})
    lines: list[str] = []

    run_batch([T1], grab=grab, out=lines.append)

    assert not any("разбор" in line for line in lines)  # без вопросов лишних строк нет
    assert SEPARATOR not in lines


def test_review_all_announces_both_phases():
    fake = _FakeTwoPhase({T1: _plan()})
    lines: list[str] = []

    run_review_batch(
        [T1], resolve=fake.resolve, execute=fake.execute, confirm=_yes,
        announce=True, out=lines.append,
    )

    # Разбор и выполнение — обе фазы спрашивают (постер выбирается в фазе 2).
    assert sum(line == "[1/1] t=1111111   разбор…" for line in lines) == 2


def test_verbose_prints_artifact_lines():
    grab = _FakeGrab({T1: (_plan(), _report())})
    quiet: list[str] = []
    loud: list[str] = []

    run_batch([T1], grab=grab, out=quiet.append)
    run_batch([T1], grab=grab, verbose=True, out=loud.append)

    assert not any("folder:" in line for line in quiet)
    assert "    folder:   ok (существует)" in loud


# --- --force применяется ко всем ссылкам --------------------------------------

def test_force_is_passed_to_every_link():
    grab = _FakeGrab({T1: (_plan(), _report()), T2: (_plan(), _report())})

    run_batch([T1, T2], grab=grab, force=True, out=lambda _: None)

    assert grab.forces == [True, True]


# --- --review-all: сперва план, потом выполнение (§9) -------------------------

class _FakeTwoPhase:
    """`resolve(url)` и `execute(url, plan)`; каждый шаг можно заставить упасть."""

    def __init__(self, resolved: dict, executed: dict | None = None):
        self._resolved = resolved
        self._executed = executed or {}
        self.resolved: list[str] = []
        self.executed: list[str] = []
        self.forces: list[bool] = []

    def resolve(self, url: str):
        self.resolved.append(url)
        outcome = self._resolved[url]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def execute(self, url: str, plan, *, force: bool = False):
        self.executed.append(url)
        self.forces.append(force)
        outcome = self._executed.get(url, _report())
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _yes(_planned):
    return True


def _no(_planned):
    return False


def test_review_all_shows_plan_then_executes():
    fake = _FakeTwoPhase({T1: _plan("Первая [2026]"), T2: _plan("Вторая [2020]")})
    lines: list[str] = []
    seen: list[int] = []

    def confirm(planned):
        seen.append(len(planned))
        # На момент подтверждения не выполнено ничего — в этом весь смысл (§9).
        assert fake.executed == []
        return True

    summary = run_review_batch(
        [T1, T2], resolve=fake.resolve, execute=fake.execute, confirm=confirm, out=lines.append
    )

    assert seen == [2]
    assert fake.resolved == [T1, T2]
    assert fake.executed == [T1, T2]
    assert summary.counts[OK] == 2
    assert summary.exit_code() == 0
    assert any("план" in line for line in lines)


def test_review_all_declined_executes_nothing():
    fake = _FakeTwoPhase({T1: _plan(), T2: _plan()})
    lines: list[str] = []

    summary = run_review_batch(
        [T1, T2], resolve=fake.resolve, execute=fake.execute, confirm=_no, out=lines.append
    )

    assert fake.executed == []
    assert summary.cancelled is True
    assert summary.exit_code() == 0  # отказ от плана — не ошибка
    assert any("не подтверждён" in line for line in lines)


def test_review_all_keeps_broken_link_out_of_the_plan():
    fake = _FakeTwoPhase({
        T1: _plan("Первая [2026]"),
        T2: ParseAmbiguous("незнакомая скобка [XYZ]"),
        T3: _plan("Третья [2019]"),
    })
    seen: list[list] = []

    def confirm(planned):
        seen.append([p.url for p in planned])
        return True

    summary = run_review_batch(
        [T1, T2, T3], resolve=fake.resolve, execute=fake.execute, confirm=confirm,
        out=lambda _: None,
    )

    assert fake.resolved == [T1, T2, T3]   # разбор не остановился на ошибке
    assert seen == [[T1, T3]]              # битая ссылка в план не попала
    assert fake.executed == [T1, T3]
    assert summary.counts[ERROR] == 1
    assert summary.exit_code() == 1


def test_review_all_not_logged_in_aborts_before_any_question():
    fake = _FakeTwoPhase({T1: NotLoggedIn("нет признака логина"), T2: _plan()})
    asked: list[int] = []

    summary = run_review_batch(
        [T1, T2], resolve=fake.resolve, execute=fake.execute,
        confirm=lambda p: asked.append(1) or True, out=lambda _: None,
    )

    assert fake.resolved == [T1]   # до второй не дошли
    assert asked == []             # плана не показывали
    assert fake.executed == []
    assert summary.aborted is True
    assert summary.exit_code() == 2


def test_review_all_isolates_errors_in_execution_phase():
    fake = _FakeTwoPhase(
        {T1: _plan(), T2: _plan(), T3: _plan()},
        executed={T2: FsError("SMB недоступен")},
    )

    summary = run_review_batch(
        [T1, T2, T3], resolve=fake.resolve, execute=fake.execute, confirm=_yes,
        out=lambda _: None,
    )

    assert fake.executed == [T1, T2, T3]   # сбой T2 не помешал T3
    assert [r.status for r in summary.results] == [OK, ERROR, OK]


def test_review_all_nothing_resolved():
    fake = _FakeTwoPhase({T1: ParseAmbiguous("нет года")})
    lines: list[str] = []
    asked: list[int] = []

    summary = run_review_batch(
        [T1], resolve=fake.resolve, execute=fake.execute,
        confirm=lambda p: asked.append(1) or True, out=lines.append,
    )

    assert asked == []  # пустой план не подтверждают
    assert any("нечего выполнять" in line for line in lines)
    assert summary.exit_code() == 1


def test_review_all_passes_force_to_execution():
    fake = _FakeTwoPhase({T1: _plan(), T2: _plan()})

    run_review_batch(
        [T1, T2], resolve=fake.resolve, execute=fake.execute, confirm=_yes,
        force=True, out=lambda _: None,
    )

    assert fake.forces == [True, True]