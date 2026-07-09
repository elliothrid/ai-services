"""Инвариант §2: leaf — байт-в-байт одна строка в SMB-mkdir и в qBittorrent save_path.

Санитизация под Windows применяется к clean_title ДО обоих потребителей, включая
случай с `:` в названии (linux стерпел бы, Windows-клиент по SMB — нет).
"""

from __future__ import annotations

from pathlib import PureWindowsPath

from rutracker_grab import config
from rutracker_grab.env_adapter import local_dir, qbit_save_path, sanitize_leaf


def test_leaf_identical_in_both_consumers():
    leaf = sanitize_leaf("Убийца 2. Против всех [2018]")
    ld = PureWindowsPath(local_dir(leaf))
    qs = qbit_save_path(leaf)

    # Один и тот же leaf уходит и в SMB-путь, и в save_path.
    assert ld.name == leaf
    assert qs == f"{config.QBIT_ROOT}/{leaf}"
    assert qs.rsplit("/", 1)[-1] == ld.name


def test_colon_sanitized_before_both():
    # Двоеточие Windows по SMB не создаст — санитизация убирает его до обоих корней.
    raw_title = "Убийца 2: Против всех [2018]"
    leaf = sanitize_leaf(raw_title)

    assert ":" not in leaf
    assert leaf != raw_title  # санитизация реально что-то изменила

    ld = PureWindowsPath(local_dir(leaf))
    qs = qbit_save_path(leaf)
    # Обе стороны видят одинаковый (уже санитизированный) лист, без `:`.
    assert ld.name == leaf
    assert qs.rsplit("/", 1)[-1] == leaf
    assert ":" not in ld.name and ":" not in qs.rsplit("/", 1)[-1]


def test_roots_match_design():
    assert config.SMB_ROOT == r"\\nas-synology\torrents-active"
    assert config.QBIT_ROOT == "/torrents-active"


def test_no_trailing_dot_or_space():
    leaf = sanitize_leaf("Фильм. ")
    assert not leaf.endswith((" ", "."))
