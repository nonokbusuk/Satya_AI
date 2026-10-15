import os
import shutil
import unittest
import sys
import importlib

# Ensure src is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from satya.core.goal_guardian import GoalGuardian, save_goal, load_goal, load_goal_alerts
from satya.sdk.client import SatyaClient, GoalDriftError
from satya.core import storage

class TestGoalGuardian(unittest.TestCase):
    def setUp(self):
        self.test_dir = "test_goals_dir"
        self.storage_names = (
            "ROOT_DIR", "SATYA_DIR", "TASKS_DIR", "TRUTH_DIR", "AGENTS_DIR",
            "HEARTBEATS_DIR", "PULSE_DIR", "GOALS_DIR",
        )
        self.old_storage = {name: getattr(storage, name) for name in self.storage_names}
        self.old_env = {
            name: os.environ.get(name)
            for name in ("SATYA_AGENT_KEY", "SATYA_AGENT_KEYS", "AUDIT_SECRET", "HUMAN_VIEW_TOKEN")
        }
        os.makedirs(self.test_dir, exist_ok=True)
        storage.set_root(self.test_dir)

        test_key = "goal-guardian-test-agent-key-long-enough-123"
        os.environ["SATYA_AGENT_KEY"] = test_key
        os.environ["SATYA_AGENT_KEYS"] = test_key
        os.environ["AUDIT_SECRET"] = "goal-guardian-test-audit-secret-long-enough-123456"
        import satya.auth as auth
        importlib.reload(auth)

    def tearDown(self):
        for name, value in self.old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        import satya.auth as auth
        importlib.reload(auth)
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)
        for name, value in self.old_storage.items():
            setattr(storage, name, value)

    def test_save_and_load_goal(self):
        agent = "test_agent"
        goal = "Build user auth system using JWT"
        save_goal(agent, goal, 0.20, 0.10)
        
        loaded = load_goal(agent)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["goal"], goal)
        self.assertEqual(loaded["threshold"], 0.20)
        self.assertEqual(loaded["halt_threshold"], 0.10)

    def test_goal_guardian_alignment_ok(self):
        gg = GoalGuardian(agent_name="test_agent", goal="Build REST API", threshold=0.20, halt_threshold=0.05)
        # Highly aligned
        res = gg.check("Implement REST API endpoint for user data")
        self.assertTrue(res["aligned"])
        self.assertEqual(res["action"], "ok")

    def test_goal_guardian_alignment_drift_warn(self):
        gg = GoalGuardian(agent_name="test_agent", goal="Build user auth", threshold=0.50, halt_threshold=0.05)
        # Marginally/non-aligned message but above halt
        res = gg.check("Let's write a simple calculator application instead of auth")
        self.assertFalse(res["aligned"])
        self.assertEqual(res["action"], "warn")

    def test_goal_guardian_alignment_drift_halt(self):
        gg = GoalGuardian(agent_name="test_agent", goal="Build user auth", threshold=0.20, halt_threshold=0.15)
        # Totally unrelated message triggers halt
        res = gg.check("unrelated text to verify low score")
        self.assertFalse(res["aligned"])
        self.assertEqual(res["action"], "halt")
        self.assertIsNotNone(res["halt_directive"])

    def test_sdk_integration_raises_drift_error(self):
        # Initialize client
        client = SatyaClient(agent_name="test_agent", repo_path=self.test_dir)
        client.set_goal("Build user auth system", threshold=0.20, halt_threshold=0.15)
        # Set window to 1 so the aligned log doesn't carry over to the drifted log
        if client.goal_guardian:
            client.goal_guardian.window = 1
        
        # A highly aligned log should succeed
        client.log("Writing user login verification functionality")
        
        # A drifted log should raise GoalDriftError
        with self.assertRaises(GoalDriftError):
            client.log("unrelated word list that has completely zero similarity")

if __name__ == "__main__":
    unittest.main()


def test_save_goal_reports_persistence_failure(monkeypatch, tmp_path):
    from src.satya.core import storage
    from src.satya.core.goal_guardian import save_goal

    monkeypatch.setattr(storage, "SATYA_DIR", str(tmp_path / "satya_data"))
    monkeypatch.setattr(storage, "save_json", lambda *args, **kwargs: False)
    assert save_goal("agent-test", "A sufficiently detailed test goal") is None
