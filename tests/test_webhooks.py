import json
import os
import time
from unittest.mock import MagicMock, patch

import pytest

sys_path = os.path.abspath("src")
import sys
if sys_path not in sys.path:
    sys.path.insert(0, sys_path)

from satya.core import storage, webhooks


@pytest.fixture(autouse=True)
def cleanup_webhooks(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "SATYA_DIR", str(tmp_path / "satya_data"))
    monkeypatch.setattr(storage, "TASKS_DIR", str(tmp_path / "satya_data" / "tasks"))
    monkeypatch.setattr(storage, "TRUTH_DIR", str(tmp_path / "satya_data" / "truth"))
    monkeypatch.setattr(storage, "AGENTS_DIR", str(tmp_path / "satya_data" / "agents"))
    monkeypatch.setattr(storage, "HEARTBEATS_DIR", str(tmp_path / "satya_data" / "heartbeats"))
    monkeypatch.setattr(storage, "PULSE_DIR", str(tmp_path / "satya_data" / "pulse"))
    monkeypatch.setattr(storage, "GOALS_DIR", str(tmp_path / "satya_data" / "goals"))
    storage.ensure_satya_dirs()
    yield


GLOBAL = (2, 1, 6, "", ("93.184.216.34", 443))
PRIVATE = (2, 1, 6, "", ("192.168.1.1", 443))


@patch("satya.core.webhooks._resolve_safe_ip")
def test_add_and_remove_webhook(mock_getaddrinfo):
    # socket is the same module object used by the scraper's resolver.
    mock_getaddrinfo.return_value = "93.184.216.34"
    url = "https://example.com/webhook"
    assert webhooks.add_webhook(url) is True
    loaded = webhooks.load_webhooks()
    assert len(loaded) == 1
    assert loaded[0]["url"] == url
    assert webhooks.remove_webhook(url) is True
    assert webhooks.load_webhooks() == []


@patch("satya.core.webhooks.requests.Session")
@patch("satya.core.webhooks._resolve_safe_ip")
def test_dispatch_pins_dns_preserves_tls_hostname_and_blocks_redirects(
    mock_getaddrinfo, mock_session_class
):
    mock_getaddrinfo.return_value = "93.184.216.34"
    fake_session = MagicMock()
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_session.post.return_value = fake_response
    mock_session_class.return_value = fake_session

    url = "https://example.com:443/webhook?event=ok"
    assert webhooks.add_webhook(url) is True
    webhooks.dispatch("task_created", {"id": "123"})

    # Wait for background dispatcher thread to finish in this bounded test.
    for _ in range(200):
        if fake_response.raise_for_status.called and fake_session.close.called:
            break
        time.sleep(0.01)

    fake_session.post.assert_called_once_with(
        url,
        json={"event": "task_created", "payload": {"id": "123"}},
        timeout=5,
        allow_redirects=False,
    )
    assert fake_session.trust_env is False
    adapter = fake_session.mount.call_args.args[1]
    assert adapter._pinned_ip == "93.184.216.34"
    fake_response.raise_for_status.assert_called_once()
    assert "verify" not in fake_session.post.call_args.kwargs


@patch("satya.core.webhooks._resolve_safe_ip")
def test_reject_private_webhook(mock_getaddrinfo):
    mock_getaddrinfo.return_value = None
    assert webhooks.add_webhook("https://internal.example/hook") is False


@patch("satya.core.webhooks._resolve_safe_ip")
def test_reject_webhook_userinfo(mock_getaddrinfo):
    mock_getaddrinfo.return_value = "93.184.216.34"
    assert webhooks.add_webhook("https://user:pass@example.com/hook") is False
