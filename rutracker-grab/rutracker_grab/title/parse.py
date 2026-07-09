"""Детерминированное ядро разбора заголовка (DESIGN.md §5.1).

Шаги: A (схлопывание языков) -> A2 (нормализация названия, в normalize.py) ->
B (классификация скобок) -> C (трансформация MAIN) -> D (сброс хвоста) ->
E (сборка). Чистые функции, без сети и LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from . import lexicons
from .decisions import Question
from .normalize import normalize_name

if TYPE_CHECKING:  # только для типов: title/ не тянет слой правил в рантайме
    from ..rules import LocalRules

BracketKind = Literal["TYPE", "EPISODES", "MAIN", "RESOLUTION", "AUDIO_SUB", "UNKNOWN"]

_BRACKET_RE = re.compile(r"\[([^\]]*)\]")
_PAREN_RE = re.compile(r"\(([^)]*)\)")


@dataclass
class Bracket:
    """Разобранная скобка `[...]` (§4)."""

    kind: BracketKind
    raw: str                       # исходное содержимое без квадратных скобок
    rendered: str | None           # для сборки; None => выброшено


@dataclass
class TitleParts:
    """Результат разбора заголовка (§4, упрощённый для итерации 1)."""

    ru_title: str
    director: str | None
    brackets: list[Bracket]
    year: str | None = None
    tech: str | None = None
    main_had_format: bool = False
    dropped: list[str] = field(default_factory=list)   # страны/жанры/аудио/сабы/хвост
    warnings: list[str] = field(default_factory=list)
    needs_user: list[Question] = field(default_factory=list)  # незакрытые решения (§5.2)
    clean_title: str = ""


# --- Шаг A: схлопывание языков ---------------------------------------------

def _collapse_language(segment: str) -> str:
    """Взять фрагмент до первого ` / ` (название или содержимое скобки режиссёра)."""
    return segment.split(" / ", 1)[0].strip()


def _split_head(raw: str) -> tuple[str, str]:
    """Разбить на head (название + режиссёр) и остаток (скобки + хвост) по первому `[`."""
    idx = raw.find("[")
    if idx == -1:
        return raw.strip(), ""
    return raw[:idx].strip(), raw[idx:]


def _extract_director(head: str) -> tuple[str, str | None]:
    """Режиссёр = последняя `(...)` в head (перед первым `[`); вернуть (name_region, director_raw)."""
    matches = list(_PAREN_RE.finditer(head))
    if not matches:
        return head.strip(), None
    last = matches[-1]
    name_region = head[: last.start()].strip()
    return name_region, last.group(1).strip()


# --- Шаг B: классификация скобок -------------------------------------------

def classify_bracket(content: str) -> BracketKind:
    """Присвоить kind скобке `[...]` по признакам из §5.1 (порядок важен)."""
    s = content.strip()
    if lexicons.TYPE_BRACKET_RE.match(s):
        return "TYPE"
    if lexicons.EPISODES_RE.search(s):
        return "EPISODES"
    if lexicons.RESOLUTION_RE.match(s):
        return "RESOLUTION"
    if _find_year(s) is not None:
        return "MAIN"
    if lexicons.has_audio_marker(s):
        return "AUDIO_SUB"
    return "UNKNOWN"


def _render_unknown(content: str, rules: LocalRules | None) -> tuple[str | None, bool]:
    """Что делать с UNKNOWN-скобкой: `(rendered, нужен_ли_вопрос)`.

    Выученный ответ (§9): `drop` — выбросить, `keep`/`tech` — оставить как есть.
    Ответа нет -> оставляем как есть и задаём вопрос (не угадываем молча).
    """
    learned = rules.bracket(content) if rules is not None else None
    if learned == "drop":
        return None, False
    if learned in ("keep", "tech"):
        return f"[{content}]", False
    return f"[{content}]", True


def _find_year(content: str) -> str | None:
    """Первый 4-значный токен-год в диапазоне 1900–2100."""
    for m in lexicons.YEAR_RE.finditer(content):
        val = int(m.group(1))
        if 1900 <= val <= 2100:
            return m.group(1)
    return None


# --- Шаг C: трансформация MAIN ---------------------------------------------

def transform_main(
    content: str, extra_tech: tuple[str, ...] = ()
) -> tuple[str | None, str, bool, list[str]]:
    """MAIN-скобка -> (year, tech, had_format, dropped).

    Токены делятся запятыми. year = первый 4-значный токен. tech = от первого
    TECH-токена до конца. Всё между годом и tech (страны/жанры) — выбрасывается.
    `extra_tech` — формат-слова, выученные у пользователя (§9).
    """
    tokens = [t.strip() for t in content.split(",") if t.strip()]

    year: str | None = None
    for t in tokens:
        if re.fullmatch(r"\d{4}", t) and 1900 <= int(t) <= 2100:
            year = t
            break

    tech_start: int | None = None
    for i, t in enumerate(tokens):
        if lexicons.is_tech_start(t, extra_tech):
            tech_start = i
            break

    if tech_start is not None:
        tech_tokens = tokens[tech_start:]
        had_format = True
    else:
        tech_tokens = []
        had_format = False

    # Выброшенное: всё, кроме года и tech (страны, жанры).
    tech_set = set(tech_tokens)
    dropped = [t for t in tokens if t != year and t not in tech_set]

    return year, ", ".join(tech_tokens), had_format, dropped


# --- Оркестрация: A -> A2 -> B -> C -> D -> E ------------------------------

def parse_title(raw: str, rules: LocalRules | None = None) -> TitleParts:
    """Разобрать сырой заголовок в TitleParts + собрать clean_title.

    `rules` — ответы, выученные у пользователя (§9). С ними часть вопросов уже
    закрыта: незнакомая скобка выбрасывается/остаётся без повторного вопроса.
    """
    warnings: list[str] = []
    needs_user: list[Question] = []
    dropped: list[str] = []
    extra_tech = rules.extra_tech() if rules is not None else ()

    head, rest = _split_head(raw)

    # Шаг A: режиссёр + схлопывание языков
    name_region, director_raw = _extract_director(head)
    name = _collapse_language(name_region)
    director = _collapse_language(director_raw) if director_raw else None

    # Шаг A2: нормализация названия
    norm = normalize_name(name, rules)
    ru_title = norm.text
    warnings.extend(norm.warnings)
    needs_user.extend(norm.needs_user)

    # Шаг B + C: разбор и трансформация скобок
    brackets: list[Bracket] = []
    year: str | None = None
    tech: str | None = None
    main_had_format = False

    for m in _BRACKET_RE.finditer(rest):
        content = m.group(1).strip()
        kind = classify_bracket(content)
        if kind in ("TYPE", "EPISODES", "RESOLUTION"):
            brackets.append(Bracket(kind=kind, raw=content, rendered=f"[{content}]"))
        elif kind == "MAIN":
            year, tech, main_had_format, main_dropped = transform_main(content, extra_tech)
            dropped.extend(main_dropped)
            rendered = f"[{year}]"
            if tech:
                rendered += f" [{tech}]"
            brackets.append(Bracket(kind=kind, raw=content, rendered=rendered))
        elif kind == "AUDIO_SUB":
            dropped.append(content)
            brackets.append(Bracket(kind=kind, raw=content, rendered=None))
        else:  # UNKNOWN -> выученный ответ (§9) или вопрос пользователю (§5.2)
            rendered, ask = _render_unknown(content, rules)
            if ask:
                needs_user.append(Question(kind="bracket", value=content))
            if rendered is None:
                dropped.append(content)
            brackets.append(Bracket(kind=kind, raw=content, rendered=rendered))

    # Шаг D: хвост после последней `]` — выбрасывается (не рендерим ничего).

    # Шаг E: сборка
    clean = _assemble(ru_title, director, brackets)

    return TitleParts(
        ru_title=ru_title,
        director=director,
        brackets=brackets,
        year=year,
        tech=tech,
        main_had_format=main_had_format,
        dropped=dropped,
        warnings=warnings,
        needs_user=needs_user,
        clean_title=clean,
    )


def _assemble(ru_title: str, director: str | None, brackets: list[Bracket]) -> str:
    """Шаг E: clean = ru_title [+ ' (director)'] + ' ' + join(kept_brackets)."""
    clean = ru_title
    if director:
        clean += f" ({director})"
    rendered = [b.rendered for b in brackets if b.rendered]
    if rendered:
        clean += " " + " ".join(rendered)
    return clean