import importlib
import sys
from pathlib import Path
from streamlit.testing.v1 import AppTest


def test_dashboard_smoke_and_read_only_task_board(monkeypatch, tmp_path):
    repository_root = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HUMAN_VIEW_TOKEN", "root-dashboard-test-token-not-production")
    if str(repository_root / "src") not in sys.path:
        sys.path.insert(0, str(repository_root / "src"))
    import satya.auth as root_auth
    importlib.reload(root_auth)
    app_test = AppTest.from_file(str(repository_root / "app.py"), default_timeout=30).run()
    assert not app_test.exception, "\n".join(str(item.value) for item in app_test.exception)

    nav = next(item for item in app_test.radio if item.label == "Navigation")
    nav.set_value("Task Board").run()
    assert not app_test.exception, "\n".join(str(item.value) for item in app_test.exception)
    assert not any(item.label == "Create New Task" for item in app_test.expander)
    assert not any(item.label == "Create Task" for item in app_test.button)

    nav.set_value("Template Galleries").run()
    assert not app_test.exception
    assert not any(item.label.startswith("Deploy ") for item in app_test.button)
    assert any("Administrator access is required" in str(item.value) for item in app_test.caption)

    nav.set_value("Agent Chat").run()
    assert not app_test.exception
    assert any("Admin Access Required" in str(item.value) for item in app_test.error)
    assert not any(item.label == "Send" for item in app_test.button)


def test_dashboard_rejects_unauthorized_orchestrator_action(monkeypatch, tmp_path):
    repository_root = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HUMAN_VIEW_TOKEN", "root-dashboard-test-token-not-production")
    if str(repository_root / "src") not in sys.path:
        sys.path.insert(0, str(repository_root / "src"))
    import satya.auth as root_auth
    importlib.reload(root_auth)
    app_test = AppTest.from_file(str(repository_root / "app.py"), default_timeout=30).run()
    nav = next(item for item in app_test.radio if item.label == "Navigation")
    nav.set_value("Main Owner").run()
    action = next(item for item in app_test.button if item.label == "Run Orchestrator Scan Now")
    action.click().run()
    assert any("Admin Access Required" in str(item.value) for item in app_test.error)
    assert not app_test.exception
