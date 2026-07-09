"""Выученные ответы: rules.local.json + их влияние на парсер (DESIGN.md §9, §5.2).

Офлайн, запись в tmp. Проверяем, что ответ, данный один раз, закрывает вопрос
навсегда — и что до ответа парсер честно спрашивает, а не угадывает.
"""

from __future__ import annotations

import json

import pytest

from rutracker_grab.rules import LocalRules, load_rules, save_rules
from rutracker_grab.title.parse import parse_title

RAW = "Фильм [2020, США, драма, BDRip 1080p] [REMUX]"


# --- LocalRules --------------------------------------------------------------

def test_roundtrip(tmp_path):
    rules = LocalRules()
    rules.learn_bracket("REMUX", "tech")
    rules.learn_name_paren("фильм третий", "drop")
    path = save_rules(rules, tmp_path / "rules.local.json")

    loaded = load_rules(path)
    assert loaded.bracket("REMUX") == "tech"
    assert loaded.name_paren("фильм третий") == "drop"
    assert loaded.extra_tech() == ("remux",)
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1


def test_missing_file_gives_empty_rules(tmp_path):
    rules = load_rules(tmp_path / "нет-такого.json")
    assert rules.bracket("что угодно") is None
    assert rules.extra_tech() == ()


def test_broken_file_gives_empty_rules(tmp_path):
    path = tmp_path / "rules.local.json"
    path.write_text("{ это не json", encoding="utf-8")
    assert load_rules(path).brackets == {}


def test_keys_are_case_and_space_insensitive():
    rules = LocalRules()
    rules.learn_bracket("  ReMuX  ", "drop")
    # `[REMUX]` и `[ remux ]` — один и тот же вопрос, спрашивать дважды нельзя.
    assert rules.bracket("REMUX") == "drop"
    assert rules.bracket("remux") == "drop"


def test_tech_answer_registers_format_word():
    rules = LocalRules()
    rules.learn_bracket("REMUX 2160p", "tech")
    assert rules.extra_tech() == ("remux",)  # первое слово -> формат-слово


def test_bad_answer_rejected():
    rules = LocalRules()
    with pytest.raises(ValueError):
        rules.learn_bracket("X", "maybe")
    with pytest.raises(ValueError):
        rules.learn_name_paren("X", "tech")  # для скобки названия tech не бывает


# --- влияние правил на парсер ------------------------------------------------

def test_unknown_bracket_asks_when_no_rules():
    parts = parse_title(RAW)
    assert [q.kind for q in parts.needs_user] == ["bracket"]
    assert parts.needs_user[0].value == "REMUX"
    assert "[REMUX]" in parts.clean_title  # до ответа оставляем как есть


def test_learned_drop_removes_bracket_and_question():
    rules = LocalRules()
    rules.learn_bracket("REMUX", "drop")

    parts = parse_title(RAW, rules)

    assert parts.needs_user == []          # вопрос закрыт
    assert "[REMUX]" not in parts.clean_title
    assert "REMUX" in parts.dropped


def test_learned_keep_silences_question():
    rules = LocalRules()
    rules.learn_bracket("REMUX", "keep")

    parts = parse_title(RAW, rules)

    assert parts.needs_user == []
    assert "[REMUX]" in parts.clean_title


def test_learned_tech_token_starts_tech_inside_main():
    """Ответ «tech» добавляет формат-слово: MAIN-скобка теперь режется по нему."""
    raw = "Фильм [2020, США, драма, REMUX 2160p]"

    without = parse_title(raw)
    assert without.tech == ""            # REMUX неизвестен -> tech не начался
    assert "драма" in without.dropped

    rules = LocalRules()
    rules.learn_bracket("REMUX", "tech")
    with_rules = parse_title(raw, rules)

    assert with_rules.tech == "REMUX 2160p"
    assert with_rules.main_had_format is True
    assert with_rules.clean_title == "Фильм [2020] [REMUX 2160p]"


def test_learned_name_paren_drop():
    raw = "Название (Дилогия) (Реж) [2020, BDRip]"

    assert [q.kind for q in parse_title(raw).needs_user] == ["name_paren"]

    rules = LocalRules()
    rules.learn_name_paren("Дилогия", "drop")
    parts = parse_title(raw, rules)

    assert parts.needs_user == []
    assert "Дилогия" not in parts.clean_title
    assert parts.ru_title == "Название"


def test_learned_name_paren_keep():
    raw = "Название (Дилогия) (Реж) [2020, BDRip]"
    rules = LocalRules()
    rules.learn_name_paren("Дилогия", "keep")

    parts = parse_title(raw, rules)

    assert parts.needs_user == []
    assert "(Дилогия)" in parts.ru_title