"""Выбор и скачивание постера -> folder.jpg (DESIGN.md §6).

Селектор — первый `div.post_body img.postImg.img-right`; из `@src` отрезаем query
(`?r=...`) и качаем оригинал через `context.request.get` (те же cookie и прокси, что
у браузера). Сохраняем ВСЕГДА `folder.jpg`: любой формат декодируем Pillow, альфу
сплющиваем на белый фон (RGBA->RGB), JPEG quality 90.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from playwright.sync_api import BrowserContext, Page
from playwright.sync_api import Error as PlaywrightError

from . import config
from .errors import CoverUnavailable

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
    """Скачать первый постер `img-right` в `dest_dir/folder.jpg`.

    `None` — постера на странице нет. `CoverUnavailable` — постер есть, но не
    скачался или не декодировался: раздачу из-за картинки не теряем (§6), тему
    доводим до конца, следующий прогон постер доберёт.
    """
    imgs = page.locator(_COVER_SELECTOR)
    if imgs.count() == 0:
        return None
    src = imgs.first.get_attribute("src")
    if not src:
        return None

    url = _strip_query(src)
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
