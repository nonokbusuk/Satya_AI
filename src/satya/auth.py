import hashlib
import hmac
import json
import logging
import os
import threading
import time
from typing import Any, Dict

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows uses the in-process fallback lock.
    fcntl = None

from .core import db, storage

logger = logging.getLogger(__name__)

_INSECURE_SECRET_VALUES = {
    "demo_key", "prod-key-123", "default_secret", "my-super-secret-audit-key",
    "docker-secret-audit-key", "change-me", "changeme",
}


def _is_strong_secret(value: str) -> bool:
    candidate = str(value or "").strip()
    lowered = candidate.lower()
    return (
        len(candidate) >= 32
        and lowered not in _INSECURE_SECRET_VALUES
        and "replace-with" not in lowered
        and "placeholder" not in lowered
    )


_AGENT_KEYS = {
    key.strip()
    for key in os.environ.get("SATYA_AGENT_KEYS", "").split(",")
    if _is_strong_secret(key)
}
_HUMAN_VIEW = os.environ.get("HUMAN_VIEW_TOKEN", "").strip()
_AUDIT_SECRET = os.environ.get("AUDIT_SECRET", "").strip()
_AUDIT_THREAD_LOCK = threading.RLock()


def is_agent_authorized(key: str) -> bool:
    """Reject absent, short, sample, and non-allow-listed agent keys."""
    candidate = str(key or "").strip()
    return _is_strong_secret(candidate) and any(
        hmac.compare_digest(candidate, allowed) for allowed in _AGENT_KEYS
    )


def is_human_authorized(token: str) -> bool:
    """Only a sufficiently strong configured token can unlock mutations."""
    candidate = str(token or "").strip()
    return (
        _is_strong_secret(_HUMAN_VIEW)
        and _is_strong_secret(candidate)
        and hmac.compare_digest(candidate, _HUMAN_VIEW)
    )


def get_agent_key_from_env() -> str:
    key = os.environ.get("SATYA_AGENT_KEY", "").strip()
    if not key:
        raise ValueError("SATYA_AGENT_KEY is not set in the environment")
    if not _is_strong_secret(key):
        raise ValueError("SATYA_AGENT_KEY must be a unique secret of at least 32 characters.")
    return key


def require_agent(key: str):
    if not is_agent_authorized(key):
        raise PermissionError("Agent credentials required or invalid")


def sign_event(event_data: str, prev_hmac: str = "") -> str:
    """Sign one audit payload; fail closed if the signing secret is missing."""
    if not _is_strong_secret(_AUDIT_SECRET):
        raise ValueError("AUDIT_SECRET must be a unique secret of at least 32 characters.")
    msg = f"{prev_hmac}:{event_data}".encode("utf-8")
    return hmac.new(_AUDIT_SECRET.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def verify_event_chain(events: list[Dict[str, Any]]) -> bool:
    """Verify an audit chain, returning False when the secret or chain is invalid."""
    if not _is_strong_secret(_AUDIT_SECRET):
        return False
    prev_hmac = ""
    for event in events:
        signature = event.get("signature")
        payload = event.get("payload", {})
        payload_str = json.dumps(payload, sort_keys=True)
        try:
            expected_signature = sign_event(payload_str, prev_hmac)
        except ValueError:
            return False
        if not hmac.compare_digest(str(signature or ""), expected_signature):
            return False
        prev_hmac = str(signature)
    return True


def _last_signature(handle) -> str:
    """Read the last complete JSONL event while the file lock is held."""
    handle.seek(0, os.SEEK_END)
    position = handle.tell()
    if position == 0:
        return ""

    cursor = position
    data = b""
    while cursor > 0:
        start = max(0, cursor - 8192)
        handle.seek(start)
        data = handle.read(cursor - start) + data
        lines = data.splitlines()
        if len(lines) >= 2 or start == 0:
            last_line = next((line for line in reversed(lines) if line.strip()), b"")
            break
        cursor = start
    else:
        last_line = b""

    if not last_line:
        return ""
    try:
        event = json.loads(last_line.decode("utf-8"))
        signature = event.get("signature")
        if not signature:
            raise ValueError("signature is missing")
        return str(signature)
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError, ValueError) as exc:
        raise RuntimeError(
            "Audit log's final event is invalid; refusing to extend a broken chain."
        ) from exc


def append_audit_event(
    agent_id: str,
    task_id: str,
    trace_id: str,
    action: str,
    details: str,
    prev_hmac: str = "",
) -> str:
    """Append a signed event while serializing tail-read, signature, and file write."""
    storage.ensure_satya_dirs()
    events_dir = os.path.join(storage.SATYA_DIR, "events")
    os.makedirs(events_dir, exist_ok=True)
    events_file = os.path.join(events_dir, "audit_log.jsonl")

    event = None
    signature = ""
    # Thread lock complements flock; the OS lock makes this safe between processes
    # on supported Unix filesystems. The lock covers tail-read AND append.
    with _AUDIT_THREAD_LOCK:
        with open(events_file, "a+b") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                current_tail = _last_signature(handle)
                if prev_hmac and prev_hmac != current_tail:
                    raise ValueError(
                        "prev_hmac must match the current audit-log tail; refusing to fork the chain."
                    )
                chain_parent = current_tail
                payload = {
                    "timestamp": time.time(),
                    "agent_id": agent_id,
                    "task_id": task_id,
                    "trace_id": trace_id,
                    "action": action,
                    "details": details,
                }
                signature = sign_event(json.dumps(payload, sort_keys=True), chain_parent)
                event = {"payload": payload, "signature": signature}
                line = (json.dumps(event, separators=(",", ":")) + "\n").encode("utf-8")
                handle.seek(0, os.SEEK_END)
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    # File log is the source of truth; an external DB failure must be visible in logs.
    if os.environ.get("SATYA_SQLITE_DB") or os.environ.get("SATYA_POSTGRES_URI"):
        try:
            db.append_event_to_db(event)
        except Exception:
            logger.exception("Failed to mirror audit event %s to configured database", signature)
    return signature
