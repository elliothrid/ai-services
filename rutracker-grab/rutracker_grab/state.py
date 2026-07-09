"""Манифесты и идемпотентность (DESIGN.md §10).

В каждой папке раздачи — `.grab.json` (topic_id, clean_title, torrent_hash, added_at,
версия правил). Перед повторным прогоном: если манифест есть и topic_id совпал —
`skipped` (если не форсим `--force`).

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
    added_at: str | None = None,
    rules_version: int = config.RULES_VERSION,
) -> Path:
    """Записать `.grab.json` после успешного прогона. Возвращает путь манифеста."""
    path = manifest_path(folder)
    data = {
        "topic_id": str(topic_id),
        "clean_title": clean_title,
        "torrent_hash": torrent_hash,
        "added_at": added_at or datetime.now(timezone.utc).isoformat(),
        "rules_version": rules_version,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def is_already_grabbed(folder: Path, topic_id: str) -> bool:
    """Есть манифест и его `topic_id` совпадает с текущим."""
    manifest = read_manifest(folder)
    return bool(manifest) and str(manifest.get("topic_id")) == str(topic_id)


def should_skip(folder: Path, topic_id: str, *, force: bool) -> bool:
    """Пропустить прогон: манифест уже есть для этого topic_id и не форсим."""
    return not force and is_already_grabbed(folder, topic_id)
