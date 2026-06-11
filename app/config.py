"""
Draftly – centralised settings loaded from environment / .env file.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Google OAuth2
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/auth/callback"

    # Anthropic
    anthropic_api_key: str = ""

    # Security
    secret_key: str = "dev-secret-key-change-in-production"
    encryption_key: str = ""          # Fernet key for token encryption

    # Database
    database_url: str = "sqlite+aiosqlite:///./draftly.db"

    # App behaviour
    app_env: str = "development"
    log_level: str = "INFO"
    max_emails_to_fetch: int = 10
    max_sent_emails_for_style: int = 5

    # Gmail API scopes required
    gmail_scopes: list[str] = [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/gmail.compose",
    ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
