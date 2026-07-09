"""CLI: разбор аргументов, режимы, exit-коды (DESIGN.md §3, §12).

    python -m rutracker_grab links.txt         # батч по списку ссылок
    python -m rutracker_grab --login           # headed-вход, cookie в профиль
    python -m rutracker_grab --probe <url>     # прочитать заголовок темы
    python -m rutracker_grab --dry-fetch <url> # папка + страница + постер
    python -m rutracker_grab --grab <url>      # одна тема целиком

Сами команды — в `fetch.py`; здесь только argparse и трансляция ошибок §12 в
exit-коды. Батч сюда не попадает: он изолирует ошибки сам и отдаёт код из сводки.
"""

from __future__ import annotations

import argparse
import sys

from .errors import GrabError
from .fetch import cmd_batch, cmd_dry_fetch, cmd_grab, cmd_login, cmd_probe

# Exit-код на класс ошибки (§12). 1 зарезервирован под «батч закончился с ошибками».
EXIT_CODES = {
    "NotLoggedIn": 2,
    "ParseAmbiguous": 3,
    "TorrentNotBittorrent": 4,
    "QbitAuthError": 5,
    "QbitRejected": 6,
    "FsError": 7,
    "BadLink": 8,
    "PageLoadError": 9,
    "TopicNotFound": 10,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rutracker_grab",
        description="rutracker-grab: логин, проба заголовка, забор раздачи и батч по списку.",
    )
    parser.add_argument(
        "links_file",
        nargs="?",
        metavar="LINKS.TXT",
        help="файл со ссылками: по одной в строке, пустые и `#`-комментарии пропускаются",
    )
    group = parser.add_mutually_exclusive_group()
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
        help="headless=False; для --probe окно держится 20 сек (осмотр глазами)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="для --probe: диагностика (profile/url/title/body/cookies); "
        "для батча: разбор по артефактам на каждую ссылку",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="для --grab и батча: игнорировать все проверки, перекачать и перезаписать всё (§10)",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="спрашивать при неоднозначности: подтвердить/поправить имя, "
        "классифицировать скобки, выбрать постер. Ответы копятся в rules.local.json (§9)",
    )
    parser.add_argument(
        "--review-all",
        action="store_true",
        help="для батча: сперва разобрать все ссылки и показать план, выполнять "
        "только после подтверждения (§9)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    # Заголовки rutracker — кириллица; консоль Windows может быть не в utf-8.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")

    parser = build_parser()
    args = parser.parse_args(argv)

    commands = (args.links_file, args.login, args.probe, args.dry_fetch, args.grab)
    if not any(commands):
        parser.error("укажите файл со ссылками или одну из команд: "
                     "--login | --probe | --dry-fetch | --grab")
    if args.links_file and any(commands[1:]):
        parser.error("файл со ссылками нельзя совмещать с --login/--probe/--dry-fetch/--grab")
    if args.review_all and not args.links_file:
        parser.error("--review-all имеет смысл только для батча (файл со ссылками)")
    if (args.interactive or args.review_all) and not sys.stdin.isatty():
        # Иначе прогон из планировщика/пайпа молча повиснет на первом же вопросе.
        flag = "--interactive" if args.interactive else "--review-all"
        parser.error(f"{flag} требует терминал: stdin не является TTY")

    if args.login:
        return cmd_login()
    try:
        if args.links_file:
            return cmd_batch(
                args.links_file,
                headed=args.headed,
                verbose=args.verbose,
                force=args.force,
                interactive=args.interactive,
                review_all=args.review_all,
            )
        if args.dry_fetch:
            return cmd_dry_fetch(args.dry_fetch, headed=args.headed, verbose=args.verbose)
        if args.grab:
            return cmd_grab(
                args.grab,
                headed=args.headed,
                verbose=args.verbose,
                force=args.force,
                interactive=args.interactive,
            )
        return cmd_probe(args.probe, headed=args.headed, verbose=args.verbose)
    except GrabError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return EXIT_CODES.get(type(exc).__name__, 1)


if __name__ == "__main__":
    raise SystemExit(main())