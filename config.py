"""Tokenized infrastructure configuration.

Every business system is addressed by an environment token rather than a
hard-coded host. Mock tools bind those tokens to in-memory dummy data.
Gemini is the only live network dependency (GEMINI_API_KEY).
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
ENV_FILE = ROOT_DIR / ".env"


def _load_env_file(path: Path) -> None:
    """Populate os.environ from a dotenv-style file without extra deps."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key, value)


_load_env_file(ENV_FILE)
_load_env_file(ROOT_DIR / ".env.example")


def _token(name: str, default: str) -> str:
    return os.environ.get(name, default)


class Settings:
    """Named interface tokens for every system this agent may touch."""

    postgres_database_1: str = _token(
        "POSTGRES_DATABASE_1",
        "mock://postgres-primary.internal:5432/ecommerce",
    )
    crm_db_2: str = _token(
        "CRM_DB_2",
        "mock://crm-replica.internal:5432/crm",
    )
    stripe_payment_gateway_1: str = _token(
        "STRIPE_PAYMENT_GATEWAY_1",
        _token("STRIPE_GATEWAY_URL", "mock://stripe-gateway.internal/v1"),
    )
    zendesk_api_1: str = _token(
        "ZENDESK_API_1",
        "mock://zendesk.internal/api/v2",
    )
    zendesk_api_token: str = _token(
        "ZENDESK_API_TOKEN",
        "tok_zendesk_mock_not_a_real_secret",
    )

    skills_dir: Path = ROOT_DIR / "skills"

    # Token names used in audit traces (stable, env-independent identifiers).
    TOKEN_POSTGRES = "POSTGRES_DATABASE_1"
    TOKEN_CRM = "CRM_DB_2"
    TOKEN_STRIPE = "STRIPE_PAYMENT_GATEWAY_1"
    TOKEN_ZENDESK = "ZENDESK_API_1"

    @property
    def gemini_api_key(self) -> str:
        return os.environ.get("GEMINI_API_KEY", "").strip()

    @property
    def gemini_model(self) -> str:
        return os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"


settings = Settings()
