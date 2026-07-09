"""Конфиг: креды, пути, лексиконы, режимы (DESIGN.md §2, §8, §14).

Секреты (пароль qBittorrent) — в `.env` рядом с проектом, не в git. См. `.env.example`.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# .env лежит в корне проекта (rutracker-grab/), рядом с pyproject.toml.
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_PATH)

# Значения ниже — из §2/§8 DESIGN.md.
QBIT_ROOT = "/torrents-active"
SMB_ROOT = r"\\nas-synology\torrents-active"
QBIT_WEBUI = "http://192.168.0.169:9866"

# Креды qBittorrent WebUI: user по умолчанию admin, пароль — только из .env (§8).
QBIT_USER = os.environ.get("QBIT_USER", "admin")
QBIT_PASSWORD = os.environ.get("QBIT_PASSWORD", "")

# Версия правил очистки заголовка — пишется в манифест .grab.json (§10).
RULES_VERSION = 1

# Сеть: Happ как локальный SOCKS5, только для Playwright (§6a).
SOCKS5_PORT = 10808
SOCKS5_PROXY = f"socks5://127.0.0.1:{SOCKS5_PORT}"

# Персистентный профиль Chromium (cookie логина живут здесь между запусками).
BROWSER_PROFILE_DIR = Path.home() / ".rutracker_grab_profile"