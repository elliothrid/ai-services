"""Playwright persistent-context, логин, рендер и полный пайплайн (DESIGN.md §3, §6a, §8).

CLI: `--login` (headed-вход), `--probe` (чтение заголовка), `--dry-fetch` (папка +
страница + постер, без торрента), `--grab` (полный пайплайн до закачки в qBittorrent).

Сеть: Chromium ходит через локальный SOCKS5 (Happ), персистентный профиль хранит
cookie логина между запусками (§6a). Сайт отдаёт `charset=windows-1251`, но браузер
декодирует сам — наружу выдаём готовые Python-str; при печати форсим utf-8.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from . import config
from .cover import save_cover
from .env_adapter import local_dir, qbit_save_path, sanitize_leaf
from .page_saver import save_page_mhtml
from .reconcile import MHTML_NAME, reconcile
from .title.parse import parse_title
from .title.validate import validate
from .torrent import (
    QbitAuthError,
    QbitRejected,
    TorrentNotBittorrent,
    add_to_qbit,
    fetch_torrent_bytes,
    recheck_torrent,
    topic_id_from_url,
    torrent_in_qbit,
)

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


@dataclass
class _Plan:
    """Итог разбора темы: пути раздачи (ещё без записи на диск)."""

    clean: str
    leaf: str
    target: Path
    qbit_save: str


def _resolve(page: Page, url: str) -> _Plan:
    """Открыть тему, проверить логин, разобрать и провалидировать заголовок.

    Только вычисления и проверки — ничего на диск не пишет (нужно до идемпотент-чека).
    """
    page.goto(url, wait_until="load")
    if not is_logged_in(page):
        raise NotLoggedIn(
            "Не залогинены на rutracker (нет признака логина на странице).\n"
            "Запустите вход: python -m rutracker_grab.fetch --login"
        )
    parts = parse_title(get_raw_title(page))

    errors = validate(parts)
    if errors:
        raise ValidationFailed(
            "заголовок не прошёл инварианты §11:\n  - " + "\n  - ".join(errors)
        )

    clean = parts.clean_title
    leaf = sanitize_leaf(clean)      # одна строка для обоих потребителей (§2)
    return _Plan(clean, leaf, local_dir(leaf), qbit_save_path(leaf))


def _write_artifacts(page: Page, context, target: Path) -> tuple[Path, Path | None]:
    """Создать папку и сохранить `About.mhtml` + `folder.jpg` (для `--dry-fetch`)."""
    target.mkdir(parents=True, exist_ok=True)
    mhtml = save_page_mhtml(page, target / MHTML_NAME)
    cover = save_cover(page, context, target)
    return mhtml, cover


def cmd_dry_fetch(url: str, *, headed: bool = False, verbose: bool = False) -> int:
    """`--dry-fetch <url>`: создать папку, сохранить `About.mhtml` и `folder.jpg`.

    Торрент НЕ качаем, qBittorrent не трогаем.
    """
    with sync_playwright() as p:
        context = _launch_context(p, headless=not headed)
        page = context.new_page()
        try:
            plan = _resolve(page, url)
            mhtml, cover = _write_artifacts(page, context, plan.target)
            if verbose:
                _print_diagnostics(context, page)
        finally:
            context.close()

    print(f"clean:     {plan.clean}")
    print(f"leaf:      {plan.leaf}")
    print(f"local_dir: {plan.target}")
    print(f"qbit_save: {plan.qbit_save}")
    print(f"mhtml:     {mhtml.name}")
    print(f"cover:     {cover.name if cover else '<не найден postImg.img-right>'}")
    return 0


class _LiveDeps:
    """Побочные эффекты `reconcile` на живых браузере и qBittorrent (§10).

    qBittorrent дёргаем прямо по LAN, минуя SOCKS5 (§6a), — контекст браузера при
    этом может быть открыт, они не мешают друг другу.
    """

    def __init__(self, context, page: Page, topic_id: str):
        self._context = context
        self._page = page
        self._topic_id = topic_id

    def fetch_torrent(self) -> bytes:
        return fetch_torrent_bytes(self._context, self._topic_id)

    def save_page(self, target: Path) -> Path:
        return save_page_mhtml(self._page, target / MHTML_NAME)

    def save_cover(self, target: Path) -> Path | None:
        return save_cover(self._page, self._context, target)

    def in_qbit(self, infohash: str) -> bool:
        return torrent_in_qbit(infohash)

    def add(self, torrent_path: Path, save_path: str, rename: str) -> str | None:
        return add_to_qbit(torrent_path, save_path, rename)

    def recheck(self, torrent_hash: str) -> None:
        recheck_torrent(torrent_hash)


def cmd_grab(
    url: str, *, headed: bool = False, verbose: bool = False, force: bool = False
) -> int:
    """`--grab <url>`: реконсиляция раздачи (§10).

    Каждый артефакт (папка, About.mhtml, folder.jpg, `<leaf>.torrent`, `.grab.json`)
    и наличие раздачи в qBittorrent проверяются независимо; недостающее досоздаётся,
    существующее не трогается. `skipped` — только когда на месте всё сразу.
    `--force` перекачивает и перезаписывает всё.
    """
    tid = topic_id_from_url(url)
    with sync_playwright() as p:
        context = _launch_context(p, headless=not headed)
        page = context.new_page()
        try:
            plan = _resolve(page, url)
            report = reconcile(
                target=plan.target,
                leaf=plan.leaf,
                clean=plan.clean,
                qbit_save=plan.qbit_save,
                topic_id=tid,
                deps=_LiveDeps(context, page, tid),
                force=force,
            )
            if verbose:
                _print_diagnostics(context, page)
        finally:
            context.close()

    if report.skipped:
        print(f"skipped: всё на месте, раздача в qBittorrent ({plan.target})")
    print(f"clean:     {plan.clean}")
    print(f"leaf:      {plan.leaf}")
    print(f"local_dir: {plan.target}")
    print(f"qbit_save: {plan.qbit_save}")
    for line in report.lines():
        print(line)
    return 0


def main(argv: list[str] | None = None) -> int:
    # Заголовки rutracker — кириллица; консоль Windows может быть не в utf-8.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        prog="rutracker_grab.fetch",
        description="rutracker-grab: логин, проба заголовка и забор раздачи.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--login", action="store_true", help="headed-вход, cookie в профиль")
    group.add_argument("--probe", metavar="URL", help="headless-чтение заголовка темы")
    group.add_argument(
        "--dry-fetch",
        metavar="URL",
        help="создать папку, сохранить About.mhtml и folder.jpg (без торрента)",
    )
    group.add_argument(
        "--grab",
        metavar="URL",
        help="полный пайплайн: папка + страница + постер + .torrent + закачка на паузе",
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
    parser.add_argument(
        "--force",
        action="store_true",
        help="для --grab: игнорировать все проверки, перекачать и перезаписать всё (§10)",
    )
    args = parser.parse_args(argv)

    if args.login:
        return cmd_login()
    try:
        if args.dry_fetch:
            return cmd_dry_fetch(args.dry_fetch, headed=args.headed, verbose=args.verbose)
        if args.grab:
            return cmd_grab(
                args.grab, headed=args.headed, verbose=args.verbose, force=args.force
            )
        return cmd_probe(args.probe, headed=args.headed, verbose=args.verbose)
    except NotLoggedIn as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2
    except ValidationFailed as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 3
    except TorrentNotBittorrent as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 4
    except QbitAuthError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 5
    except QbitRejected as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 6


if __name__ == "__main__":
    raise SystemExit(main())
