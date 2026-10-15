import os
import subprocess
import sys
from pathlib import Path

from src.satya.core import storage
from src.satya.core.tasks import Tasks


def test_task_comment_and_knowledge_source_survive_process_restart(tmp_path, monkeypatch):
    original = {
        name: getattr(storage, name)
        for name in (
            "ROOT_DIR", "SATYA_DIR", "TASKS_DIR", "TRUTH_DIR",
            "AGENTS_DIR", "HEARTBEATS_DIR", "PULSE_DIR", "GOALS_DIR"
        )
    }
    storage.set_root(str(tmp_path))
    try:
        manager = Tasks(repo_path=str(tmp_path))
        monkeypatch.setattr(manager.git_handler, "commit_and_push", lambda *a, **k: True)

        task = manager.create_task(
            "Persistence regression test",
            "Verify task fields and comments survive process restart.",
            assignee="test-agent",
        )
        assert task
        assert manager.update_task_status(task["id"], "in_progress", agent_name="test-agent")
        assert manager.add_comment(task["id"], "Persist this comment.", agent_name="test-agent")

        markdown_path = storage.save_markdown(
            "restart-proof.md",
            "# Restart proof\n\nThis source must remain persisted.",
        )
        assert markdown_path
        task_id = task["id"]

        project_root = Path(__file__).resolve().parents[1]
        child_script = """
import sys
from satya.core import storage
from satya.core.tasks import Tasks
storage.set_root(sys.argv[1])
manager = Tasks(repo_path=sys.argv[1])
task = manager.get_task(sys.argv[2])
assert task and task["status"] == "in_progress", task
assert any(c.get("text") == "Persist this comment." for c in task.get("comments", []))
assert "restart-proof.md" in storage.list_truth_files()
with open(storage.get_task_path(sys.argv[2]), encoding="utf-8") as handle:
    assert __import__("json").load(handle)["id"] == sys.argv[2]
"""
        environment = os.environ.copy()
        source_path = str(project_root / "src")
        environment["PYTHONPATH"] = os.pathsep.join(
            [str(project_root), source_path, environment.get("PYTHONPATH", "")]
        )
        result = subprocess.run(
            [sys.executable, "-c", child_script, str(tmp_path), task_id],
            cwd=str(tmp_path),
            env=environment,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert result.returncode == 0, result.stdout + "\n" + result.stderr
    finally:
        for name, value in original.items():
            setattr(storage, name, value)
