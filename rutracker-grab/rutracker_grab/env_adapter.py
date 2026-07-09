r"""Трансляция путей local/nas/qbit — «общий лист в двух корнях» (DESIGN.md §2).

Одна физическая папка видна в двух представлениях:
- `qbit_root = /torrents-active`       — как видит qBittorrent (уходит в save_path);
- `smb_root  = \\nas-synology\...`     — как видит ПК по сети (туда пишем файлы).

Инвариант (§2): `leaf` — байт-в-байт одна и та же строка в SMB-mkdir и в
`save_path`. Санитизация под Windows (`sanitize_leaf`) применяется к `clean_title`
ДО обоих потребителей, поэтому обе функции ниже принимают уже готовый `leaf`.
"""

from __future__ import annotations

from pathlib import Path

from . import config
from .title.validate import sanitize_leaf  # единый источник санитизации (§2, §11)

__all__ = ["sanitize_leaf", "local_dir", "qbit_save_path"]


def local_dir(leaf: str) -> Path:
    """Папка на ПК (UNC по SMB), куда пишем `.mhtml` / `folder.jpg` / `.torrent`."""
    return Path(config.SMB_ROOT) / leaf


def qbit_save_path(leaf: str) -> str:
    """`save_path` для qBittorrent — posix-путь под linux-корнем контейнера."""
    return f"{config.QBIT_ROOT}/{leaf}"
