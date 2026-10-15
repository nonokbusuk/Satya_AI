import socket
from unittest.mock import patch

import pytest

from src.satya.core.scraper import (
    Scraper,
    _PinnedHTTPSConnection,
    _is_safe_url,
    _resolve_safe_ip,
)
from urllib3.util import connection as urllib3_connection


GLOBAL = (2, 1, 6, "", ("93.184.216.34", 443))
LOOPBACK = (2, 1, 6, "", ("127.0.0.1", 80))
PRIVATE = (2, 1, 6, "", ("192.168.1.10", 443))


def test_safe_url_global_ip(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [GLOBAL])
    assert _is_safe_url("https://example.com") is True


def test_unsafe_url_local_ip(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [LOOPBACK])
    assert _is_safe_url("https://localhost") is False


def test_unsafe_url_mixed_ips(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [GLOBAL, PRIVATE])
    assert _is_safe_url("https://example.com") is False


def test_invalid_scheme():
    assert _is_safe_url("ftp://example.com") is False


def test_empty_hostname():
    assert _is_safe_url("https://") is False


def test_url_with_userinfo_is_rejected(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [GLOBAL])
    assert _resolve_safe_ip("https://user:password@example.com/") is None


def test_dns_rebinding_cannot_change_pinned_connection(monkeypatch):
    # The hostname resolves to a public IP during validation and would resolve to
    # loopback on a second lookup. The transport must connect to the validated IP.
    lookups = iter([[GLOBAL], [LOOPBACK]])
    calls = []

    def fake_getaddrinfo(*args, **kwargs):
        result = next(lookups)
        calls.append(args[0])
        return result

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    pinned_ip = _resolve_safe_ip("https://rebind.example/")
    assert pinned_ip == "93.184.216.34"

    connection = _PinnedHTTPSConnection(
        host="rebind.example", port=443, timeout=1, pinned_ip=pinned_ip
    )
    with patch.object(
        urllib3_connection,
        "create_connection",
        return_value=object(),
    ) as connect:
        connection._new_conn()

    connect.assert_called_once()
    assert connect.call_args.args[0] == ("93.184.216.34", 443)
    assert calls == ["rebind.example"]


def test_redirect_to_private_address_is_blocked(monkeypatch, tmp_path):
    import src.satya.core.scraper as scraper_module
    from src.satya.core import storage

    monkeypatch.setattr(storage, "ROOT_DIR", str(tmp_path))
    monkeypatch.setattr(storage, "SATYA_DIR", str(tmp_path / "satya_data"))
    monkeypatch.setattr(storage, "TASKS_DIR", str(tmp_path / "satya_data" / "tasks"))
    monkeypatch.setattr(storage, "TRUTH_DIR", str(tmp_path / "satya_data" / "truth"))
    monkeypatch.setattr(storage, "AGENTS_DIR", str(tmp_path / "satya_data" / "agents"))
    monkeypatch.setattr(storage, "HEARTBEATS_DIR", str(tmp_path / "satya_data" / "heartbeats"))
    monkeypatch.setattr(storage, "PULSE_DIR", str(tmp_path / "satya_data" / "pulse"))
    monkeypatch.setattr(storage, "GOALS_DIR", str(tmp_path / "satya_data" / "goals"))
    storage.ensure_satya_dirs()
    monkeypatch.setattr(
        scraper_module,
        "_resolve_safe_ip",
        lambda url: "93.184.216.34" if url.startswith("https://public.example/") else None,
    )
    requests_made = []

    def fake_fetch(url, pinned_ip, max_bytes=scraper_module.MAX_RESPONSE_BYTES):
        requests_made.append(url)
        return 302, {"Location": "http://127.0.0.1/latest/meta-data"}, ""

    monkeypatch.setattr(scraper_module, "_fetch_once_pinned", fake_fetch)
    scraper = Scraper(repo_path=str(tmp_path))
    monkeypatch.setattr(scraper.git_handler, "commit_and_push", lambda *a, **k: True)

    assert scraper.fetch_and_save("https://public.example/start") is None
    assert requests_made == ["https://public.example/start"]
    assert storage.list_truth_files() == []


def test_scraper_limits_response_size(monkeypatch):
    import src.satya.core.scraper as scraper_module
    from unittest.mock import MagicMock

    session = MagicMock()
    response = MagicMock()
    response.status_code = 200
    response.headers = {"Content-Length": str(scraper_module.MAX_RESPONSE_BYTES + 1)}
    session.get.return_value = response

    with patch.object(scraper_module.requests, "Session", return_value=session):
        with pytest.raises(ValueError, match="5 MiB"):
            scraper_module._fetch_once_pinned(
                "https://example.com/", "93.184.216.34"
            )



def test_pinned_adapter_builds_hostname_preserving_pool():
    from src.satya.core.scraper import _PinnedHTTPAdapter, _PinnedHTTPSConnectionPool

    adapter = _PinnedHTTPAdapter("93.184.216.34")
    try:
        pool = adapter.poolmanager.connection_from_host(
            "rebind.example", port=443, scheme="https"
        )
        assert isinstance(pool, _PinnedHTTPSConnectionPool)
        assert pool.conn_kw["pinned_ip"] == "93.184.216.34"
        assert pool.host == "rebind.example"
    finally:
        adapter.close()
