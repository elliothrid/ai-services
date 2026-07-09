"""Playwright persistent-context, логин, рендер и полный пайплайн (DESIGN.md §3, §6a, §8).

Здесь — команды (`cmd_login`, `cmd_probe`, `cmd_dry_fetch`, `cmd_grab`, `cmd_batch`)
и работа над одной темой (`grab_one`). Разбор аргументов и exit-коды — в `__main__.py`.

Сеть: Chromium ходит через локальный SOCKS5 (Happ), персистентный профиль хранит
cookie логина между запусками (§6a). Сайт отдаёт `charset=windows-1251`, но браузер
декодирует сам — наружу выдаём готовые Python-str; при печати форсим utf-8.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright

from . import config
from .batch import read_links, run_batch
from .cover import save_cover
from .env_adapter import local_dir, qbit_save_path, sanitize_leaf
from .errors import FsError, NotLoggedIn, PageLoadError, ParseAmbiguous, TopicNotFound
from .interactive import AutoPrompter, ConsolePrompter, Prompter, resolve_questions
from .page_saver import save_page_mhtml
from .reconcile import MHTML_NAME, Report, reconcile
from .rules import LocalRules, load_rules
from .title.parse import parse_title
from .title.validate import validate
from .torrent import (
    add_to_qbit,
    fetch_torrent_bytes,
    qbit_session,
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


# --- Проверки страницы ------------------------------------------------------

def is_logged_in(page: Page) -> bool:
    """Залогинены ли: есть `#logged-in-username` (или ссылка `mode=editprofile`)."""
    return page.locator(_LOGGED_IN_SELECTOR).count() > 0


def get_raw_title(page: Page) -> str:
    """Сырой заголовок темы — текст `h1.maintitle`.

    Нет заголовка -> `TopicNotFound` (а не голый RuntimeError): в списке ссылок
    регулярно попадаются удалённые темы, и батч должен их классифицировать (§12).
    """
    loc = page.locator(_TITLE_SELECTOR)
    if loc.count() == 0:
        raise TopicNotFound(
            f"не найден {_TITLE_SELECTOR} — тема удалена/перенесена "
            f"или это не страница темы: {page.url}"
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
            _goto(page, url)
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
            "Запустите вход: python -m rutracker_grab --login"
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


def _goto(page: Page, url: str) -> None:
    """Открыть страницу, переведя ошибки браузера в `PageLoadError` (§12).

    `wait_until="domcontentloaded"`, а не `"load"`: на странице крутится реклама, и
    ждать все подресурсы — значит регулярно упираться в таймаут. Для заголовка и
    признака логина хватает DOM, а ленивые постеры прогревает `page_saver` (§7).
    """
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=config.PAGE_TIMEOUT_MS)
    except PlaywrightError as exc:
        raise PageLoadError(
            f"страница не открылась за {config.PAGE_TIMEOUT_MS // 1000} с: {url}\n"
            f"{type(exc).__name__}: {str(exc).splitlines()[0]}"
        ) from exc


def _resolve(
    page: Page,
    url: str,
    *,
    prompter: Prompter | None = None,
    rules: LocalRules | None = None,
) -> _Plan:
    """Открыть тему, проверить логин, разобрать и провалидировать заголовок.

    Только вычисления и проверки — ничего на диск не пишет (нужно до идемпотент-чека).
    В интерактивном режиме (§9) незакрытые решения закрываются вопросами, после чего
    заголовок разбирается заново — уже с выученными правилами.
    """
    prompter = prompter or AutoPrompter()
    _goto(page, url)
    if not is_logged_in(page):
        raise NotLoggedIn(
            "Не залогинены на rutracker (нет признака логина на странице).\n"
            "Запустите вход: python -m rutracker_grab --login"
        )

    raw = get_raw_title(page)
    parts = parse_title(raw, rules)
    if resolve_questions(parts.needs_user, prompter):
        parts = parse_title(raw, rules)  # переразбор с только что выученными правилами

    errors = validate(parts)
    if prompter.enabled:
        # Review-чекпоинт §3: показать имя и путь до создания папки, дать поправить.
        clean = prompter.confirm_clean_title(parts.clean_title, errors)
    elif errors:
        raise ParseAmbiguous("заголовок не прошёл инварианты §11: " + "; ".join(errors))
    else:
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

    def __init__(self, context, page: Page, topic_id: str, prompter: Prompter):
        self._context = context
        self._page = page
        self._topic_id = topic_id
        self._prompter = prompter

    def fetch_torrent(self) -> bytes:
        return fetch_torrent_bytes(self._context, self._topic_id)

    def save_page(self, target: Path) -> Path:
        return save_page_mhtml(self._page, target / MHTML_NAME)

    def save_cover(self, target: Path) -> Path | None:
        chooser = self._prompter.choose_cover if self._prompter.enabled else None
        return save_cover(self._page, self._context, target, chooser)

    def in_qbit(self, infohash: str) -> bool:
        return torrent_in_qbit(infohash)

    def add(self, torrent_path: Path, save_path: str, rename: str) -> str | None:
        return add_to_qbit(torrent_path, save_path, rename)

    def recheck(self, torrent_hash: str) -> None:
        recheck_torrent(torrent_hash)


def grab_one(
    context,
    page: Page,
    url: str,
    *,
    force: bool = False,
    prompter: Prompter | None = None,
    rules: LocalRules | None = None,
) -> tuple[_Plan, Report]:
    """Обработать одну тему в уже открытом контексте (§10).

    Каждый артефакт (папка, About.mhtml, folder.jpg, `<leaf>.torrent`, `.grab.json`)
    и наличие раздачи в qBittorrent проверяются независимо; недостающее досоздаётся,
    существующее не трогается. `force` перекачивает и перезаписывает всё.

    Ничего не печатает — это делает вызывающий (одиночный `--grab` или батч).
    """
    prompter = prompter or AutoPrompter()
    tid = topic_id_from_url(url)
    plan = _resolve(page, url, prompter=prompter, rules=rules)
    try:
        report = reconcile(
            target=plan.target,
            leaf=plan.leaf,
            clean=plan.clean,
            qbit_save=plan.qbit_save,
            topic_id=tid,
            deps=_LiveDeps(context, page, tid, prompter),
            force=force,
        )
    except OSError as exc:  # SMB отвалился, нет прав, длинный путь (§12)
        raise FsError(f"не удалось записать артефакты в {plan.target}: {exc}") from exc
    except PlaywrightError as exc:  # снимок, постер, dl.php — всё ходит через браузер
        raise PageLoadError(
            f"браузер не справился с темой t={tid}: "
            f"{type(exc).__name__}: {str(exc).splitlines()[0]}"
        ) from exc
    return plan, report


def _make_prompter(interactive: bool) -> tuple[Prompter, LocalRules]:
    """Промптер и выученные правила (§9). Правила читаем всегда — они и без вопросов
    закрывают то, на что уже отвечали раньше."""
    rules = load_rules()
    prompter = ConsolePrompter(rules) if interactive else AutoPrompter()
    return prompter, rules


def cmd_grab(
    url: str,
    *,
    headed: bool = False,
    verbose: bool = False,
    force: bool = False,
    interactive: bool = False,
) -> int:
    """`--grab <url>`: реконсиляция одной раздачи с подробным выводом."""
    prompter, rules = _make_prompter(interactive)
    with sync_playwright() as p:
        context = _launch_context(p, headless=not headed)
        page = context.new_page()
        try:
            with qbit_session():
                plan, report = grab_one(
                    context, page, url, force=force, prompter=prompter, rules=rules
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


def cmd_batch(
    links_file: str,
    *,
    headed: bool = False,
    verbose: bool = False,
    force: bool = False,
    interactive: bool = False,
) -> int:
    """`<links.txt>`: прогнать список ссылок в одном браузере и одной сессии qBittorrent.

    Один persistent-context и одно подключение к qBittorrent на весь батч (§3, п.5):
    поднимать браузер на каждую ссылку — секунды впустую, да и два процесса на одном
    профиле не уживаются. С `--interactive` вопросы задаются по ходу, на своей теме.
    """
    links = read_links(Path(links_file))
    if not links:
        print(f"В {links_file} нет ссылок (пустые строки и `#`-комментарии пропускаются).")
        return 0

    prompter, rules = _make_prompter(interactive)

    def grab_in_fresh_page(url: str, *, force: bool = False):
        """Своя вкладка на ссылку: после таймаута `goto` в странице остаётся висящая
        навигация, и следующий `goto` встаёт за ней в очередь — одна битая ссылка
        утаскивала за собой все последующие. Контекст (cookie, профиль) общий."""
        page = context.new_page()
        try:
            return grab_one(context, page, url, force=force, prompter=prompter, rules=rules)
        finally:
            try:
                page.close()
            except PlaywrightError:  # вкладка уже мертва — не мешаем следующей ссылке
                pass

    with sync_playwright() as p:
        context = _launch_context(p, headless=not headed)
        try:
            with qbit_session():
                summary = run_batch(
                    links, grab=grab_in_fresh_page, force=force, verbose=verbose
                )
        finally:
            context.close()
    return summary.exit_code()


