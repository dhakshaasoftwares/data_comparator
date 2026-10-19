"""Runtime settings, read from environment variables."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


class Settings:
    def __init__(self) -> None:
        base = Path(__file__).resolve().parent.parent
        self.database_url = os.getenv("DATABASE_URL", f"sqlite:///{base / 'var' / 'app.db'}")
        self.secret_key = os.getenv("SECRET_KEY", "dev-secret-change-me")
        # Fernet key for connection secrets; derived from SECRET_KEY when unset.
        self.encryption_key = os.getenv("ENCRYPTION_KEY", "")
        self.work_dir = Path(os.getenv("WORK_DIR", str(base / "var" / "work")))
        # Local files / SQLite databases may only be read below this directory.
        self.data_root = Path(os.getenv("DATA_ROOT", str(base.parent / "sample_data"))).resolve()
        self.cors_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]
        self.token_ttl_minutes = int(os.getenv("TOKEN_TTL_MINUTES", "720"))
        # When false every organisation may switch plans freely (evaluation mode).
        self.billing_enabled = _bool("BILLING_ENABLED", False)
        self.default_plan = os.getenv("DEFAULT_PLAN", "trial")
        self.max_workers = int(os.getenv("MAX_WORKERS", "2"))
        self.seed_demo = _bool("SEED_DEMO", True)
        # Stripe (subscriptions). Billing is live only when BILLING_ENABLED and a key are set.
        self.stripe_secret_key = os.getenv("STRIPE_SECRET_KEY", "")
        self.stripe_webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")
        self.stripe_prices = {
            "team": os.getenv("STRIPE_PRICE_TEAM", ""),
            "business": os.getenv("STRIPE_PRICE_BUSINESS", ""),
        }
        # Public URL of the web app, used for Stripe redirects.
        self.app_url = os.getenv("APP_URL", "http://localhost:3000").rstrip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings()
