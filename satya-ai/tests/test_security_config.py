import pytest

from pydantic import ValidationError
from satya.config import Settings


def test_configuration_requires_non_sample_secrets():
    with pytest.raises(ValidationError):
        Settings(satya_agent_keys="", audit_secret="")


def test_configuration_rejects_demo_credentials():
    with pytest.raises(ValidationError):
        Settings(
            satya_agent_keys="DEMO_KEY",
            audit_secret="a-random-secret-that-is-long-enough-123456789",
        )
    with pytest.raises(ValidationError):
        Settings(
            satya_agent_keys="a-random-agent-key-long-enough-123456",
            audit_secret="short",
        )


def test_configuration_accepts_strong_test_secrets():
    settings = Settings(
        satya_agent_keys="a-random-agent-key-long-enough-123456",
        audit_secret="a-random-secret-that-is-long-enough-123456789",
        database_url="sqlite+aiosqlite:///:memory:",
    )
    assert settings.satya_agent_keys
