"""Нормализация названия (DESIGN.md §5, Шаг A2) + хук нечёткого слоя (§5.2).

Итерация 1: детерминированная часть Шага A2 — скобки «часть»/«тип» в названии и
замена разделителя ` - ` -> `. `. LLM-провайдер = 'none' (только правила + флаг
needs_user). Чистые функции, без сети.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import lexicons

_PAREN_RE = re.compile(r"\(([^)]*)\)")
_MULTISPACE_RE = re.compile(r"\s{2,}")


@dataclass
class NameNormalization:
    """Результат нормализации названия (Шаг A2)."""

    text: str
    warnings: list[str] = field(default_factory=list)
    needs_user: list[str] = field(default_factory=list)


def normalize_name(name: str) -> NameNormalization:
    """Шаг A2: обработать скобки внутри названия и заменить разделитель.

    - `(фильм первый)`, `(часть N)` — убрать (drop);
    - `(ТВ)`, `(OVA)`, `(TV)` — оставить (keep);
    - незнакомая скобка — оставить как есть, но пометить needs_user (§5.2);
    - ` - ` между фрагментами -> `. ` (всегда, автоматически).
    """
    warnings: list[str] = []
    needs_user: list[str] = []

    out: list[str] = []
    last = 0
    for m in _PAREN_RE.finditer(name):
        kind = lexicons.classify_name_paren(m.group(1))
        out.append(name[last:m.start()])
        if kind == "part":
            # drop: содержимое не добавляем; лишние пробелы схлопнутся ниже.
            pass
        elif kind == "type":
            out.append(m.group(0))
        else:  # unknown -> фолбэк-вопрос (§5.2), пока оставляем как есть
            out.append(m.group(0))
            needs_user.append(f"скобка названия не классифицирована: ({m.group(1).strip()})")
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