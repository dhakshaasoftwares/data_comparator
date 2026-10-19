"""FastAPI application entry point."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import select

from . import __version__
from .api import auth, billing, connections, dashboard, jobs
from .db import Connection, Job, Organization, SessionLocal, User, init_db
from .engine.config import JobConfig
from .security import encrypt_secrets, hash_password
from .services import recover_runs
from .settings import get_settings

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("data_comparator")

DEMO_EMAIL = "demo@example.com"
DEMO_PASSWORD = "demo1234"


def seed_demo() -> None:
    """Create demo data and a demo account on an empty installation."""
    from .demo import generate

    settings = get_settings()
    generate(settings.data_root)
    db = SessionLocal()
    try:
        if db.scalar(select(User).limit(1)):
            return
        org = Organization(name="Demo Workspace", plan=settings.default_plan)
        db.add(org)
        db.flush()
        user = User(
            email=DEMO_EMAIL,
            name="Demo User",
            password_hash=hash_password(DEMO_PASSWORD),
            role="admin",
            organization_id=org.id,
        )
        src = Connection(
            organization_id=org.id,
            name="Legacy Oracle (demo SQLite)",
            type="sqlite",
            config={"path": "demo/legacy_oracle.db"},
            secrets=encrypt_secrets({}),
        )
        tgt = Connection(
            organization_id=org.id,
            name="Spark Parquet lake (demo)",
            type="files",
            config={"base_path": "demo/spark_lake", "format": "parquet", "hive_partitioning": True},
            secrets=encrypt_secrets({}),
        )
        db.add_all([user, src, tgt])
        db.flush()
        config = JobConfig.model_validate(
            {
                "source_connection_id": src.id,
                "target_connection_id": tgt.id,
                "tables": [
                    {
                        "source_table": "CUSTOMERS",
                        "target_table": "customers",
                        "key_columns": ["CUSTOMER_ID"],
                        "rules": [
                            {
                                "type": "regex",
                                "column": "email",
                                "pattern": r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}",
                                "severity": "medium",
                                "name": "Email format (lower case)",
                            },
                            {"type": "not_null", "column": "city", "severity": "high"},
                        ],
                    },
                    {
                        "source_table": "ORDERS",
                        "target_table": "orders",
                        "key_columns": ["ORDER_ID"],
                        "rules": [
                            {
                                "type": "range",
                                "column": "amount",
                                "min": 0,
                                "severity": "high",
                                "name": "Amount is not negative",
                            },
                            {
                                "type": "allowed_values",
                                "column": "status",
                                "values": ["NEW", "PAID", "SHIPPED", "DELIVERED", "CANCELLED"],
                            },
                        ],
                    },
                    {"source_table": "PRODUCTS", "target_table": "products", "key_columns": ["PRODUCT_ID"]},
                ],
                "options": {"mode": "full"},
            }
        )
        db.add(
            Job(
                organization_id=org.id,
                name="Legacy Oracle -> Spark lake (demo)",
                description="Demo migration with planted defects: missing rows, case drift, "
                "decimal rounding and negative amounts.",
                config=config.model_dump(by_alias=True),
                created_by=None,
            )
        )
        db.commit()
        log.info("Demo workspace created: %s / %s", DEMO_EMAIL, DEMO_PASSWORD)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    settings.work_dir.mkdir(parents=True, exist_ok=True)
    settings.data_root.mkdir(parents=True, exist_ok=True)
    init_db()
    if settings.seed_demo:
        seed_demo()
    recover_runs()
    yield


app = FastAPI(
    title="Data Comparator",
    version=__version__,
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
for module in (auth, connections, jobs, dashboard, billing):
    app.include_router(module.router)
app.include_router(auth.users_router)


@app.get("/api/health")
def health():
    return {"status": "ok", "version": __version__}


# Serve the built web UI when present (single-container deployment).
FRONTEND_DIR = Path(os.getenv("FRONTEND_DIR", Path(__file__).resolve().parents[2] / "frontend" / "dist"))
if FRONTEND_DIR.is_dir():

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        file = (FRONTEND_DIR / path).resolve()
        if path and file.is_file() and FRONTEND_DIR.resolve() in file.parents:
            return FileResponse(file)
        return FileResponse(FRONTEND_DIR / "index.html")
