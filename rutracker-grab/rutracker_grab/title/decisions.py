"""Незакрытые решения парсера — то, что нельзя вывести правилами (DESIGN.md §5.2).

Парсер не угадывает молча: всё, что не классифицировано, он складывает в
`TitleParts.needs_user` как `Question`. Дальше их закрывает `interactive.py`
(вопрос пользователю) или, в неинтерактивном режиме, они печатаются
предупреждением. Здесь — только тип данных, без логики и зависимостей.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

QuestionKind = Literal["bracket", "name_paren"]


@dataclass(frozen=True)
class Question:
    """Один незакрытый вопрос: что за элемент и какой у него текст.

    `bracket`    — скобка `[...]`, не попавшая ни в один kind (§5.1, Шаг B).
    `name_paren` — скобка `(...)` в названии, не «часть» и не «тип» (Шаг A2).
    """

    kind: QuestionKind
    value: str

    @property
    def text(self) -> str:
        if self.kind == "bracket":
            return f"скобка не классифицирована: [{self.value}]"
        return f"скобка названия не классифицирована: ({self.value})"

    def __str__(self) -> str:
        return self.text