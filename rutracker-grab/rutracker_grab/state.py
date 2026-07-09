"""Манифесты и идемпотентность (DESIGN.md §10).

В каждой папке раздачи — `.grab.json` (topic_id, clean_title, torrent_hash,
first_grabbed_at, updated_at, версия правил). Модуль только читает и пишет манифест;
решение «пропустить прогон» принимается в `reconcile.py` по совокупности артефактов,
а не по одному манифесту.

ВАЖНО: имя папки (leaf) содержит `[...]` — НЕ искать через glob/Path.glob(), скобки
станут классом символов и поиск молча вернёт пусто. Только `Path.exists()`.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import config

MANIFEST_NAME = ".grab.json"


def manifest_path(folder: Path) -> Path:
    return Path(folder) / MANIFEST_NAME


def read_manifest(folder: Path) -> dict | None:
    """Прочитать `.grab.json` из папки (только `exists()`, без glob). None — если нет/битый."""
    path = manifest_path(folder)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def write_manifest(
    folder: Path,
    *,
    topic_id: str,
    clean_title: str,
    torrent_hash: str | None,
    now: str | None = None,
    rules_version: int = config.RULES_VERSION,
) -> Path:
    """Перезаписать `.grab.json` после успешного прогона. Возвращает путь манифеста.

    Пишем всегда (кроме `skipped`): раздача на рутрекере могла обновиться, и тогда
    `torrent_hash` в старом манифесте протух. `first_grabbed_at` переносим из
    прежнего манифеста (у самых старых это поле называлось `added_at`), `updated_at`
    ставим текущий. Если манифеста не было — оба равны времени этого прогона.
    """
    path = manifest_path(folder)
    updated_at = now or datetime.now(timezone.utc).isoformat()

    previous = read_manifest(folder) or {}
    first_grabbed_at = previous.get("first_grabbed_at") or previous.get("added_at") or updated_at

    data = {
        "topic_id": str(topic_id),
        "clean_title": clean_title,
        "torrent_hash": torrent_hash,
        "first_grabbed_at": first_grabbed_at,
        "updated_at": updated_at,
        "rules_version": rules_version,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def is_already_grabbed(folder: Path, topic_id: str) -> bool:
    """Есть манифест и его `topic_id` совпадает с текущим.

    Наличия манифеста НЕ достаточно, чтобы пропустить прогон: артефакты могли
    удалить по отдельности. Решение о `skipped` принимает `reconcile.py` (§10).
    """
    manifest = read_manifest(folder)
    return bool(manifest) and str(manifest.get("topic_id")) == str(topic_id)
