from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlparse

from cryptography.fernet import Fernet
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    app_env: Literal["development", "test", "production"] = "development"
    app_base_url: str = "http://127.0.0.1:8000"
    database_url: SecretStr = SecretStr("postgresql+psycopg://localhost/karto")
    upload_dir: Path = ROOT / "var" / "uploads"
    mail_dir: Path = ROOT / "var" / "mail"
    allowed_hosts: str = "localhost,127.0.0.1,testserver"
    ai_provider: Literal["disabled", "gemini", "mock"] = "disabled"
    ai_model: str = "gemini-3.8-flash"
    gemini_api_key: SecretStr = SecretStr("")
    ai_daily_budget_usd: Decimal = Field(default=Decimal("0"), ge=0)
    ai_input_price_per_million: Decimal = Field(default=Decimal("0.75"), ge=0)
    ai_output_price_per_million: Decimal = Field(default=Decimal("3.75"), ge=0)
    ai_price_version: str = "gemini-3.8-flash-2026-09-24"
    ai_max_input_tokens: int = Field(default=16384, ge=1024, le=100000)
    ai_max_output_tokens: int = Field(default=4096, ge=256, le=4096)
    ai_terms_accepted: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_from: str = ""
    smtp_starttls: bool = True
    smtp_use_tls: bool = False
    mail_backend: Literal["file", "smtp"] = "file"
    uzum_seller_email: SecretStr = SecretStr("")
    uzum_seller_password: SecretStr = SecretStr("")
    uzum_seller_api_key: SecretStr = SecretStr("")
    initial_monthly_limit: int = Field(default=20, ge=1)
    regeneration_monthly_limit: int = Field(default=40, ge=1)
    daily_job_limit: int = Field(default=10, ge=1)
    daily_draft_limit: int = Field(default=10, ge=1)
    max_queue_size: int = Field(default=20, ge=1)
    consent_version: str = "2026-09-28"
    maintenance_mode: bool = False
    backup_key: SecretStr = SecretStr("")
    backup_dir: Path = ROOT / "var" / "backups"
    backup_bucket: str = ""
    backup_prefix: str = "karto"
    backup_endpoint_url: str = ""
    alert_webhook_url: SecretStr = SecretStr("")
    audit_log: Path = ROOT / "var" / "audit.log"
    forwarded_allow_ips: str = "127.0.0.1"

    @property
    def secure_cookies(self) -> bool:
        return urlparse(self.app_base_url).scheme == "https"

    @model_validator(mode="after")
    def validate_runtime(self) -> Self:
        if not self.database_url.get_secret_value().startswith("postgresql+psycopg://"):
            raise ValueError("DATABASE_URL must use postgresql+psycopg://")
        origin = urlparse(self.app_base_url)
        if origin.scheme not in {"http", "https"} or not origin.hostname or origin.path not in {"", "/"}:
            raise ValueError("APP_BASE_URL must be an HTTP(S) origin")
        if self.ai_provider == "gemini":
            if not self.gemini_api_key.get_secret_value() or not self.ai_terms_accepted:
                raise ValueError("Gemini requires GEMINI_API_KEY and AI_TERMS_ACCEPTED")
            if min(self.ai_daily_budget_usd, self.ai_input_price_per_million, self.ai_output_price_per_million) <= 0:
                raise ValueError("Gemini requires a positive budget and verified model prices")
        if self.backup_key.get_secret_value():
            try:
                Fernet(self.backup_key.get_secret_value().encode())
            except ValueError:
                raise ValueError("BACKUP_KEY must be generated with `karto create-backup-key`") from None
        if self.app_env == "production":
            if not self.secure_cookies or self.ai_provider != "gemini":
                raise ValueError("Production requires HTTPS and Gemini; mock/disabled is not allowed")
            if self.mail_backend != "smtp" or not self.smtp_host or not self.smtp_from:
                raise ValueError("Production requires configured SMTP")
            if not self.backup_key.get_secret_value() or not self.backup_bucket:
                raise ValueError("Production requires encrypted offsite backups")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
