"""Сохранение страницы одним файлом: MHTML через CDP (DESIGN.md §7).

`Page.captureSnapshot` вшивает картинки в один MHTML-файл. Два нюанса:

1. Данные CDP уже содержат CRLF (MIME-формат). `write_text` на Windows ещё раз
   переводит `\n -> \r\n`, получается `\r\r\n` — Chrome показывает белый экран.
   Поэтому пишем БИНАРНО (`write_bytes`), не трогая переводы строк.
2. Постеры на рутрекере (fastpic/imageban) грузятся лениво. Снимок «сразу» вшивает
   только static.rutracker.cc. Поэтому перед снимком прокручиваем страницу и ждём,
   пока у всех `div.post_body img` появится `naturalWidth > 0` (или таймаут).
"""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError

# Сколько ждём догрузки постеров перед снимком.
_IMAGES_TIMEOUT_MS = 15_000

# Пошаговый скролл до низа — триггерит ленивую загрузку картинок.
_SCROLL_TO_BOTTOM = """
async () => {
  await new Promise((resolve) => {
    let y = 0;
    const step = () => {
      window.scrollTo(0, y);
      y += window.innerHeight;
      if (y < document.body.scrollHeight) {
        setTimeout(step, 50);
      } else {
        window.scrollTo(0, document.body.scrollHeight);
        resolve();
      }
    };
    step();
  });
}
"""

# Единственный критерий готовности: у всех постовых картинок есть размер.
# НЕ полагаемся на networkidle — на странице крутится реклама, сеть не затихает.
_ALL_IMAGES_LOADED = (
    "() => Array.from(document.querySelectorAll('div.post_body img'))"
    ".every((img) => img.naturalWidth > 0)"
)

# Счётчик недогруженных / всего — для предупреждения при мягкой деградации.
_IMAGES_PENDING_COUNT = """
() => {
  const imgs = Array.from(document.querySelectorAll('div.post_body img'));
  return { pending: imgs.filter((i) => !(i.naturalWidth > 0)).length, total: imgs.length };
}
"""


def _wait_for_post_images(page: Page, timeout_ms: int = _IMAGES_TIMEOUT_MS) -> None:
    """Прокрутить страницу и дождаться `naturalWidth>0` у постовых картинок.

    Мягкая деградация: если за `timeout_ms` часть картинок не догрузилась —
    печатаем предупреждение, но снимок всё равно делаем (снаружи).
    """
    page.evaluate(_SCROLL_TO_BOTTOM)
    try:
        page.wait_for_function(_ALL_IMAGES_LOADED, timeout=timeout_ms)
    except PlaywrightTimeoutError:
        stat = page.evaluate(_IMAGES_PENDING_COUNT)
        print(
            f"Предупреждение: {stat['pending']} из {stat['total']} постовых картинок "
            f"не догрузились за {timeout_ms // 1000} с — снимок будет неполным.",
            flush=True,
        )


def save_page_mhtml(page: Page, dest: Path) -> Path:
    """Снять MHTML-снапшот текущей страницы в `dest` (бинарно). Вернуть путь."""
    _wait_for_post_images(page)
    client = page.context.new_cdp_session(page)
    try:
        snap = client.send("Page.captureSnapshot", {"format": "mhtml"})
    finally:
        client.detach()
    # Бинарно: НЕ преобразуем переводы строк, иначе CRLF -> CRCRLF (белый экран).
    dest.write_bytes(snap["data"].encode("utf-8"))
    return dest
