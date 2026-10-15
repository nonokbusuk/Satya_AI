import json
import multiprocessing
import os
import pytest
from importlib import reload

from src.satya.sdk.client import SatyaClient
from src.satya.core import storage
import src.satya.auth as auth

@pytest.fixture(autouse=True)
def setup_teardown(monkeypatch, tmp_path):
    monkeypatch.setenv("SATYA_AGENT_KEYS", "root-test-agent-key-one-long-enough-123456,root-test-agent-key-two-long-enough-123456")
    monkeypatch.setenv("HUMAN_VIEW_TOKEN", "root-test-human-token-long-enough-123456")
    monkeypatch.setenv("AUDIT_SECRET", "root-unit-test-secret-at-least-32-characters-long")

    reload(auth)

    # Mock storage to use tmp_path
    monkeypatch.setattr(storage, "SATYA_DIR", os.path.join(tmp_path, "satya_data"))
    monkeypatch.setattr(storage, "TASKS_DIR", os.path.join(tmp_path, "satya_data", "tasks"))
    monkeypatch.setattr(storage, "TRUTH_DIR", os.path.join(tmp_path, "satya_data", "truth"))
    monkeypatch.setattr(storage, "AGENTS_DIR", os.path.join(tmp_path, "satya_data", "agents"))
    monkeypatch.setattr(storage, "HEARTBEATS_DIR", os.path.join(tmp_path, "satya_data", "heartbeats"))
    monkeypatch.setattr(storage, "PULSE_DIR", os.path.join(tmp_path, "satya_data", "pulse"))
    monkeypatch.setattr(storage, "GOALS_DIR", os.path.join(tmp_path, "satya_data", "goals"))
    storage.ensure_satya_dirs()
    yield
    # Restore environment and module-level auth cache before other tests run.
    monkeypatch.undo()
    reload(auth)

def test_auth_checks():
    assert auth.is_agent_authorized("root-test-agent-key-one-long-enough-123456") is True
    assert auth.is_agent_authorized("bad_key") is False
    assert auth.is_human_authorized("root-test-human-token-long-enough-123456") is True
    assert auth.is_human_authorized("root-test-human-token-long-enough-123456-wrong") is False

def test_audit_event_append_and_verify():
    events_dir = os.path.join(storage.SATYA_DIR, "events")
    events_file = os.path.join(events_dir, "audit_log.jsonl")
    if os.path.exists(events_file):
        os.remove(events_file)

    # Append events
    sig1 = auth.append_audit_event("agent_1", "task_A", "trace_1", "task_created", "demo create")
    sig2 = auth.append_audit_event("agent_1", "task_A", "trace_1", "status_updated", "in progress", prev_hmac=sig1)

    assert os.path.exists(events_file)
    with open(events_file, 'r') as f:
        lines = f.readlines()

    assert len(lines) == 2
    events = [json.loads(line) for line in lines]

    assert events[0]["signature"] == sig1
    assert events[1]["signature"] == sig2

    assert auth.verify_event_chain(events) is True

def test_sdk_create_task_requires_auth(monkeypatch):
    monkeypatch.setenv("SATYA_AGENT_KEY", "root-invalid-agent-key-not-registered-123456")
    with pytest.raises(PermissionError):
        client = SatyaClient(agent_name="tester")

def test_sdk_use_satya_audit_chain(monkeypatch):
    monkeypatch.setenv("SATYA_AGENT_KEY", "root-test-agent-key-one-long-enough-123456")
    client = SatyaClient(agent_name="tester")

    events_dir = os.path.join(storage.SATYA_DIR, "events")
    events_file = os.path.join(events_dir, "audit_log.jsonl")
    if os.path.exists(events_file):
        os.remove(events_file)

    # Simulate picking a task
    parent_task = client.create_task("Parent Task", "Long enough description for parent.")
    client.tasks.update_task_status(parent_task["id"], "in_progress", agent_name="tester")
    client.current_task = client.tasks.get_task(parent_task["id"])

    # Agent calls use_satya
    child_task = client.use_satya("Use satya to run a subtask", parent_trace_id=parent_task["trace_id"])

    assert child_task["parent_trace_id"] == parent_task["trace_id"]

    # Verify events
    events_dir = os.path.join(storage.SATYA_DIR, "events")
    events_file = os.path.join(events_dir, "audit_log.jsonl")
    with open(events_file, 'r') as f:
        lines = f.readlines()

    events = [json.loads(line) for line in lines]

    # Look for spawned_subtask
    spawn_events = [e for e in events if e["payload"]["action"] == "spawned_subtask"]
    assert len(spawn_events) == 1
    assert "child_id" in spawn_events[0]["payload"]["details"]
    assert "child_trace" in spawn_events[0]["payload"]["details"]

    # Verify the chain is valid overall
    assert auth.verify_event_chain(events) is True

def test_sqlite_audit_store(monkeypatch, tmp_path):
    db_path = os.path.join(tmp_path, "test_audit.db")
    monkeypatch.setenv("SATYA_SQLITE_DB", db_path)

    # Need to reset the initialized flag because tests might re-use it
    from src.satya.core import db
    db._DB_INITIALIZED = False

    sig = auth.append_audit_event("agent_sqlite", "task_db", "trace_db", "sqlite_test", "demo details")

    assert os.path.exists(db_path)

    import sqlite3
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT agent_id, task_id, action, signature FROM audit_log;")
    rows = cursor.fetchall()
    conn.close()

    assert len(rows) == 1
    assert rows[0][0] == "agent_sqlite"
    assert rows[0][1] == "task_db"
    assert rows[0][2] == "sqlite_test"
    assert rows[0][3] == sig


def test_audit_signing_fails_closed_without_secret(monkeypatch):
    monkeypatch.setattr(auth, "_AUDIT_SECRET", "")
    assert auth.is_agent_authorized("root-test-agent-key-one-long-enough-123456") is True
    with pytest.raises(ValueError, match="AUDIT_SECRET"):
        auth.sign_event('{"action":"test"}')
    assert auth.verify_event_chain([]) is False


def _parallel_audit_writer(data_root, worker_id, number_of_events):
    from src.satya.core import storage as child_storage
    import src.satya.auth as child_auth

    child_storage.SATYA_DIR = data_root
    child_storage.TASKS_DIR = os.path.join(data_root, "tasks")
    child_storage.TRUTH_DIR = os.path.join(data_root, "truth")
    child_storage.AGENTS_DIR = os.path.join(data_root, "agents")
    child_storage.HEARTBEATS_DIR = os.path.join(data_root, "heartbeats")
    child_storage.PULSE_DIR = os.path.join(data_root, "pulse")
    child_storage.GOALS_DIR = os.path.join(data_root, "goals")
    child_storage.ensure_satya_dirs()

    for event_no in range(number_of_events):
        child_auth.append_audit_event(
            f"agent-{worker_id}",
            f"task-{event_no}",
            f"trace-{worker_id}-{event_no}",
            "parallel_test",
            f"worker={worker_id};event={event_no}",
        )


@pytest.mark.skipif(
    "fork" not in multiprocessing.get_all_start_methods(),
    reason="cross-process flock test requires the POSIX fork start method",
)
def test_audit_chain_is_valid_under_concurrent_processes(tmp_path):
    context = multiprocessing.get_context("fork")
    workers = 4
    events_per_worker = 15
    data_root = os.path.join(tmp_path, "satya_data")
    processes = [
        context.Process(
            target=_parallel_audit_writer,
            args=(data_root, worker_id, events_per_worker),
        )
        for worker_id in range(workers)
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
        assert not process.is_alive(), "audit writer process did not exit"
        assert process.exitcode == 0

    events_file = os.path.join(data_root, "events", "audit_log.jsonl")
    with open(events_file, "r", encoding="utf-8") as handle:
        events = [json.loads(line) for line in handle if line.strip()]

    assert len(events) == workers * events_per_worker
    assert auth.verify_event_chain(events) is True


def test_audit_rejects_stale_parent_signature(monkeypatch):
    first = auth.append_audit_event("agent", "task", "trace", "first", "first event")
    auth.append_audit_event("agent", "task", "trace", "second", "second event")
    with pytest.raises(ValueError, match="current audit-log tail"):
        auth.append_audit_event(
            "agent", "task", "trace", "third", "stale parent", prev_hmac=first
        )


def test_weak_agent_and_human_secrets_are_rejected(monkeypatch):
    assert auth.is_agent_authorized("test_key1") is False
    assert auth.is_agent_authorized("DEMO_KEY") is False
    assert auth.is_human_authorized("admin_secret") is False

    monkeypatch.setenv("SATYA_AGENT_KEYS", "DEMO_KEY")
    reload(auth)
    assert auth.is_agent_authorized("DEMO_KEY") is False


def test_missing_agent_and_human_secrets_deny_access(monkeypatch):
    monkeypatch.setenv("SATYA_AGENT_KEYS", "")
    monkeypatch.setenv("HUMAN_VIEW_TOKEN", "")
    reload(auth)
    assert auth.is_agent_authorized("root-test-agent-key-one-long-enough-123456") is False
    assert auth.is_human_authorized("root-test-human-token-long-enough-123456") is False
