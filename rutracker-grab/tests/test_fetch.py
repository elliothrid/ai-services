"""Проверки страницы: признак логина и заголовок темы (DESIGN.md §3, §12).

Офлайн: `page` — фейк с одним локатором.
"""

from __future__ import annotations

import pytest

from rutracker_grab.errors import GrabError, TopicNotFound
from rutracker_grab.fetch import get_raw_title, is_logged_in


class _FakeLocator:
    def __init__(self, count: int, text: str = ""):
        self._count = count
        self._text = text
        self.first = self

    def count(self) -> int:
        return self._count

    def inner_text(self) -> str:
        return self._text


class _FakePage:
    def __init__(self, count: int, text: str = "", url: str = "https://rutracker.org/x"):
        self._locator = _FakeLocator(count, text)
        self.url = url

    def locator(self, selector: str) -> _FakeLocator:
        return self._locator


def test_get_raw_title_strips_whitespace():
    page = _FakePage(1, "  Название (Реж) [2026]  \n")
    assert get_raw_title(page) == "Название (Реж) [2026]"


def test_missing_maintitle_is_topic_not_found():
    # Удалённая/перенесённая тема: должна классифицироваться, а не падать RuntimeError —
    # иначе в батче она уходит в «неожиданное исключение» и печатается без типа (§12).
    page = _FakePage(0, url="https://rutracker.org/forum/viewtopic.php?t=100")
    with pytest.raises(TopicNotFound, match="тема удалена"):
        get_raw_title(page)


def test_topic_not_found_is_a_grab_error():
    # Батч ловит GrabError — иначе одна мёртвая ссылка выглядит как баг в коде.
    assert issubclass(TopicNotFound, GrabError)


def test_is_logged_in_reads_locator_count():
    assert is_logged_in(_FakePage(1)) is True
    assert is_logged_in(_FakePage(0)) is False