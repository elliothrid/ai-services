"""Постер -> folder.jpg: выбор, таймаут, мягкая деградация (DESIGN.md §6).

Офлайн: `page` и `context.request` — фейки, картинки рисуются Pillow в память.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image
from playwright.sync_api import Error as PlaywrightError

from rutracker_grab import config
from rutracker_grab.cover import save_cover
from rutracker_grab.errors import CoverUnavailable


def _png(mode: str = "RGB", size=(4, 6)) -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, (255, 0, 0) if mode == "RGB" else (255, 0, 0, 128)).save(buf, "PNG")
    return buf.getvalue()


class _FakeLocator:
    def __init__(self, src: str | None, count: int = 1):
        self._src = src
        self._count = count
        self.first = self

    def count(self) -> int:
        return self._count

    def get_attribute(self, name: str) -> str | None:
        return self._src


class _FakePage:
    def __init__(self, src: str | None, count: int = 1):
        self._locator = _FakeLocator(src, count)

    def locator(self, selector: str) -> _FakeLocator:
        return self._locator


class _FakeResponse:
    def __init__(self, body: bytes, ok: bool = True, status: int = 200):
        self._body = body
        self.ok = ok
        self.status = status

    def body(self) -> bytes:
        return self._body


class _FakeRequest:
    def __init__(self, response=None, exc: Exception | None = None):
        self._response = response
        self._exc = exc
        self.last_url: str | None = None
        self.last_timeout: float | None = None

    def get(self, url: str, timeout: float | None = None):
        self.last_url = url
        self.last_timeout = timeout
        if self._exc is not None:
            raise self._exc
        return self._response


class _FakeContext:
    def __init__(self, request: _FakeRequest):
        self.request = request


def test_saves_folder_jpg_and_strips_query(tmp_path):
    page = _FakePage("https://fastpic.org/big/1.png?r=12345")
    ctx = _FakeContext(_FakeRequest(_FakeResponse(_png())))

    dest = save_cover(page, ctx, tmp_path)

    assert dest == tmp_path / "folder.jpg"
    assert ctx.request.last_url == "https://fastpic.org/big/1.png"  # `?r=...` отрезан
    assert ctx.request.last_timeout == config.REQUEST_TIMEOUT_MS
    assert Image.open(dest).format == "JPEG"


def test_alpha_is_flattened_to_white(tmp_path):
    page = _FakePage("https://imageban.ru/a.png")
    ctx = _FakeContext(_FakeRequest(_FakeResponse(_png("RGBA"))))

    dest = save_cover(page, ctx, tmp_path)

    assert Image.open(dest).mode == "RGB"  # альфы в JPEG быть не может


def test_no_poster_on_page_returns_none(tmp_path):
    page = _FakePage(None, count=0)
    ctx = _FakeContext(_FakeRequest())

    # Постера нет — это не ошибка: тема без картинки штатна.
    assert save_cover(page, ctx, tmp_path) is None


# --- мягкая деградация: постер не должен ронять раздачу (§6) -------------------

def test_timeout_raises_cover_unavailable(tmp_path):
    page = _FakePage("https://fastpic.org/slow.png")
    ctx = _FakeContext(_FakeRequest(exc=PlaywrightError("Timeout 60000ms exceeded")))

    with pytest.raises(CoverUnavailable, match="не скачался за 60 с"):
        save_cover(page, ctx, tmp_path)
    assert not (tmp_path / "folder.jpg").exists()


def test_http_error_raises_cover_unavailable(tmp_path):
    page = _FakePage("https://fastpic.org/gone.png")
    ctx = _FakeContext(_FakeRequest(_FakeResponse(b"", ok=False, status=404)))

    with pytest.raises(CoverUnavailable, match="HTTP 404"):
        save_cover(page, ctx, tmp_path)


def test_broken_bytes_raise_cover_unavailable(tmp_path):
    page = _FakePage("https://fastpic.org/broken.png")
    ctx = _FakeContext(_FakeRequest(_FakeResponse(b"not an image at all")))

    with pytest.raises(CoverUnavailable, match="не декодировался"):
        save_cover(page, ctx, tmp_path)