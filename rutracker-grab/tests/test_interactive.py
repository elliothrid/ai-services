"""Интерактивный слой: вопросы, правка имени, выбор постера (DESIGN.md §9).

Офлайн: `stdin` — StringIO с заготовленными ответами, вывод собирается в список.
Ни браузера, ни сети, ни записи rules.local.json (persist подменён).
"""

from __future__ import annotations

import io

import pytest

from rutracker_grab.errors import ParseAmbiguous
from rutracker_grab.interactive import (
    AutoPrompter,
    ConsolePrompter,
    ask_yes_no,
    resolve_questions,
)
from rutracker_grab.rules import LocalRules
from rutracker_grab.title.decisions import Question

BRACKET_Q = Question(kind="bracket", value="REMUX")
PAREN_Q = Question(kind="name_paren", value="Дилогия")
VALID = "Фильм [2020] [BDRip]"


def _console(answers: str, rules: LocalRules | None = None):
    """Промптер, читающий ответы из строки. Возвращает (prompter, lines, saved)."""
    lines: list[str] = []
    saved: list[LocalRules] = []
    prompter = ConsolePrompter(
        rules if rules is not None else LocalRules(),
        stdin=io.StringIO(answers),
        out=lines.append,
        persist=saved.append,
    )
    return prompter, lines, saved


# --- AutoPrompter: не спрашивает, но и не молчит ------------------------------

def test_auto_prompter_warns_and_does_not_ask():
    lines: list[str] = []
    prompter = AutoPrompter(out=lines.append)

    changed = resolve_questions([BRACKET_Q], prompter)

    assert changed is False
    assert any("REMUX" in line and "Предупреждение" in line for line in lines)


def test_auto_prompter_refuses_to_answer():
    # Защита от вызова не в том режиме: молча угадывать нельзя.
    with pytest.raises(RuntimeError):
        AutoPrompter().answer(BRACKET_Q)


def test_auto_prompter_cover_keeps_pre_interactive_behaviour():
    auto = AutoPrompter()
    assert auto.choose_cover(["a", "b"], has_img_right=True) == "a"
    assert auto.choose_cover(["a"], has_img_right=False) is None  # postImg не берём


# --- ConsolePrompter: ответы про скобки --------------------------------------

def test_bracket_answer_is_learned_and_persisted():
    rules = LocalRules()
    prompter, lines, saved = _console("d\n", rules)

    assert prompter.answer(BRACKET_Q) == "drop"
    assert rules.bracket("REMUX") == "drop"
    assert saved == [rules]  # записали rules.local.json сразу
    assert any("запомнено" in line for line in lines)


def test_bracket_tech_answer_registers_format_word():
    rules = LocalRules()
    prompter, _, _ = _console("t\n", rules)

    assert prompter.answer(BRACKET_Q) == "tech"
    assert rules.extra_tech() == ("remux",)


def test_empty_input_takes_default():
    rules = LocalRules()
    prompter, _, _ = _console("\n", rules)

    assert prompter.answer(BRACKET_Q) == "keep"  # Enter = k


def test_invalid_input_reasks():
    rules = LocalRules()
    prompter, lines, _ = _console("х\nz\nd\n", rules)

    assert prompter.answer(BRACKET_Q) == "drop"
    assert sum("не понял" in line for line in lines) == 2


def test_name_paren_answer():
    rules = LocalRules()
    prompter, _, _ = _console("d\n", rules)

    assert prompter.answer(PAREN_Q) == "drop"
    assert rules.name_paren("Дилогия") == "drop"


def test_eof_does_not_hang():
    # Закрытый пайп: вопрос остаётся без ответа -> тема пропускается, не зависаем.
    prompter, _, _ = _console("")
    with pytest.raises(ParseAmbiguous):
        prompter.answer(BRACKET_Q)


def test_resolve_questions_asks_each_and_reports_change():
    rules = LocalRules()
    prompter, _, _ = _console("d\nk\n", rules)

    assert resolve_questions([BRACKET_Q, PAREN_Q], prompter) is True
    assert rules.bracket("REMUX") == "drop"
    assert rules.name_paren("Дилогия") == "keep"


def test_resolve_questions_noop_when_empty():
    prompter, _, _ = _console("")
    assert resolve_questions([], prompter) is False


# --- ConsolePrompter: подтверждение и правка заголовка ------------------------

def test_confirm_accepts_by_default():
    prompter, lines, _ = _console("\n")

    assert prompter.confirm_clean_title(VALID, []) == VALID
    assert any("Папка:" in line for line in lines)  # путь показан ДО создания (§3)


def test_confirm_shows_raw_title():
    """Решение о правке принимается глазами: сырой заголовок рядом с очищенным."""
    raw = "Фильм / Movie (Реж / Dir) [2020, США, драма, BDRip] Dub + Sub"
    prompter, lines, _ = _console("\n")

    prompter.confirm_clean_title(VALID, [], raw)

    assert f"  Исходный заголовок: {raw}" in lines
    assert f"  Заголовок:          {VALID}" in lines


def test_raw_title_is_reshown_on_every_edit():
    # Правим дважды: исходник остаётся точкой отсчёта на каждой итерации.
    raw = "Фильм / Movie [2020, BDRip]"
    prompter, lines, _ = _console("e\nБез года\ne\nС годом [2001]\n\n")

    prompter.confirm_clean_title(VALID, [], raw)

    assert sum(f"Исходный заголовок: {raw}" in line for line in lines) == 3


def test_confirm_without_raw_omits_the_line():
    prompter, lines, _ = _console("\n")

    prompter.confirm_clean_title(VALID, [])

    assert not any("Исходный заголовок" in line for line in lines)


def test_edit_replaces_title():
    prompter, _, _ = _console("e\nДругое имя [1999]\n\n")

    assert prompter.confirm_clean_title(VALID, []) == "Другое имя [1999]"


def test_invalid_title_cannot_be_accepted():
    """§11 — гейт: с `:` в имени Enter не выпустит, цикл требует правки."""
    bad = "Фильм: продолжение [2020]"
    problems = ["санитизация изменила имя"]
    # Enter (= править) -> вводим валидное имя -> Enter (= принять).
    prompter, lines, _ = _console("\nФильм. Продолжение [2020]\n\n")

    assert prompter.confirm_clean_title(bad, problems) == "Фильм. Продолжение [2020]"
    assert any("не проходит §11" in line for line in lines)


def test_edit_loop_repeats_until_valid():
    # Первая правка всё ещё невалидна (нет [год]) -> спросят снова.
    prompter, lines, _ = _console("e\nБез года\ne\nС годом [2001]\n\n")

    assert prompter.confirm_clean_title(VALID, []) == "С годом [2001]"
    assert any("ожидалась ровно одна [год]-скобка" in line for line in lines)


def test_skip_raises_parse_ambiguous():
    prompter, _, _ = _console("s\n")

    with pytest.raises(ParseAmbiguous, match="пропущена пользователем"):
        prompter.confirm_clean_title(VALID, [])


# --- ConsolePrompter: выбор постера ------------------------------------------

def test_single_img_right_is_not_asked():
    prompter, lines, _ = _console("")  # ввода нет — значит и вопроса быть не должно

    assert prompter.choose_cover(["https://f/1.png"], has_img_right=True) == "https://f/1.png"
    assert lines == []


def test_choose_among_several():
    prompter, _, _ = _console("2\n")

    chosen = prompter.choose_cover(["https://f/1.png", "https://f/2.png"], has_img_right=True)
    assert chosen == "https://f/2.png"


def test_decline_several():
    prompter, _, _ = _console("n\n")

    assert prompter.choose_cover(["https://f/1.png", "https://f/2.png"], has_img_right=True) is None


def test_post_img_fallback_offered():
    prompter, lines, _ = _console("\n")  # Enter = y

    assert prompter.choose_cover(["https://f/a.png"], has_img_right=False) == "https://f/a.png"
    assert any("img-right` нет" in line for line in lines)


def test_post_img_fallback_declined():
    prompter, _, _ = _console("n\n")

    assert prompter.choose_cover(["https://f/a.png"], has_img_right=False) is None


def test_no_candidates_is_not_a_question():
    prompter, lines, _ = _console("")

    assert prompter.choose_cover([], has_img_right=False) is None
    assert lines == []


# --- ask_yes_no: подтверждение плана --review-all (§9) ------------------------

def _yes_no(answers: str, **kw) -> tuple[bool, list[str]]:
    lines: list[str] = []
    result = ask_yes_no("Выполнить?", stdin=io.StringIO(answers), out=lines.append, **kw)
    return result, lines


@pytest.mark.parametrize("answer", ["y", "yes", "да", "Д", "YES"])
def test_yes_variants(answer):
    assert _yes_no(f"{answer}\n")[0] is True


@pytest.mark.parametrize("answer", ["n", "no", "нет", "Н"])
def test_no_variants(answer):
    assert _yes_no(f"{answer}\n")[0] is False


def test_enter_takes_default():
    assert _yes_no("\n")[0] is True
    assert _yes_no("\n", default=False)[0] is False


def test_reasks_on_garbage():
    result, lines = _yes_no("может быть\ny\n")
    assert result is True
    assert sum("не понял" in line for line in lines) == 1


def test_eof_means_no_even_when_default_yes():
    # Закрытый пайп не должен молча запустить запись на диск и в qBittorrent.
    result, lines = _yes_no("", default=True)
    assert result is False
    assert any("ввод закончился" in line for line in lines)