import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Non-production defaults strictly for unit tests.
os.environ.setdefault("SATYA_AGENT_KEYS", "root-test-agent-key-not-for-production")
os.environ.setdefault("HUMAN_VIEW_TOKEN", "root-test-human-token-not-for-production")
os.environ.setdefault(
    "AUDIT_SECRET",
    "root-unit-test-audit-secret-at-least-32-characters-long",
)
