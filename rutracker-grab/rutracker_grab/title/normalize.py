"""Нормализация названия (DESIGN.md §5, Шаг A2) + хук нечёткого слоя (§5.2).

Итерация 1: детерминированная часть Шага A2 — скобки «часть»/«тип» в названии и
замена разделителя ` - ` -> `. `. LLM-провайдер = 'none' (только правила + флаг
needs_user). Чистые функции, без сети.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from . import lexicons
from .decisions import Question

if TYPE_CHECKING:  # только для типов: title/ не зависит от слоя правил в рантайме
    from ..rules import LocalRules

_PAREN_RE = re.compile(r"\(([^)]*)\)")
_MULTISPACE_RE = re.compile(r"\s{2,}")


@dataclass
class NameNormalization:
    """Результат нормализации названия (Шаг A2)."""

    text: str
    warnings: list[str] = field(default_factory=list)
    needs_user: list[Question] = field(default_factory=list)


def normalize_name(name: str, rules: LocalRules | None = None) -> NameNormalization:
    """Шаг A2: обработать скобки внутри названия и заменить разделитель.

    - `(фильм первый)`, `(часть N)` — убрать (drop);
    - `(ТВ)`, `(OVA)`, `(TV)` — оставить (keep);
    - незнакомая скобка — спросить пользователя (`needs_user`), если ответа ещё нет
      в `rules.local.json`; до ответа оставляем как есть (§5.2, §9);
    - ` - ` между фрагментами -> `. ` (всегда, автоматически).
    """
    warnings: list[str] = []
    needs_user: list[Question] = []

    out: list[str] = []
    last = 0
    for m in _PAREN_RE.finditer(name):
        inner = m.group(1).strip()
        kind = lexicons.classify_name_paren(inner)
        if kind == "unknown" and rules is not None:
            learned = rules.name_paren(inner)
            if learned is not None:
                kind = "type" if learned == "keep" else "part"

        out.append(name[last:m.start()])
        if kind == "part":
            # drop: содержимое не добавляем; лишние пробелы схлопнутся ниже.
            pass
        elif kind == "type":
            out.append(m.group(0))
        else:  # unknown -> фолбэк-вопрос (§5.2), пока оставляем как есть
            out.append(m.group(0))
            needs_user.append(Question(kind="name_paren", value=inner))
        last = m.end()
    out.append(name[last:])

    text = "".join(out)
    text = _MULTISPACE_RE.sub(" ", text).strip()

    # Разделитель фрагментов названия: ` - ` -> `. `
    text = text.replace(" - ", ". ")

    return NameNormalization(text=text, warnings=warnings, needs_user=needs_user)


def resolve_unknown(token: str, *, provider: str = "none") -> str | None:
    """Хук нечёткого слоя (§5.2): предложить резолюцию для незнакомого токена/скобки.

    Итерация 1 поддерживает только provider='none' — правила + интерактив,
    без LLM. Возвращает None (решение отдаётся пользователю).
    Провайдеры 'claude-cli'/'local' будут добавлены позже за этим же интерфейсом.
    """
    if provider != "none":
        raise NotImplementedError(f"LLM-провайдер '{provider}' пока не реализован (итерация 1)")
    return None