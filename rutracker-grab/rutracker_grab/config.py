"""Конфиг: креды, пути, лексиконы, режимы (DESIGN.md §2, §8, §14).

Заглушка итерации 1. Секреты — в `.env`, не в git.
"""

from __future__ import annotations

from pathlib import Path

# Значения ниже — из §2/§8 DESIGN.md; вынесение в .env — в следующих итерациях.
QBIT_ROOT = "/torrents-active"
SMB_ROOT = r"\\nas-synology\torrents-active"
QBIT_WEBUI = "http://192.168.0.169:9866"

# Сеть: Happ как локальный SOCKS5, только для Playwright (§6a).
SOCKS5_PORT = 10808
SOCKS5_PROXY = f"socks5://127.0.0.1:{SOCKS5_PORT}"

# Персистентный профиль Chromium (cookie логина живут здесь между запусками).
BROWSER_PROFILE_DIR = Path.home() / ".rutracker_grab_profile"