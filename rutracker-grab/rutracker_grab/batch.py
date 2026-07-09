"""Батч по списку ссылок: изоляция ошибок, прогресс, сводка (DESIGN.md §3, §12).

Модуль намеренно не знает ни про Playwright, ни про qBittorrent: работу над одной
темой выполняет переданный колбэк `grab(url, force=...)`. Так батч тестируется
офлайн, а браузер и сессию qBittorrent поднимает `fetch.py` — по одному разу на
весь прогон (§3, п.5).

`NotLoggedIn` прерывает батч немедленно: остальные ссылки упадут с той же ошибкой,
и осмысленнее один раз сказать «запусти --login», чем напечатать её N раз.
"""

from __future__ import annotations

from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from .errors import GrabError, NotLoggedIn
from .torrent import topic_id_from_url

OK = "ok"
SKIPPED = "skipped"
ERROR = "error"


class _Grab(Protocol):
    def __call__(self, url: str, *, force: bool = False): ...


def read_links(path: Path) -> list[str]:
    """Прочитать ссылки: пропустить пустые строки и `#`-комментарии, снять дубли.

    Дубли снимаются по topic_id (одна тема = одна работа), а не по строке: у одной
    темы бывают разные url (`?f=718&t=42` и `?t=42`). Строки без `t=<id>` дублями
    не считаем и оставляем как есть — пусть на них честно упадёт `BadLink`.

    Читаем как `utf-8-sig`: Блокнот и `Out-File -Encoding utf8` ставят BOM, и он
    прилипает к первой строке — `#`-комментарий перестаёт быть комментарием.
    """
    seen: OrderedDict[str, str] = OrderedDict()
    extras: list[str] = []
    for raw in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            key = topic_id_from_url(line)
        except GrabError:
            extras.append(line)
            continue
        seen.setdefault(key, line)
    return list(seen.values()) + extras


@dataclass
class LinkResult:
    """Итог по одной ссылке: строка прогресса + материал для сводки."""

    url: str
    status: str
    topic_id: str = "?"
    title: str = ""
    error_type: str | None = None
    reason: str = ""

    def progress_line(self, index: int, total: int) -> str:
        tail = f"{self.error_type}: {self.reason}" if self.status == ERROR else self.title
        return f"[{index}/{total}] t={self.topic_id:<9} {self.status:<8} {tail}"


@dataclass
class BatchSummary:
    """Счётчики, проблемные ссылки и exit-код всего прогона."""

    results: list[LinkResult] = field(default_factory=list)
    aborted: bool = False
    cancelled: bool = False   # --review-all: пользователь не подтвердил план

    @property
    def counts(self) -> Counter:
        return Counter(r.status for r in self.results)

    @property
    def failures(self) -> list[LinkResult]:
        return [r for r in self.results if r.status == ERROR]

    def exit_code(self) -> int:
        """0 — всё чисто; 2 — батч прерван (NotLoggedIn); 1 — были ошибки."""
        if self.aborted:
            return 2
        return 1 if self.failures else 0

    def lines(self) -> list[str]:
        counts = self.counts
        out = [
            "",
            f"итого: ok {counts[OK]}, skipped {counts[SKIPPED]}, error {counts[ERROR]}",
        ]
        if self.cancelled:
            out.append("план не подтверждён — ничего не выполнено")
        if self.aborted:
            out.append("батч прерван: не залогинены. Запустите: python -m rutracker_grab --login")
        if not self.failures:
            return out

        # Готово к повторному запуску: причина — `#`-комментарием, ссылка — строкой.
        # `read_links` пропустит комментарии, так что блок можно скормить обратно.
        out.append("")
        out.append("проблемные ссылки (можно сохранить в файл и запустить снова):")
        by_type: OrderedDict[str, list[LinkResult]] = OrderedDict()
        for result in self.failures:
            by_type.setdefault(result.error_type or "GrabError", []).append(result)
        for error_type, group in by_type.items():
            out.append(f"  # --- {error_type} ({len(group)}) ---")
            for result in group:
                out.append(f"  # {result.reason}")
                out.append(f"  {result.url}")
        return out


def _reason(exc: BaseException) -> str:
    """Первая строка сообщения: в подсказках бывает многострочный текст."""
    return str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__


def _topic_id_or_unknown(url: str) -> str:
    try:
        return topic_id_from_url(url)
    except GrabError:
        return "?"


def _record_error(summary: BatchSummary, url: str, exc: BaseException) -> LinkResult:
    result = LinkResult(
        url=url,
        status=ERROR,
        topic_id=_topic_id_or_unknown(url),
        error_type=type(exc).__name__,
        reason=_reason(exc),
    )
    summary.results.append(result)
    return result


def _isolated(summary: BatchSummary, url: str, index: int, total: int, work, out):
    """Выполнить `work()` с изоляцией §12. Вернуть `(ok, value)`.

    Любое исключение, кроме `NotLoggedIn`, попадает в сводку — батч едет дальше.
    `NotLoggedIn` ставит `aborted`: остальные ссылки упадут с той же ошибкой.
    `KeyboardInterrupt` — `BaseException`, сюда не попадает: Ctrl+C работает.
    """
    try:
        return True, work()
    except NotLoggedIn as exc:
        result = _record_error(summary, url, exc)
        summary.aborted = True
        out(result.progress_line(index, total))
        return False, None
    except Exception as exc:  # noqa: BLE001 — изоляция §12 сильнее аккуратности
        # Необёрнутая ошибка библиотеки тоже не должна уносить батч. Имя класса
        # уйдёт в сводку, и станет видно, что стоит обернуть явно.
        result = _record_error(summary, url, exc)
        out(result.progress_line(index, total))
        return False, None


def _record_report(summary: BatchSummary, url: str, plan, report, index, total, verbose, out) -> None:
    result = LinkResult(
        url=url,
        status=SKIPPED if report.skipped else OK,
        topic_id=_topic_id_or_unknown(url),
        title=plan.clean,
    )
    summary.results.append(result)
    out(result.progress_line(index, total))
    if verbose:
        for line in report.lines():
            out(f"    {line}")


def run_batch(
    links: list[str],
    *,
    grab: _Grab,
    force: bool = False,
    verbose: bool = False,
    out: Callable[[str], None] = print,
) -> BatchSummary:
    """Прогнать список ссылок. Сбой на одной не мешает остальным (§12).

    `grab(url, force=...)` должен вернуть `(plan, report)`. Любое исключение, кроме
    `NotLoggedIn`, ловится и попадает в сводку — батч едет дальше. `NotLoggedIn`
    останавливает прогон.
    """
    summary = BatchSummary()
    total = len(links)
    for index, url in enumerate(links, start=1):
        ok, value = _isolated(summary, url, index, total, lambda u=url: grab(u, force=force), out)
        if not ok:
            if summary.aborted:
                break
            continue
        plan, report = value
        _record_report(summary, url, plan, report, index, total, verbose, out)

    for line in summary.lines():
        out(line)
    return summary


@dataclass
class PlannedLink:
    """Ссылка, прошедшая фазу разбора: план готов, выполнение ещё не начиналось."""

    url: str
    topic_id: str
    plan: object   # fetch._Plan; batch намеренно не знает его типа


def run_review_batch(
    links: list[str],
    *,
    resolve: Callable[[str], object],
    execute: Callable[..., object],
    confirm: Callable[[list[PlannedLink]], bool],
    force: bool = False,
    verbose: bool = False,
    out: Callable[[str], None] = print,
) -> BatchSummary:
    """`--review-all`: сперва разобрать все ссылки, показать план, потом выполнять (§9).

    Фаза 1 — `resolve(url)`: страница, заголовок, вопросы (если интерактив). Ничего
    не пишет на диск и не трогает qBittorrent. Ошибки изолируются так же, как в
    обычном батче, и в план не попадают.

    Фаза 2 — `execute(url, plan, force=...)` только для подтверждённых ссылок.
    Отказ от плана — не ошибка: exit-код определяется только сбоями фазы 1.
    """
    summary = BatchSummary()
    total = len(links)
    planned: list[PlannedLink] = []

    for index, url in enumerate(links, start=1):
        ok, plan = _isolated(summary, url, index, total, lambda u=url: resolve(u), out)
        if not ok:
            if summary.aborted:
                for line in summary.lines():
                    out(line)
                return summary
            continue
        topic_id = _topic_id_or_unknown(url)
        planned.append(PlannedLink(url=url, topic_id=topic_id, plan=plan))
        out(f"[{index}/{total}] t={topic_id:<9} {'план':<8} {plan.clean}")

    if not planned:
        out("")
        out("нечего выполнять: ни одна ссылка не разобралась")
        for line in summary.lines():
            out(line)
        return summary

    if not confirm(planned):
        summary.cancelled = True
        for line in summary.lines():
            out(line)
        return summary

    out("")
    executed = len(planned)
    for index, item in enumerate(planned, start=1):
        ok, report = _isolated(
            summary, item.url, index, executed,
            lambda i=item: execute(i.url, i.plan, force=force), out,
        )
        if not ok:
            if summary.aborted:
                break
            continue
        _record_report(summary, item.url, item.plan, report, index, executed, verbose, out)

    for line in summary.lines():
        out(line)
    return summary