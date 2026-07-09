"""Лексиконы и предикаты для разбора заголовка (DESIGN.md §5.1).

Только данные + чистые предикаты, без состояния и сети. Всё расширяемо —
незнакомые токены уходят в интерактив/LLM (§5.2), а не ломают разбор.
"""

from __future__ import annotations

import re

# --- Скобки [...] : типы и эпизоды (Шаг B) ---------------------------------

# TYPE: [TV] / [Movie] / [OVA] / [ONA] / [Special] — keep as-is.
TYPE_BRACKET_RE = re.compile(r"^(TV|Movie|OVA|ONA|Special)$", re.IGNORECASE)

# EPISODES: [26 из 26] / [12 of 24] — keep as-is.
EPISODES_RE = re.compile(r"\d+\s*(?:из|of)\s*\d+", re.IGNORECASE)

# RESOLUTION: [1080p] / [2160p] / [720p] — keep as-is.
RESOLUTION_RE = re.compile(r"^\d{3,4}p$", re.IGNORECASE)

# Год внутри скобки -> MAIN. Диапазон проверяется отдельно (§11: 1900–2100).
YEAR_RE = re.compile(r"\b(\d{4})\b")


# --- TECH-лексикон (Шаг C, §5.1) -------------------------------------------

# Формат-слова: только они запускают tech (индекс первого токена, начинающегося
# с формат-слова). Многословные варианты («UHD BDRemux») ловятся по startswith.
TECH_FORMATS: tuple[str, ...] = (
    "UHD BDRemux",
    "UHD BluRay",
    "WEB-DLRip",
    "WEB-DL",
    "WEBRip",
    "HDTVRip",
    "HDRip",
    "BDRemux",
    "BDRip",
    "DVDRip",
    "TVRip",
    "BluRay",
    "Blu-ray",
    "HDTV",
    "DVD5",
    "DVD9",
)

# HDR/цвет и разрешение — часть tech, но НЕ запускают его (идут после формата).
# Держим списком для прозрачности и будущей валидации.
TECH_HDR: tuple[str, ...] = ("SDR", "HDR10+", "HDR10", "HDR", "Dolby Vision", "DV", "HLG")
TECH_RESOLUTION: tuple[str, ...] = ("2160p", "1080p", "720p", "480p", "4K")


def is_tech_start(token: str, extra: tuple[str, ...] = ()) -> bool:
    """Токен начинается с формат-слова (запускает tech).

    `extra` — формат-слова, выученные у пользователя (`rules.local.json`, §9):
    ответ «tech» про незнакомую скобку добавляет её первое слово сюда.
    """
    low = token.strip().lower()
    formats = tuple(f.lower() for f in TECH_FORMATS) + tuple(e.lower() for e in extra)
    return any(low.startswith(fmt) for fmt in formats)


# --- Аудио/саб-маркеры (Шаг B: AUDIO_SUB drop) -----------------------------

# Скобка без года, но с этими маркерами — озвучка/сабы, выбрасывается.
AUDIO_MARKERS: tuple[str, ...] = (
    "RUS",
    "ENG",
    "JAP",
    "GER",
    "FRE",
    "ITA",
    "Sub",
    "Dub",
    "MVO",
    "DVO",
    "AVO",
    "VO",
    "Original",
    "int",
    "ext",
)

_AUDIO_MARKER_RE = re.compile(
    r"(?<![A-Za-zА-Яа-я])(?:" + "|".join(re.escape(m) for m in AUDIO_MARKERS) + r")(?![A-Za-zА-Яа-я])",
    re.IGNORECASE,
)


def has_audio_marker(content: str) -> bool:
    """В содержимом скобки присутствует аудио/саб-маркер."""
    return bool(_AUDIO_MARKER_RE.search(content))


# --- Скобки (...) внутри названия (Шаг A2) ----------------------------------

# «Тип/формат» — оставляем как есть: (ТВ), (TV), (OVA)...
NAME_PAREN_TYPE_RE = re.compile(r"^(ТВ|ТБ|TV|OVA|ONA|Special|Movie|спешл)$", re.IGNORECASE)

# «Порядок/часть» — убираем: (фильм первый), (часть N), (part 2)...
NAME_PAREN_PART_RE = re.compile(r"^(фильм|часть|серия|эпизод|part|episode|vol\.?)\b", re.IGNORECASE)


def classify_name_paren(inner: str) -> str:
    """Классифицировать скобку внутри названия: 'type' | 'part' | 'unknown'."""
    s = inner.strip()
    if NAME_PAREN_TYPE_RE.match(s):
        return "type"
    if NAME_PAREN_PART_RE.match(s):
        return "part"
    return "unknown"