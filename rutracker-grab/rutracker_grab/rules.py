"""Выученные ответы пользователя: `rules.local.json` (DESIGN.md §9).

Каждый ответ интерактива про скобку кладётся сюда, и в следующий раз вопрос уже
не задаётся. Файл личный, в git не идёт (`.gitignore`).

Формат:

    {
      "version": 1,
      "brackets":    {"remux":  "tech"},   # keep | drop | tech
      "name_parens": {"фильм третий": "drop"},   # keep | drop
      "tech_tokens": ["remux"]             # доп. формат-слова, запускающие tech (§5.1, Шаг C)
    }

Ключи нормализованы (lower + схлопнутые пробелы), чтобы `[REMUX]` и `[ remux ]`
считались одним и тем же вопросом.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import config

RULES_VERSION = 1

BRACKET_ANSWERS: tuple[str, ...] = ("keep", "drop", "tech")
PAREN_ANSWERS: tuple[str, ...] = ("keep", "drop")


def _key(text: str) -> str:
    """Нормализовать ключ: регистр и лишние пробелы не должны плодить вопросы."""
    return " ".join(text.strip().lower().split())


@dataclass
class LocalRules:
    """Ответы, выученные у пользователя. Пустой экземпляр = «правил нет»."""

    brackets: dict[str, str] = field(default_factory=dict)
    name_parens: dict[str, str] = field(default_factory=dict)
    tech_tokens: list[str] = field(default_factory=list)

    # --- чтение (используется парсером) ---

    def bracket(self, content: str) -> str | None:
        return self.brackets.get(_key(content))

    def name_paren(self, inner: str) -> str | None:
        return self.name_parens.get(_key(inner))

    def extra_tech(self) -> tuple[str, ...]:
        return tuple(self.tech_tokens)

    # --- запись (используется интерактивом) ---

    def learn_bracket(self, content: str, answer: str) -> None:
        if answer not in BRACKET_ANSWERS:
            raise ValueError(f"ответ про скобку должен быть из {BRACKET_ANSWERS}, дано {answer!r}")
        self.brackets[_key(content)] = answer
        if answer == "tech":
            # Первое слово скобки становится формат-словом: `[REMUX 2160p]` -> `remux`
            # начнёт запускать tech и внутри MAIN-скобки (§5.1, Шаг C).
            head = _key(content).split(",")[0].split(" ")[0]
            if head and head not in self.tech_tokens:
                self.tech_tokens.append(head)

    def learn_name_paren(self, inner: str, answer: str) -> None:
        if answer not in PAREN_ANSWERS:
            raise ValueError(f"ответ про скобку названия должен быть из {PAREN_ANSWERS}, дано {answer!r}")
        self.name_parens[_key(inner)] = answer


def load_rules(path: Path | None = None) -> LocalRules:
    """Прочитать `rules.local.json`. Нет файла или он битый -> пустые правила."""
    path = Path(path or config.RULES_LOCAL_PATH)
    if not path.exists():
        return LocalRules()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return LocalRules()
    return LocalRules(
        brackets=dict(data.get("brackets") or {}),
        name_parens=dict(data.get("name_parens") or {}),
        tech_tokens=list(data.get("tech_tokens") or []),
    )


def save_rules(rules: LocalRules, path: Path | None = None) -> Path:
    """Записать `rules.local.json` (utf-8, кириллица как есть)."""
    path = Path(path or config.RULES_LOCAL_PATH)
    data = {
        "version": RULES_VERSION,
        "brackets": rules.brackets,
        "name_parens": rules.name_parens,
        "tech_tokens": rules.tech_tokens,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path