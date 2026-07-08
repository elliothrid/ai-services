"""Инварианты-валидаторы заголовка (DESIGN.md §11).

После сборки, до записи на диск/в qBittorrent. Непрохождение инварианта ->
интерактив (наверху), а не тихая запись. Здесь — только чистая проверка:
возвращаем список нарушений.
"""

from __future__ import annotations

import re

from .parse import TitleParts

# Windows-нелегальные символы (санитизация до обоих потребителей, §2).
_ILLEGAL_FS = r'<>:"/\|?*'
_ILLEGAL_FS_RE = re.compile(f"[{re.escape(_ILLEGAL_FS)}]")
_YEAR_BRACKET_RE = re.compile(r"\[(\d{4})\]")

# Практический лимит на компонент пути (NTFS/SMB): 255 символов.
_MAX_LEAF_LEN = 255


def sanitize_leaf(name: str) -> str:
    """Санитизировать имя папки (leaf) для Windows: убрать нелегальные символы и
    хвостовые `.`/пробелы. Байт-в-байт результат идёт и в SMB-mkdir, и в save_path."""
    cleaned = _ILLEGAL_FS_RE.sub("", name)
    return cleaned.rstrip(" .")


def validate(parts: TitleParts) -> list[str]:
    """Проверить инварианты §11. Вернуть список нарушений (пусто => ок)."""
    errors: list[str] = []
    clean = parts.clean_title

    # ru_title не пустой
    if not parts.ru_title.strip():
        errors.append("ru_title пуст")

    # Языковой хвост (` / ...`) не просочился в название/режиссёра
    if " / " in parts.ru_title:
        errors.append(f"языковой хвост в ru_title: {parts.ru_title!r}")
    if parts.director and " / " in parts.director:
        errors.append(f"языковой хвост в director: {parts.director!r}")

    # Ровно одна [год]-скобка, год 1900–2100
    years = _YEAR_BRACKET_RE.findall(clean)
    if len(years) != 1:
        errors.append(f"ожидалась ровно одна [год]-скобка, найдено {len(years)}")
    else:
        y = int(years[0])
        if not (1900 <= y <= 2100):
            errors.append(f"год вне диапазона 1900–2100: {y}")

    # tech непустой, если в исходной MAIN был формат-токен
    if parts.main_had_format and not (parts.tech or "").strip():
        errors.append("MAIN содержал формат-токен, но tech пуст")

    # Санитизация не должна менять имя (тихое расхождение недопустимо, §2)
    leaf = sanitize_leaf(clean)
    if leaf != clean:
        errors.append(f"санитизация изменила имя: {clean!r} -> {leaf!r}")

    # Длина и хвостовые `.`/пробелы
    if len(leaf) > _MAX_LEAF_LEN:
        errors.append(f"имя папки длиннее {_MAX_LEAF_LEN}: {len(leaf)}")
    if clean != clean.rstrip(" ."):
        errors.append("имя оканчивается на `.` или пробел")

    return errors