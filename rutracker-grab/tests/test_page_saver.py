"""MHTML пишется бинарно: CRLF из CDP не должен удваиваться в CRCRLF (DESIGN.md §7).

Удвоение `\r\r\n` — причина «белого экрана» в Chrome. Тест снимает снапшот через
реальный Chromium (офлайн, `set_content`) и проверяет, что в файле нет `\r\r\n`.
"""

from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

from rutracker_grab.page_saver import save_page_mhtml  # noqa: E402


def test_mhtml_no_double_cr(tmp_path):
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(tmp_path / "profile"), headless=True
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.set_content("<h1 class='maintitle'>Тест кириллица</h1><p>тело</p>")
        dest = save_page_mhtml(page, tmp_path / "About.mhtml")
        context.close()

    data = dest.read_bytes()
    assert data.count(b"\r\r\n") == 0
    assert b"\r\n" in data          # CRLF из MIME на месте — писали именно бинарно
    assert dest.name == "About.mhtml"
