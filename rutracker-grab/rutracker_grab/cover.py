"""Выбор и скачивание постера -> folder.jpg (DESIGN.md §6).

Селектор — первый `div.post_body img.postImg.img-right`; из `@src` отрезаем query
(`?r=...`) и качаем оригинал через `context.request.get` (те же cookie и прокси, что
у браузера). Сохраняем ВСЕГДА `folder.jpg`: любой формат декодируем Pillow, альфу
сплющиваем на белый фон (RGBA->RGB), JPEG quality 90.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image
from playwright.sync_api import BrowserContext, Page

_COVER_SELECTOR = "div.post_body img.postImg.img-right"


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


def save_cover(page: Page, context: BrowserContext, dest_dir: Path) -> Path | None:
    """Скачать первый постер `img-right` в `dest_dir/folder.jpg`. None — если постера нет."""
    imgs = page.locator(_COVER_SELECTOR)
    if imgs.count() == 0:
        return None
    src = imgs.first.get_attribute("src")
    if not src:
        return None
    url = _strip_query(src)
    resp = context.request.get(url)
    if not resp.ok:
        raise RuntimeError(f"постер не скачался: HTTP {resp.status} {url}")
    return _flatten_to_jpeg(resp.body(), dest_dir / "folder.jpg")
