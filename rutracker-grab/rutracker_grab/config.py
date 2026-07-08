"""Конфиг: креды, пути, лексиконы, режимы (DESIGN.md §2, §8, §14).

Заглушка итерации 1. Секреты — в `.env`, не в git.
"""

from __future__ import annotations

# Значения ниже — из §2/§8 DESIGN.md; вынесение в .env — в следующих итерациях.
QBIT_ROOT = "/torrents-active"
SMB_ROOT = r"\\nas-synology\torrents-active"
QBIT_WEBUI = "http://192.168.0.169:9866"
SOCKS5_PORT = 10808