"""dl.php: извлечение topic_id и валидация «это торрент?» (DESIGN.md §8, §12).

Сеть не трогаем — `context.request` подменяется фейком; проверяем только логику
разбора и валидации bencode/content-type.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from rutracker_grab import torrent as torrent_mod
from rutracker_grab.torrent import (
    QbitRejected,
    TorrentNotBittorrent,
    add_to_qbit,
    download_torrent,
    topic_id_from_url,
)


class _FakeResponse:
    def __init__(self, body: bytes, content_type: str, status: int = 200, ok: bool = True):
        self._body = body
        self.headers = {"content-type": content_type}
        self.status = status
        self.ok = ok

    def body(self) -> bytes:
        return self._body


class _FakeRequest:
    def __init__(self, response: _FakeResponse):
        self._response = response
        self.last_headers: dict | None = None

    def get(self, url: str, headers: dict | None = None) -> _FakeResponse:
        self.last_headers = headers
        return self._response


class _FakeContext:
    def __init__(self, response: _FakeResponse):
        self.request = _FakeRequest(response)


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://rutracker.org/forum/viewtopic.php?t=6870556", "6870556"),
        ("https://rutracker.org/forum/viewtopic.php?f=718&t=42", "42"),
    ],
)
def test_topic_id_from_url(url, expected):
    assert topic_id_from_url(url) == expected


def test_topic_id_missing_raises():
    with pytest.raises(ValueError):
        topic_id_from_url("https://rutracker.org/forum/index.php")


def test_accept_by_bencode_first_byte(tmp_path):
    ctx = _FakeContext(_FakeResponse(b"d8:announce...e", "application/octet-stream"))
    dest = download_torrent(ctx, "42", tmp_path / "a.torrent")
    assert dest.read_bytes().startswith(b"d")
    # Referer выставлен на страницу темы (§8).
    assert ctx.request.last_headers["Referer"].endswith("viewtopic.php?t=42")


def test_accept_by_content_type(tmp_path):
    ctx = _FakeContext(_FakeResponse(b"anything", "application/x-bittorrent"))
    dest = download_torrent(ctx, "42", tmp_path / "b.torrent")
    assert dest.exists()


def test_reject_html_session_expired(tmp_path):
    ctx = _FakeContext(_FakeResponse(b"<!DOCTYPE html><html>login", "text/html; charset=windows-1251"))
    with pytest.raises(TorrentNotBittorrent):
        download_torrent(ctx, "42", tmp_path / "c.torrent")
    assert not (tmp_path / "c.torrent").exists()  # мусор на диск не пишем


# --- add_to_qbit: оба формата ответа, фейковый клиент без сети -----------------


class _FakeClient:
    """Мимикрия qbittorrentapi.Client: контекст-менеджер + torrents_add(result)."""

    def __init__(self, result):
        self._result = result
        self.add_kwargs: dict | None = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def torrents_add(self, **kwargs):
        self.add_kwargs = kwargs
        return self._result


@pytest.fixture
def fake_client(monkeypatch):
    """Подменяет qbittorrentapi.Client фабрикой, отдающей заданный result."""

    holder: dict = {}

    def install(result):
        client = _FakeClient(result)
        holder["client"] = client
        monkeypatch.setattr(
            torrent_mod.qbittorrentapi, "Client", lambda **kwargs: client
        )
        return client

    holder["install"] = install
    return holder


def test_add_object_response_success(tmp_path, fake_client):
    tor = tmp_path / "x.torrent"
    tor.write_bytes(b"d...e")
    # Реальный формат qbittorrent-api 2026.7.0 (TorrentsAddedMetadata).
    meta = SimpleNamespace(
        added_torrent_ids=["8d36abc"],
        failure_count=0,
        pending_count=0,
        success_count=1,
    )
    client = fake_client["install"](meta)

    result = add_to_qbit(tor, "/torrents-active/leaf", "Имя раздачи")

    assert result == "8d36abc"  # torrent_hash для §10
    # Параметры добавления доехали в клиент как задумано.
    assert client.add_kwargs["save_path"] == "/torrents-active/leaf"
    assert client.add_kwargs["rename"] == "Имя раздачи"
    assert client.add_kwargs["is_paused"] is True


def test_add_string_response_backward_compat(tmp_path, fake_client):
    tor = tmp_path / "y.torrent"
    tor.write_bytes(b"d...e")
    fake_client["install"]("Ok.")

    # Старый формат: строка "Ok." — успех, хеша нет.
    assert add_to_qbit(tor, "/torrents-active/leaf", "Имя") is None


def test_add_object_response_failure(tmp_path, fake_client):
    tor = tmp_path / "z.torrent"
    tor.write_bytes(b"d...e")
    meta = SimpleNamespace(
        added_torrent_ids=[], failure_count=1, pending_count=0, success_count=0
    )
    fake_client["install"](meta)

    with pytest.raises(QbitRejected, match="failure_count=1"):
        add_to_qbit(tor, "/torrents-active/leaf", "Имя")


def test_add_string_response_failure(tmp_path, fake_client):
    tor = tmp_path / "w.torrent"
    tor.write_bytes(b"d...e")
    fake_client["install"]("Fails.")

    with pytest.raises(QbitRejected):
        add_to_qbit(tor, "/torrents-active/leaf", "Имя")
