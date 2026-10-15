import pytest
import os
import os
import src.satya.core.storage as storage
from src.satya.sdk.client import SatyaClient
from importlib import reload
import src.satya.auth as auth

@pytest.fixture
def temp_client(monkeypatch, tmp_path):
    monkeypatch.setenv("SATYA_AGENT_KEY", "root-sdk-test-agent-key-long-enough-123456")
    monkeypatch.setenv("SATYA_AGENT_KEYS", "root-sdk-test-agent-key-long-enough-123456")
    reload(auth)

    repo_path = str(tmp_path)

    # Isolate every storage path, including heartbeat/pulse/goal data.
    storage_names = (
        "ROOT_DIR", "SATYA_DIR", "TASKS_DIR", "TRUTH_DIR", "AGENTS_DIR",
        "HEARTBEATS_DIR", "PULSE_DIR", "GOALS_DIR",
    )
    old_vars = {name: getattr(storage, name) for name in storage_names}
    storage.set_root(repo_path)

    try:
        client = SatyaClient(agent_name="test_agent", repo_path=repo_path)
        yield client
    finally:
        for var, value in old_vars.items():
            setattr(storage, var, value)
        # Restore process environment before reloading cached module-level auth config.
        monkeypatch.undo()
        reload(auth)

def test_create_task_description_too_short(temp_client):
    with pytest.raises(ValueError, match="Governance Error: Task description must be at least 10 characters."):
        temp_client.create_task("Test Task", "short")

def test_create_task_description_empty(temp_client):
    with pytest.raises(ValueError, match="Governance Error: Task description must be at least 10 characters."):
        temp_client.create_task("Test Task", "")

def test_create_task_description_whitespace(temp_client):
    # Description "   12345   " stripped is "12345", length 5 < 10
    with pytest.raises(ValueError, match="Governance Error: Task description must be at least 10 characters."):
        temp_client.create_task("Test Task", "   12345   ")

def test_create_task_description_none(temp_client):
    with pytest.raises(ValueError, match="Governance Error: Task description must be at least 10 characters."):
        temp_client.create_task("Test Task", None)

def test_create_task_valid_description(temp_client):
    task = temp_client.create_task("Test Task", "This is a long enough description.")
    assert task["title"] == "Test Task"
    assert task["description"] == "This is a long enough description."
    assert task["id"] is not None
