"""Playwright persistent-context, логин, рендер страницы (DESIGN.md §3, §6a, §8).

Итерация 2a — только чтение страницы через браузер. Ничего не пишем на диск,
`dl.php` и qBittorrent не трогаем.

Сеть: Chromium ходит через локальный SOCKS5 (Happ), персистентный профиль хранит
cookie логина между запусками (§6a). Сайт отдаёт `charset=windows-1251`, но браузер
декодирует сам — наружу выдаём готовые Python-str; при печати форсим utf-8.
"""

from __future__ import annotations

import argparse
import sys

from playwright.sync_api import Page, sync_playwright

from . import config
from .cover import save_cover
from .env_adapter import local_dir, qbit_save_path, sanitize_leaf
from .page_saver import save_page_mhtml
from .title.parse import parse_title
from .title.validate import validate

LOGIN_URL = "https://rutracker.org/forum/login.php"

# Признак залогиненности: блок с именем текущего юзера (#logged-in-username),
# фолбэк — ссылка «редактировать мой профиль». Оба есть только у залогиненного;
# ссылки `logout=` на странице темы нет (подтверждено дампом), потому не годится.
_LOGGED_IN_SELECTOR = "#logged-in-username, a[href*='mode=editprofile']"
_TITLE_SELECTOR = "h1.maintitle"


class NotLoggedIn(RuntimeError):
    """Сессия не залогинена — нужен `--login` (DESIGN.md §12)."""


class ValidationFailed(RuntimeError):
    """Заголовок не прошёл инварианты §11 — на диск не пишем (уходило бы в интерактив)."""


# --- Проверки страницы ------------------------------------------------------

def is_logged_in(page: Page) -> bool:
    """Залогинены ли: есть `#logged-in-username` (или ссылка `mode=editprofile`)."""
    return page.locator(_LOGGED_IN_SELECTOR).count() > 0


def get_raw_title(page: Page) -> str:
    """Сырой заголовок темы — текст `h1.maintitle`."""
    loc = page.locator(_TITLE_SELECTOR)
    if loc.count() == 0:
        raise RuntimeError(
            f"Не найден {_TITLE_SELECTOR} — это точно страница темы (viewtopic.php)?"
        )
    return loc.first.inner_text().strip()


# --- Запуск браузера --------------------------------------------------------

def _launch_context(playwright, *, headless: bool):
    """Persistent-context Chromium через SOCKS5-прокси (§6a).

    Прокси задаётся здесь из config, а не хардкодом в вызывающем коде.
    """
    return playwright.chromium.launch_persistent_context(
        str(config.BROWSER_PROFILE_DIR),
        headless=headless,
        proxy={"server": config.SOCKS5_PROXY},
    )


# --- CLI-команды ------------------------------------------------------------

def cmd_login() -> int:
    """`--login`: headed-режим, вход руками (плюс Cloudflare-челлендж, если есть).

    Ждём, пока пользователь закроет окно браузера — cookie сохранятся в профиле.
    """
    with sync_playwright() as p:
        context = _launch_context(p, headless=False)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        print(
            "Открыт rutracker: войдите в аккаунт (при появлении пройдите "
            "Cloudflare-челлендж вручную), затем ЗАКРОЙТЕ окно браузера.",
            flush=True,
        )
        # timeout=0 — без ограничения: ждём, пока пользователь закроет окно.
        try:
            context.wait_for_event("close", timeout=0)
        except Exception:
            # Контекст мог закрыться раньше, чем мы начали ждать — это норма.
            pass
    print("Профиль сохранён. Проверьте: --probe <url темы>.")
    return 0


# Cookie рутрекера, по наличию которых понятно, что сессия жива (для --verbose).
_RUTRACKER_COOKIES = ("bb_session", "bb_data", "bb_t")


def _print_diagnostics(context, page) -> None:
    """Диагностика --probe под --verbose: печатается при любом исходе, сама не падает."""
    print("--- диагностика ---")
    print(f"profile: {config.BROWSER_PROFILE_DIR}  (exists={config.BROWSER_PROFILE_DIR.exists()})")
    try:
        print(f"url:     {page.url}")
    except Exception as e:  # noqa: BLE001 — диагностика не должна падать
        print(f"url:     <ошибка: {e}>")
    try:
        print(f"title:   {page.title()}")
    except Exception as e:  # noqa: BLE001
        print(f"title:   <ошибка: {e}>")
    try:
        body = page.inner_text("body")[:300]
        print(f"body[:300]: {body!r}")
    except Exception as e:  # noqa: BLE001
        print(f"body[:300]: <ошибка: {e}>")
    try:
        cookies = context.cookies()
        names = {c.get("name") for c in cookies}
        rt = sorted(n for n in names if n and (n in _RUTRACKER_COOKIES or n.startswith("bb_")))
        print(f"cookies: {len(cookies)}; bb_session={'bb_session' in names}; rutracker={rt}")
    except Exception as e:  # noqa: BLE001
        print(f"cookies: <ошибка: {e}>")
    print("-------------------")


def cmd_probe(url: str, *, headed: bool = False, verbose: bool = False) -> int:
    """`--probe <url>`: headless, открыть тему, напечатать сырой и чистый заголовок.

    По умолчанию печатает только `raw:` и `clean:`. `--verbose` добавляет блок
    диагностики (профиль, url, title, начало body, cookie). `--headed`
    (headless=False) держит окно 20 секунд перед закрытием — осмотр глазами.
    """
    raw: str | None = None
    logged_in = False
    with sync_playwright() as p:
        context = _launch_context(p, headless=not headed)
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded")
            logged_in = is_logged_in(page)
            if logged_in:
                raw = get_raw_title(page)
            if headed:
                print("Окно открыто на 20 секунд для осмотра...", flush=True)
                page.wait_for_timeout(20_000)
        finally:
            if verbose:
                _print_diagnostics(context, page)
            context.close()

    if not logged_in:
        raise NotLoggedIn(
            "Не залогинены на rutracker (нет признака логина на странице).\n"
            "Запустите вход: python -m rutracker_grab.fetch --login"
        )

    print(f"raw:   {raw}")
    print(f"clean: {parse_title(raw).clean_title}")
    return 0


def cmd_dry_fetch(url: str, *, headed: bool = False, verbose: bool = False) -> int:
    """`--dry-fetch <url>`: создать папку, сохранить `<leaf>.mhtml` и `folder.jpg`.

    Торрент НЕ качаем, qBittorrent не трогаем. Перед записью прогоняем инварианты
    §11 — при нарушении на диск ничего не пишем (это ушло бы в интерактив).
    """
    with sync_playwright() as p:
        context = _launch_context(p, headless=not headed)
        page = context.new_page()
        cover: Path | None = None
        try:
            page.goto(url, wait_until="load")
            if not is_logged_in(page):
                raise NotLoggedIn(
                    "Не залогинены на rutracker (нет признака логина на странице).\n"
                    "Запустите вход: python -m rutracker_grab.fetch --login"
                )
            raw = get_raw_title(page)
            parts = parse_title(raw)

            errors = validate(parts)
            if errors:
                raise ValidationFailed(
                    "заголовок не прошёл инварианты §11:\n  - " + "\n  - ".join(errors)
                )

            clean = parts.clean_title
            leaf = sanitize_leaf(clean)      # одна строка для обоих потребителей (§2)
            target = local_dir(leaf)
            qbit = qbit_save_path(leaf)

            target.mkdir(parents=True, exist_ok=True)
            mhtml = save_page_mhtml(page, target / "About.mhtml")
            cover = save_cover(page, context, target)

            if verbose:
                _print_diagnostics(context, page)
        finally:
            context.close()

    print(f"clean:     {clean}")
    print(f"leaf:      {leaf}")
    print(f"local_dir: {target}")
    print(f"qbit_save: {qbit}")
    print(f"mhtml:     {mhtml.name}")
    print(f"cover:     {cover.name if cover else '<не найден postImg.img-right>'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    # Заголовки rutracker — кириллица; консоль Windows может быть не в utf-8.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        prog="rutracker_grab.fetch",
        description="Итерация 2a: логин и проба чтения темы через браузер.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--login", action="store_true", help="headed-вход, cookie в профиль")
    group.add_argument("--probe", metavar="URL", help="headless-чтение заголовка темы")
    group.add_argument(
        "--dry-fetch",
        metavar="URL",
        help="создать папку, сохранить <leaf>.mhtml и folder.jpg (без торрента)",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="для --probe: headless=False, окно держится 20 сек (осмотр глазами)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="для --probe: печатать блок диагностики (profile/url/title/body/cookies)",
    )
    args = parser.parse_args(argv)

    if args.login:
        return cmd_login()
    try:
        if args.dry_fetch:
            return cmd_dry_fetch(args.dry_fetch, headed=args.headed, verbose=args.verbose)
        return cmd_probe(args.probe, headed=args.headed, verbose=args.verbose)
    except NotLoggedIn as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2
    except ValidationFailed as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
