from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated application settings. Secrets must be supplied by the environment."""

    satya_agent_keys: str = ""
    audit_secret: str = ""
    database_url: str = "sqlite+aiosqlite:///./satya.db"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @model_validator(mode="after")
    def validate_required_secrets(self):
        keys = [key.strip() for key in self.satya_agent_keys.split(",") if key.strip()]
        if not keys:
            raise ValueError("SATYA_AGENT_KEYS must contain at least one configured key.")
        weak_keys = {"demo_key", "prod-key-123", "change-me", "changeme"}
        if any(key.lower() in weak_keys or "replace-with" in key.lower() or "placeholder" in key.lower() for key in keys):
            raise ValueError("SATYA_AGENT_KEYS contains a sample/placeholder value; generate a unique secret.")
        if any(len(key) < 32 for key in keys):
            raise ValueError("Each SATYA_AGENT_KEYS value must be at least 32 characters.")
        secret = self.audit_secret.strip()
        if len(secret) < 32:
            raise ValueError("AUDIT_SECRET must be at least 32 characters.")
        if secret.lower() in {"default_secret", "my-super-secret-audit-key", "docker-secret-audit-key"} or "replace-with" in secret.lower() or "placeholder" in secret.lower():
            raise ValueError("AUDIT_SECRET contains an unsafe sample/placeholder value; generate a unique secret.")
        if not self.database_url.strip():
            raise ValueError("DATABASE_URL must not be empty.")
        return self


settings = Settings()
