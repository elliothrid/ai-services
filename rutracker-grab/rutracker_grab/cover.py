"""Выбор и скачивание постера -> folder.jpg (DESIGN.md §6).

Селектор — первый `div.post_body img.postImg.img-right`; из `@src` отрезаем query
(`?r=...`) и качаем оригинал через `context.request.get` (те же cookie и прокси, что
у браузера). Сохраняем ВСЕГДА `folder.jpg`: любой формат декодируем Pillow, альфу
сплющиваем на белый фон (RGBA->RGB), JPEG quality 90.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Protocol

from PIL import Image, UnidentifiedImageError
from playwright.sync_api import BrowserContext, Page
from playwright.sync_api import Error as PlaywrightError

from . import config
from .errors import CoverUnavailable


class Chooser(Protocol):
    """Выбор постера из кандидатов (реализуется интерактивом, §9)."""

    def __call__(self, candidates: list[str], *, has_img_right: bool) -> str | None: ...

_COVER_SELECTOR = "div.post_body img.postImg.img-right"
_FALLBACK_SELECTOR = "div.post_body img.postImg"


def _strip_query(src: str) -> str:
    """Отрезать `?r=...` и прочий query у URL постера."""
    return src.split("?", 1)[0]


def _flatten_to_jpeg(data: bytes, dest: Path) -> Path:
    """Декодировать байты картинки, сплющить альфу на белый фон, сохранить JPEG q90."""
    img = Image.open(io.BytesIO(data))
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        img = img.convert("RGBA")
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[-1])
        img = background
    else:
        img = img.convert("RGB")
    img.save(dest, "JPEG", quality=90)
    return dest


def _srcs(page: Page, selector: str) -> list[str]:
    """Все непустые `@src` по селектору, с отрезанным query."""
    loc = page.locator(selector)
    found = []
    for i in range(loc.count()):
        src = loc.nth(i).get_attribute("src")
        if src:
            found.append(_strip_query(src))
    return found


def cover_candidates(page: Page) -> tuple[list[str], bool]:
    """Кандидаты в постеры: `(urls, есть_ли_img_right)` — фолбэк по §6.

    Сначала `img-right` (в 4 канонических примерах постер именно такой). Если их
    нет — все `postImg`: пусть решает вызывающий (интерактив предложит первый).
    """
    right = _srcs(page, _COVER_SELECTOR)
    if right:
        return right, True
    return _srcs(page, _FALLBACK_SELECTOR), False


def save_cover(
    page: Page,
    context: BrowserContext,
    dest_dir: Path,
    chooser: Chooser | None = None,
) -> Path | None:
    """Скачать постер в `dest_dir/folder.jpg`.

    `chooser(candidates, has_img_right=...)` выбирает url (интерактив §9). Без него —
    прежнее поведение: первый `img-right`, иначе постера нет.

    `None` — постера нет (или пользователь отказался). `CoverUnavailable` — постер
    есть, но не скачался/не декодировался: раздачу из-за картинки не теряем (§6).
    """
    candidates, has_img_right = cover_candidates(page)
    if chooser is None:
        url = candidates[0] if (candidates and has_img_right) else None
    else:
        url = chooser(candidates, has_img_right=has_img_right)
    if not url:
        return None
    try:
        # Постеры лежат на fastpic/imageban: через SOCKS5 дефолтных 30 с не хватает.
        resp = context.request.get(url, timeout=config.REQUEST_TIMEOUT_MS)
        body = resp.body()
    except PlaywrightError as exc:
        raise CoverUnavailable(
            f"постер не скачался за {config.REQUEST_TIMEOUT_MS // 1000} с: {url} "
            f"({type(exc).__name__})"
        ) from exc
    if not resp.ok:
        raise CoverUnavailable(f"постер не скачался: HTTP {resp.status} {url}")

    try:
        return _flatten_to_jpeg(body, dest_dir / "folder.jpg")
    except UnidentifiedImageError as exc:  # битые байты; OSError записи -> FsError выше
        raise CoverUnavailable(f"постер не декодировался: {url} ({exc})") from exc
