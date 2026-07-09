"""Вопросы пользователю, режимы, выученные правила (DESIGN.md §9).

Два режима, оба — объекты с одним интерфейсом (`Prompter`):

- `AutoPrompter` (по умолчанию): вопросов не задаёт. Незакрытые решения печатает
  предупреждением — «не угадывать молча» относится и к неинтерактивному режиму.
  Нарушение §11 остаётся ошибкой `ParseAmbiguous`, тема пропускается.
- `ConsolePrompter` (`--interactive`): спрашивает через `input()`. Ответы про
  скобки копятся в `rules.local.json` (§9) — в следующий раз вопрос не повторится.

Спрашиваем через stdlib, без `questionary`/`rich`: инструмент личный, новых
зависимостей не заводим, а подмена `stdin`/`stdout` делает тесты офлайновыми.

`ConsolePrompter` требует TTY. Проверка — в `__main__`, до запуска браузера:
иначе батч из планировщика молча повис бы на первом же вопросе.
"""

from __future__ import annotations

import sys
from typing import Callable, Protocol, TextIO

from .env_adapter import sanitize_leaf
from .errors import ParseAmbiguous
from .rules import LocalRules, save_rules
from .title.decisions import Question
from .title.validate import validate_clean_title


class Prompter(Protocol):
    """Интерфейс интерактива. `enabled=False` => вопросы не задаются."""

    enabled: bool

    def answer(self, question: Question) -> str: ...
    def confirm_clean_title(self, proposed: str, problems: list[str]) -> str: ...
    def choose_cover(self, candidates: list[str], *, has_img_right: bool) -> str | None: ...
    def note(self, message: str) -> None: ...


class AutoPrompter:
    """Режим по умолчанию: ничего не спрашивает, но и не молчит."""

    enabled = False

    def __init__(self, out: Callable[[str], None] = print):
        self._out = out

    def answer(self, question: Question) -> str:
        raise RuntimeError("AutoPrompter не отвечает на вопросы; проверяйте .enabled")

    def confirm_clean_title(self, proposed: str, problems: list[str]) -> str:
        raise RuntimeError("AutoPrompter не подтверждает заголовок; проверяйте .enabled")

    def choose_cover(self, candidates: list[str], *, has_img_right: bool) -> str | None:
        """Без вопросов: первый `img-right`, иначе постера нет (поведение до §9)."""
        return candidates[0] if (candidates and has_img_right) else None

    def note(self, message: str) -> None:
        self._out(message)


class ConsolePrompter:
    """Режим `--interactive`: вопросы через stdin, ответы -> `rules.local.json`."""

    enabled = True

    def __init__(
        self,
        rules: LocalRules,
        *,
        stdin: TextIO | None = None,
        out: Callable[[str], None] = print,
        persist: Callable[[LocalRules], object] = save_rules,
    ):
        self._rules = rules
        self._stdin = stdin or sys.stdin
        self._out = out
        self._persist = persist

    # --- ввод -----------------------------------------------------------

    def _readline(self) -> str:
        line = self._stdin.readline()
        if line == "":  # EOF: Ctrl+D или закрытый пайп — не зацикливаемся
            raise ParseAmbiguous("ввод закончился, вопрос остался без ответа")
        return line.strip()

    def _choose(self, prompt: str, options: dict[str, str], default: str) -> str:
        """Спрашивать, пока не получим один из вариантов. Пустой ввод -> `default`."""
        hint = ", ".join(f"{key} — {text}" if text else key for key, text in options.items())
        self._out(f"  {prompt}")
        while True:
            self._out(f"  [{hint}] (Enter = {default}): ")
            raw = self._readline().lower()
            if not raw:
                return default
            if raw in options:
                return raw
            self._out(f"  ! не понял {raw!r}, ожидаю одно из: {', '.join(options)}")

    def note(self, message: str) -> None:
        self._out(message)

    # --- вопросы --------------------------------------------------------

    def answer(self, question: Question) -> str:
        """Спросить про скобку и запомнить ответ в `rules.local.json`."""
        if question.kind == "bracket":
            choice = self._choose(
                f"Скобка не классифицирована: [{question.value}]",
                {"k": "оставить", "d": "выбросить", "t": "это tech"},
                default="k",
            )
            answer = {"k": "keep", "d": "drop", "t": "tech"}[choice]
            self._rules.learn_bracket(question.value, answer)
        else:
            choice = self._choose(
                f"Скобка названия не классифицирована: ({question.value})",
                {"k": "оставить", "d": "выбросить"},
                default="k",
            )
            answer = {"k": "keep", "d": "drop"}[choice]
            self._rules.learn_name_paren(question.value, answer)

        self._persist(self._rules)
        self._out(f"  -> {answer}, запомнено (больше не спрошу)")
        return answer

    def confirm_clean_title(self, proposed: str, problems: list[str]) -> str:
        """Показать заголовок и путь папки, дать отредактировать. Вернуть валидное имя.

        Цикл не выпускает невалидное имя наружу: §11 — гейт, а не рекомендация.
        `s` пропускает тему (`ParseAmbiguous`), как в неинтерактивном режиме.
        """
        current = proposed
        while True:
            self._out("")
            self._out(f"  Заголовок: {current}")
            self._out(f"  Папка:     {sanitize_leaf(current)}")
            for problem in problems:
                self._out(f"  ! {problem}")

            if problems:
                # Невалидное имя принять нельзя — только править или пропустить.
                choice = self._choose(
                    "Имя не проходит §11.",
                    {"e": "править", "s": "пропустить тему"},
                    default="e",
                )
            else:
                choice = self._choose(
                    "Принять?",
                    {"y": "да", "e": "править", "s": "пропустить тему"},
                    default="y",
                )
                if choice == "y":
                    return current

            if choice == "s":
                raise ParseAmbiguous(f"тема пропущена пользователем: {current!r}")

            self._out("  Новое имя (Enter — оставить как есть): ")
            edited = self._readline()
            if edited:
                current = edited
            problems = validate_clean_title(current)

    def choose_cover(self, candidates: list[str], *, has_img_right: bool) -> str | None:
        """Постер: несколько `img-right` — какой брать; ни одного — предложить `postImg`."""
        if not candidates:
            return None
        if has_img_right and len(candidates) == 1:
            return candidates[0]

        if not has_img_right:
            self._out("")
            self._out(f"  Постера `img-right` нет. Первый `postImg`: {candidates[0]}")
            choice = self._choose("Взять его?", {"y": "да", "n": "без постера"}, default="y")
            return candidates[0] if choice == "y" else None

        self._out("")
        self._out("  Несколько постеров `img-right`:")
        for index, url in enumerate(candidates, start=1):
            self._out(f"    {index}) {url}")
        options: dict[str, str] = {str(i): "" for i in range(1, len(candidates) + 1)}
        options["n"] = "без постера"
        choice = self._choose("Какой взять?", options, default="1")
        return None if choice == "n" else candidates[int(choice) - 1]


def ask_yes_no(
    question: str,
    *,
    default: bool = True,
    stdin: TextIO | None = None,
    out: Callable[[str], None] = print,
) -> bool:
    """Спросить да/нет. Нужен и без `--interactive` (подтверждение плана `--review-all`).

    EOF -> `default=False` небезопасен, поэтому на EOF возвращаем `False`: закрытый
    пайп не должен молча запускать запись на диск и в qBittorrent.
    """
    stream = stdin or sys.stdin
    hint = "Y/n" if default else "y/N"
    while True:
        out(f"{question} [{hint}]: ")
        line = stream.readline()
        if line == "":
            out("  ввод закончился — считаю за «нет»")
            return False
        raw = line.strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes", "д", "да"):
            return True
        if raw in ("n", "no", "н", "нет"):
            return False
        out(f"  ! не понял {raw!r}, ожидаю y или n")


def resolve_questions(questions: list[Question], prompter: Prompter) -> bool:
    """Закрыть незакрытые решения. Вернуть True, если правила изменились.

    В неинтерактивном режиме не спрашивает, но печатает предупреждение: молча
    угадывать нельзя даже без `--interactive` (CLAUDE.md).
    """
    if not questions:
        return False
    if not prompter.enabled:
        for question in questions:
            prompter.note(
                f"Предупреждение: {question.text} — оставлено как есть "
                f"(спросить: --interactive)"
            )
        return False
    for question in questions:
        prompter.answer(question)
    return True