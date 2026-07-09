"""dl.php: извлечение topic_id и валидация «это торрент?» (DESIGN.md §8, §12).

Сеть не трогаем — `context.request` подменяется фейком; проверяем только логику
разбора и валидации bencode/content-type.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import qbittorrentapi

from rutracker_grab import torrent as torrent_mod
from rutracker_grab.torrent import (
    QbitAlreadyPresent,
    QbitAuthError,
    QbitRejected,
    TorrentNotBittorrent,
    add_to_qbit,
    download_torrent,
    recheck_torrent,
    topic_id_from_url,
    torrent_in_qbit,
    torrent_infohash_v1,
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
    """Мимикрия qbittorrentapi.Client: явный логин + add/info с настраиваемым поведением."""

    def __init__(self, *, add_result=None, add_exc=None, login_exc=None, info_result=None):
        self._add_result = add_result
        self._add_exc = add_exc
        self._login_exc = login_exc
        self._info_result = info_result if info_result is not None else []
        self.add_kwargs: dict | None = None
        self.info_hashes: str | None = None
        self.recheck_hashes: str | None = None
        self.logged_out = False

    def auth_log_in(self):
        if self._login_exc is not None:
            raise self._login_exc

    def auth_log_out(self):
        self.logged_out = True

    def torrents_add(self, **kwargs):
        self.add_kwargs = kwargs
        if self._add_exc is not None:
            raise self._add_exc
        return self._add_result

    def torrents_info(self, torrent_hashes=None):
        self.info_hashes = torrent_hashes
        return self._info_result

    def torrents_recheck(self, torrent_hashes=None):
        self.recheck_hashes = torrent_hashes


@pytest.fixture
def fake_client(monkeypatch):
    """Подменяет qbittorrentapi.Client фабрикой, отдающей настроенный фейк."""

    holder: dict = {}

    def install(**kwargs):
        client = _FakeClient(**kwargs)
        holder["client"] = client
        monkeypatch.setattr(
            torrent_mod.qbittorrentapi, "Client", lambda **kw: client
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
    client = fake_client["install"](add_result=meta)

    result = add_to_qbit(tor, "/torrents-active/leaf", "Имя раздачи")

    assert result == "8d36abc"  # torrent_hash для §10
    assert client.add_kwargs["save_path"] == "/torrents-active/leaf"
    assert client.add_kwargs["rename"] == "Имя раздачи"
    assert client.add_kwargs["is_paused"] is True
    assert client.logged_out is True  # сессия закрывается


def test_add_string_response_backward_compat(tmp_path, fake_client):
    tor = tmp_path / "y.torrent"
    tor.write_bytes(b"d...e")
    fake_client["install"](add_result="Ok.")

    # Старый формат: строка "Ok." — успех, хеша нет.
    assert add_to_qbit(tor, "/torrents-active/leaf", "Имя") is None


def test_add_object_response_failure(tmp_path, fake_client):
    tor = tmp_path / "z.torrent"
    tor.write_bytes(b"d...e")
    meta = SimpleNamespace(
        added_torrent_ids=[], failure_count=1, pending_count=0, success_count=0
    )
    fake_client["install"](add_result=meta)

    with pytest.raises(QbitRejected, match="failure_count=1"):
        add_to_qbit(tor, "/torrents-active/leaf", "Имя")


def test_add_string_response_failure(tmp_path, fake_client):
    tor = tmp_path / "w.torrent"
    tor.write_bytes(b"d...e")
    fake_client["install"](add_result="Fails.")

    with pytest.raises(QbitRejected):
        add_to_qbit(tor, "/torrents-active/leaf", "Имя")


# --- разделение слоя ошибок: логин ≠ добавление, 409 = skipped ----------------

def test_login_failure_is_auth_error(tmp_path, fake_client):
    tor = tmp_path / "t.torrent"
    tor.write_bytes(b"d...e")
    fake_client["install"](
        login_exc=qbittorrentapi.exceptions.LoginFailed("bad creds"),
        add_result="Ok.",
    )
    # Сбой auth_log_in -> QbitAuthError, не QbitRejected/QbitAlreadyPresent.
    with pytest.raises(QbitAuthError):
        add_to_qbit(tor, "/torrents-active/leaf", "Имя")


def test_add_conflict_409_is_already_present(tmp_path, fake_client):
    tor = tmp_path / "t.torrent"
    tor.write_bytes(b"d...e")
    # Логин прошёл, а torrents_add вернул 409 — это дубль, не ошибка входа.
    fake_client["install"](
        add_exc=qbittorrentapi.exceptions.Conflict409Error("duplicate")
    )
    with pytest.raises(QbitAlreadyPresent):
        add_to_qbit(tor, "/torrents-active/leaf", "Имя")


def test_add_generic_api_error_is_rejected(tmp_path, fake_client):
    tor = tmp_path / "t.torrent"
    tor.write_bytes(b"d...e")
    fake_client["install"](
        add_exc=qbittorrentapi.exceptions.APIError("boom")
    )
    with pytest.raises(QbitRejected):
        add_to_qbit(tor, "/torrents-active/leaf", "Имя")


def test_recheck_torrent(fake_client):
    client = fake_client["install"]()
    recheck_torrent("8d36abc")
    assert client.recheck_hashes == "8d36abc"
    assert client.logged_out is True


def test_torrent_in_qbit(fake_client):
    fake_client["install"](info_result=[{"hash": "abc"}])
    assert torrent_in_qbit("abc") is True
    fake_client["install"](info_result=[])
    assert torrent_in_qbit("def") is False


# --- infohash v1 из bencode ---------------------------------------------------

def test_infohash_v1_matches_sha1_of_info():
    import hashlib

    info = b"d6:lengthi3e4:name3:foo12:piece lengthi16384e6:pieces0:e"
    data = b"d8:announce8:http://x4:info" + info + b"e"
    assert torrent_infohash_v1(data) == hashlib.sha1(info).hexdigest()
