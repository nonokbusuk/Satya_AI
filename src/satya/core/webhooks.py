import json
import logging
import os
import requests
import threading
from urllib.parse import urlparse

from . import storage
from .scraper import _PinnedHTTPAdapter, _resolve_safe_ip

logger = logging.getLogger(__name__)


def is_safe_url(url: str) -> bool:
    """Return true only if URL resolves exclusively to globally routable addresses."""
    return _resolve_safe_ip(url) is not None


def get_webhooks_path():
    return os.path.join(storage.SATYA_DIR, "webhooks.json")


def load_webhooks():
    path = get_webhooks_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        logger.exception("Error loading webhooks")
        return []


def save_webhooks(webhooks):
    path = get_webhooks_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        temporary_path = path + f".{threading.get_ident()}.tmp"
        with open(temporary_path, "w", encoding="utf-8") as handle:
            json.dump(webhooks, handle, indent=4)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        return True
    except Exception:
        logger.exception("Error saving webhooks")
        try:
            if os.path.exists(temporary_path):
                os.remove(temporary_path)
        except Exception:
            pass
        return False


def add_webhook(url, events=None):
    parsed = urlparse(url)
    if not parsed.hostname or parsed.username is not None or parsed.password is not None:
        logger.warning("Rejected malformed webhook URL")
        return False
    if not is_safe_url(url):
        logger.warning("Rejected unsafe webhook URL: %s", parsed.scheme + "://" + parsed.netloc)
        return False

    if events is None:
        events = ["task_created", "task_updated"]
    webhooks = load_webhooks()
    for webhook in webhooks:
        if webhook.get("url") == url:
            webhook["events"] = events
            return save_webhooks(webhooks)
    webhooks.append({"url": url, "events": events})
    return save_webhooks(webhooks)


def remove_webhook(url):
    webhooks = load_webhooks()
    webhooks = [webhook for webhook in webhooks if webhook.get("url") != url]
    return save_webhooks(webhooks)


def dispatch(event_type, payload):
    webhooks = load_webhooks()
    urls_to_notify = [
        webhook["url"]
        for webhook in webhooks
        if event_type in webhook.get("events", []) and webhook.get("url")
    ]
    if not urls_to_notify:
        return

    data = {"event": event_type, "payload": payload}

    def _send():
        for url in urls_to_notify:
            parsed = urlparse(url)
            try:
                # Re-resolve at dispatch time, reject every non-global DNS answer and
                # pin the underlying socket to that exact address. Keep the original
                # hostname in URL for Host/SNI/certificate validation.
                safe_ip = _resolve_safe_ip(url)
                if not safe_ip:
                    logger.warning("Skipping unsafe webhook destination: %s", parsed.scheme + "://" + parsed.netloc)
                    continue

                session = requests.Session()
                session.trust_env = False
                session.mount(
                    f"{parsed.scheme}://{parsed.netloc}",
                    _PinnedHTTPAdapter(safe_ip),
                )
                try:
                    response = session.post(
                        url,
                        json=data,
                        timeout=5,
                        allow_redirects=False,
                    )
                    response.raise_for_status()
                    logger.info("Webhook dispatched to %s for event %s", parsed.scheme + "://" + parsed.netloc, event_type)
                finally:
                    session.close()
            except Exception:
                logger.exception("Failed to dispatch webhook to %s", parsed.scheme + "://" + parsed.netloc)

    threading.Thread(target=_send, daemon=True).start()
