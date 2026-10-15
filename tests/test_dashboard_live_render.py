import json
from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_task_changes_are_rendered_after_dashboard_rerun(monkeypatch, tmp_path):
    repository_root = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HUMAN_VIEW_TOKEN", "root-dashboard-test-token-not-production")

    tasks_dir = tmp_path / "satya_data" / "tasks"
    tasks_dir.mkdir(parents=True)
    task_file = tasks_dir / "task-live-test.json"
    task = {
        "id": "task-live-test",
        "title": "UI persistence regression task",
        "description": "Verify dashboard reflects persisted task changes.",
        "status": "queued",
        "priority": "Medium",
        "assignee": "audit-agent",
        "created_at": "2026-10-15T10:00:00+00:00",
        "updated_at": "2026-10-15T10:00:00+00:00",
        "comments": [],
        "audit_trail": [],
    }
    task_file.write_text(json.dumps(task), encoding="utf-8")

    app_test = AppTest.from_file(str(repository_root / "app.py"), default_timeout=30).run()
    assert not app_test.exception, "\n".join(str(item.value) for item in app_test.exception)
    nav = next(item for item in app_test.radio if item.label == "Navigation")
    nav.set_value("Task Board").run()
    assert any("UI persistence regression task" in str(item.value) for item in app_test.markdown)

    task["status"] = "in_progress"
    task["updated_at"] = "2026-10-15T10:05:00+00:00"
    task_file.write_text(json.dumps(task), encoding="utf-8")
    app_test.run()

    assert not app_test.exception, "\n".join(str(item.value) for item in app_test.exception)
    rendered = "\n".join(str(item.value) for item in app_test.markdown)
    assert "UI persistence regression task" in rendered
    assert "In Progress" in rendered
