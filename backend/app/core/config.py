"""Application configuration with fail-closed production validation."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import quote_plus, urlsplit

from fastapi import Request
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

EnvironmentType = Literal["development", "test", "staging", "production"]

_INSECURE_PLACEHOLDER_SUBSTRINGS = ("change_me", "ci_test_only")


def _contains_insecure_placeholder(value: str) -> bool:
    """Return True if value contains dev/CI placeholder markers (case-insensitive)."""
    lowered = value.lower()
    return any(token in lowered for token in _INSECURE_PLACEHOLDER_SUBSTRINGS)


def _validate_and_normalize_origin(raw_origin: str, *, require_https: bool) -> str:
    """Validate an origin string and normalize scheme, host, and default port."""
    candidate = raw_origin.strip()
    if not candidate or candidate.lower() == "null":
        raise ValueError("ALLOWED_ORIGINS must not contain empty or 'null' origins.")
    if "*" in candidate:
        raise ValueError("ALLOWED_ORIGINS must not contain wildcard '*' origins.")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F or ch.isspace() for ch in candidate):
        raise ValueError(
            "ALLOWED_ORIGINS entries must not contain whitespace or control characters."
        )
    if "?" in candidate or "#" in candidate:
        raise ValueError(
            "ALLOWED_ORIGINS entries must not contain query strings or fragments."
        )

    try:
        parsed = urlsplit(candidate)
        hostname = parsed.hostname
    except ValueError as exc:
        raise ValueError("ALLOWED_ORIGINS contains a malformed origin URL.") from exc

    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError("ALLOWED_ORIGINS entries must use http or https scheme.")
    if require_https and scheme != "https":
        raise ValueError(
            "ALLOWED_ORIGINS entries must use https when ENVIRONMENT=production."
        )
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("ALLOWED_ORIGINS entries must not include userinfo credentials.")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError(
            "ALLOWED_ORIGINS entries must not include paths, query strings, or fragments."
        )
    if not hostname:
        raise ValueError("ALLOWED_ORIGINS entries must include a valid hostname.")
    if parsed.netloc.endswith(":"):
        raise ValueError("ALLOWED_ORIGINS entries must not contain an empty port.")

    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("ALLOWED_ORIGINS entries contain an invalid port.") from exc

    if port is not None and (port < 1 or port > 65535):
        raise ValueError("ALLOWED_ORIGINS port must be between 1 and 65535.")

    host_part = f"[{hostname}]" if ":" in hostname else hostname
    if (
        port is None
        or (scheme == "http" and port == 80)
        or (scheme == "https" and port == 443)
    ):
        netloc_normalized = host_part
    else:
        netloc_normalized = f"{host_part}:{port}"

    return f"{scheme}://{netloc_normalized}"


class Settings(BaseSettings):
    """Application runtime settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # PostgreSQL components (optional when DATABASE_URL is explicitly supplied)
    postgres_user: str | None = None
    postgres_password: str | None = Field(default=None, repr=False)
    postgres_db: str | None = None
    database_url: str = Field(default="", repr=False)

    # Runtime environment, cookies, and CORS
    environment: EnvironmentType = "development"
    cookie_secure: bool = False
    allowed_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    # Cryptographic secrets (must be supplied via environment)
    csrf_hmac_secret: str = Field(..., min_length=1, repr=False)
    guest_order_hmac_secret: str = Field(..., min_length=1, repr=False)
    payment_webhook_secret: str = Field(..., min_length=1, repr=False)

    # Sandbox payments, session lifetime, and reservation sweeper
    payment_sandbox_mode: bool = True
    session_ttl_hours: int = Field(default=168, ge=1, le=8760)
    reservation_sweeper_interval_seconds: int = Field(default=60, ge=0, le=3600)

    # Server-authoritative shipping & tax configuration
    free_shipping_threshold_cents: int = Field(default=10000, ge=0)
    standard_shipping_cents: int = Field(default=1000, ge=0)
    tax_rate_bps: int = Field(default=800, ge=0, le=10000)

    # Frontend URL references present in .env.example (optional on backend runtime)
    next_public_api_base_url: str | None = None
    internal_api_base_url: str | None = None

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def _parse_allowed_origins(cls, value: object) -> list[str]:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",")]
        if isinstance(value, (list, tuple, set)):
            items: list[str] = []
            for item in value:
                if not isinstance(item, str):
                    raise ValueError("ALLOWED_ORIGINS entries must be strings.")
                items.append(item.strip())
            return items
        raise ValueError("ALLOWED_ORIGINS must be a comma-separated string or list of origins.")

    @model_validator(mode="after")
    def _validate_database_and_production_security(self) -> Settings:
        if not self.database_url.strip():
            if self.postgres_user and self.postgres_password and self.postgres_db:
                user = quote_plus(self.postgres_user)
                password = quote_plus(self.postgres_password)
                db = quote_plus(self.postgres_db)
                self.database_url = f"postgresql+psycopg://{user}:{password}@db:5432/{db}"
            else:
                raise ValueError(
                    "DATABASE_URL must be configured (or provide POSTGRES_USER, "
                    "POSTGRES_PASSWORD, and POSTGRES_DB)."
                )
        else:
            self.database_url = self.database_url.strip()

        require_https = self.environment == "production"
        self.allowed_origins = list(
            dict.fromkeys(
                _validate_and_normalize_origin(origin, require_https=require_https)
                for origin in self.allowed_origins
            )
        )

        if self.environment == "production":
            if self.payment_sandbox_mode:
                raise ValueError(
                    "PAYMENT_SANDBOX_MODE must be false when ENVIRONMENT=production."
                )
            if not self.cookie_secure:
                raise ValueError("COOKIE_SECURE must be true when ENVIRONMENT=production.")
            if not self.allowed_origins:
                raise ValueError(
                    "ALLOWED_ORIGINS must be non-empty when ENVIRONMENT=production."
                )
            if not self.database_url.lower().startswith(
                ("postgresql://", "postgresql+psycopg://")
            ):
                raise ValueError(
                    "DATABASE_URL must use a postgresql:// or postgresql+psycopg:// scheme "
                    "when ENVIRONMENT=production."
                )
            if _contains_insecure_placeholder(self.database_url):
                raise ValueError(
                    "DATABASE_URL must not contain development/CI placeholder credentials "
                    "when ENVIRONMENT=production."
                )
            if self.postgres_password is not None and _contains_insecure_placeholder(
                self.postgres_password
            ):
                raise ValueError(
                    "POSTGRES_PASSWORD (and DATABASE_URL) must not contain development/CI "
                    "placeholders when ENVIRONMENT=production."
                )

            for field_name in (
                "csrf_hmac_secret",
                "guest_order_hmac_secret",
                "payment_webhook_secret",
            ):
                secret_value = getattr(self, field_name)
                if (
                    secret_value.strip() != secret_value
                    or len(secret_value) < 32
                    or _contains_insecure_placeholder(secret_value)
                ):
                    raise ValueError(
                        f"{field_name.upper()} must be at least 32 characters without "
                        "leading/trailing whitespace or development/CI placeholders "
                        "when ENVIRONMENT=production."
                    )

            if (
                len(
                    {
                        self.csrf_hmac_secret,
                        self.guest_order_hmac_secret,
                        self.payment_webhook_secret,
                    }
                )
                != 3
            ):
                raise ValueError(
                    "CSRF_HMAC_SECRET, GUEST_ORDER_HMAC_SECRET, and PAYMENT_WEBHOOK_SECRET "
                    "must be distinct when ENVIRONMENT=production."
                )

        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings loaded from environment."""
    return Settings()


def get_app_settings(request: Request) -> Settings:
    """Resolve Settings from app.state if injected, otherwise from environment cache."""
    state_settings = getattr(request.app.state, "settings", None)
    if isinstance(state_settings, Settings):
        return state_settings
    return get_settings()
