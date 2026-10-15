import os
import shutil
import pytest
from datetime import datetime, timedelta
from src.satya.core.tasks import Tasks, STATUS_IN_PROGRESS, STATUS_DONE
from src.satya.core.watchdog import WatchdogChecker

@pytest.fixture
def temp_watchdog(tmp_path):
    repo_path = str(tmp_path / "test_repo_watchdog")
    os.makedirs(repo_path, exist_ok=True)

    import src.satya.core.storage as storage
    storage_names = ("ROOT_DIR", "SATYA_DIR", "TASKS_DIR", "TRUTH_DIR", "AGENTS_DIR", "HEARTBEATS_DIR", "PULSE_DIR", "GOALS_DIR",)
    old_storage = {name: getattr(storage, name) for name in storage_names}
    storage.set_root(repo_path)
    try:
        checker = WatchdogChecker(repo_path)
        yield checker
    finally:
        shutil.rmtree(repo_path, ignore_errors=True)
        for name, value in old_storage.items():
            setattr(storage, name, value)


def test_stale_task_detected_after_time_limit(temp_watchdog):
    tasks = temp_watchdog.tasks
    task = tasks.create_task("Test", "Desc", time_limit_minutes=15)

    tasks.update_task_status(task["id"], STATUS_IN_PROGRESS, "agent1")

    # Overwrite locked_at to simulate time passing
    old_time = datetime.now() - timedelta(minutes=20)
    tasks.update_task(task["id"], {"locked_at": old_time.isoformat()})

    stale_tasks = temp_watchdog.scan()
    assert len(stale_tasks) == 1
    assert stale_tasks[0]["id"] == task["id"]
    assert stale_tasks[0]["elapsed_minutes"] >= 20

def test_non_stale_task_not_flagged(temp_watchdog):
    tasks = temp_watchdog.tasks
    task = tasks.create_task("Test", "Desc", time_limit_minutes=15)

    tasks.update_task_status(task["id"], STATUS_IN_PROGRESS, "agent1")

    # Overwrite locked_at to be within time limit
    old_time = datetime.now() - timedelta(minutes=10)
    tasks.update_task(task["id"], {"locked_at": old_time.isoformat()})

    stale_tasks = temp_watchdog.scan()
    assert len(stale_tasks) == 0

def test_completed_task_not_flagged(temp_watchdog):
    tasks = temp_watchdog.tasks

    # we need to manually fake the criteria to pass completion so we can move it to done
    task = tasks.create_task("Test", "Desc", time_limit_minutes=15)

    tasks.update_task_status(task["id"], STATUS_IN_PROGRESS, "agent1")

    old_time = datetime.now() - timedelta(minutes=20)

    # make it stale
    tasks.update_task(task["id"], {"locked_at": old_time.isoformat()})

    # Wait, actually if a task is "done" it's skipped by get_tasks(status=STATUS_IN_PROGRESS)
    # We don't even need to fully trigger completion checker, we can just manually set the status field
    # since we want to avoid file creation overhead if possible, or just create it:

    # bypassing completion check by manually writing JSON
    import src.satya.core.storage as storage
    task_data = storage.load_json(storage.get_task_path(task["id"]))
    task_data["status"] = STATUS_DONE
    storage.save_json(storage.get_task_path(task["id"]), task_data)

    stale_tasks = temp_watchdog.scan()
    assert len(stale_tasks) == 0
