"""Юнит-тесты разбора заголовка — 4 канонических кейса (DESIGN.md §15).

Offline, без сети/LLM/qBittorrent. Кейсы 1–3 проходят детерминированным ядром;
кейс 4 — с правилами Шага A2 (скобка «часть» drop, ` - ` -> `. `).
"""

from __future__ import annotations

import pytest

from rutracker_grab.title.parse import parse_title
from rutracker_grab.title.validate import validate

# (id, raw, expected_clean)
CASES = [
    (
        "6870556",
        "Ешь. Молись. Худей / Saccharine (Натали Эрика Джеймс / Natalie Erika James) "
        "[2026, Австралия, ужасы, WEB-DL 2160p, SDR] "
        "Dub (UltradoxStudio) + Original Eng + Sub (Rus, Eng)",
        "Ешь. Молись. Худей (Натали Эрика Джеймс) [2026] [WEB-DL 2160p, SDR]",
    ),
    (
        "6770843",
        "Убийца 2. Против всех / Sicario: Day of the Soldado (Стефано Соллима / Stefano Sollima) "
        "[2018, США, Италия, боевик, драма, криминал, UHD BDRemux 2160p, HDR10, Dolby Vision] "
        "Dub (iTunes) + MVO (HDRezka) + VO (Юрий Немахов) + VO (Дмитрий Есарев) "
        "+ Sub Rus, Eng + Original Eng",
        "Убийца 2. Против всех (Стефано Соллима) [2018] [UHD BDRemux 2160p, HDR10, Dolby Vision]",
    ),
    (
        "3994094",
        "Видение Эскафлона (ТВ) / Tenkuu no Escaflowne / Vision of Escaflowne (Аканэ Кадзуки) "
        "[TV] [26 из 26] [RUS(ext),JAP+Sub] "
        "[1996, приключения, фэнтези, романтика, меха, BDRip] [1080p]",
        "Видение Эскафлона (ТВ) (Аканэ Кадзуки) [TV] [26 из 26] [1996] [BDRip] [1080p]",
    ),
    (
        "6842010",
        "Стальной алхимик (фильм первый) - Завоеватель Шамбалы / Gekijouban ... / "
        "Fullmetal Alchemist The Movie: Conqueror of Shamballa (Мидзусима Сэйдзи) "
        "[Movie] [RUS(int) JAP+Sub] "
        "[2005, приключения, драма, история, фэнтези, BDRemux, HDR10] [2160p]",
        "Стальной алхимик. Завоеватель Шамбалы (Мидзусима Сэйдзи) [Movie] [2005] [BDRemux, HDR10] [2160p]",
    ),
]


@pytest.mark.parametrize("topic_id, raw, expected", CASES, ids=[c[0] for c in CASES])
def test_clean_title(topic_id, raw, expected):
    parts = parse_title(raw)
    assert parts.clean_title == expected


@pytest.mark.parametrize("topic_id, raw, expected", CASES, ids=[c[0] for c in CASES])
def test_invariants_hold(topic_id, raw, expected):
    parts = parse_title(raw)
    assert validate(parts) == []


@pytest.mark.parametrize("topic_id, raw, expected", CASES, ids=[c[0] for c in CASES])
def test_no_language_tail(topic_id, raw, expected):
    parts = parse_title(raw)
    assert " / " not in parts.ru_title
    assert not (parts.director and " / " in parts.director)


@pytest.mark.parametrize("topic_id, raw, expected", CASES, ids=[c[0] for c in CASES])
def test_exactly_one_year_bracket(topic_id, raw, expected):
    parts = parse_title(raw)
    assert parts.year is not None
    assert parts.clean_title.count(f"[{parts.year}]") == 1


def test_case4_step_a2_drops_part_paren_and_swaps_separator():
    """Кейс 4: `(фильм первый)` убрана, ` - ` -> `. ` (правила Шага A2)."""
    parts = parse_title(CASES[3][1])
    assert parts.ru_title == "Стальной алхимик. Завоеватель Шамбалы"
    assert "(фильм первый)" not in parts.ru_title
    assert " - " not in parts.ru_title


def test_case3_keeps_type_paren_in_name():
    """Кейс 3: `(ТВ)` — тип, остаётся в названии."""
    parts = parse_title(CASES[2][1])
    assert parts.ru_title == "Видение Эскафлона (ТВ)"