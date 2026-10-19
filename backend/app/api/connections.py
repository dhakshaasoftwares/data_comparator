from __future__ import annotations

import importlib.util
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..connectors import CONNECTORS, ConnectorError, get_connector, secret_fields
from ..connectors.sql import SQLAlchemyConnector
from ..db import Connection, Job, User, get_db
from ..plans import enforce
from ..security import decrypt_secrets, encrypt_secrets
from ..services import connector_for
from .deps import current_user, org_plan, require

router = APIRouter(prefix="/api", tags=["connections"])


class ConnectionIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    type: str
    config: dict[str, Any] = Field(default_factory=dict)


class TestIn(BaseModel):
    type: str
    config: dict[str, Any] = Field(default_factory=dict)
    connection_id: int | None = None  # reuse stored secrets left blank in the form


def _driver_installed(cls) -> bool:
    if issubclass(cls, SQLAlchemyConnector) and cls.driver_module:
        return (
            importlib.util.find_spec(cls.driver_module.split(".")[0]) is not None
            and importlib.util.find_spec(cls.driver_module) is not None
        )
    return True


@router.get("/connector-types")
def connector_types(_: User = Depends(current_user)):
    return [{**asdict(cls.spec), "driver_installed": _driver_installed(cls)} for cls in CONNECTORS.values()]


def _split(type_: str, config: dict[str, Any]) -> tuple[dict, dict]:
    if type_ not in CONNECTORS:
        raise HTTPException(400, f"Unknown connector type '{type_}'")
    secrets = secret_fields(type_)
    allowed = {f.name for f in CONNECTORS[type_].spec.fields}
    plain = {k: v for k, v in config.items() if k in allowed and k not in secrets}
    hidden = {k: v for k, v in config.items() if k in secrets and v not in (None, "")}
    return plain, hidden


def connection_out(c: Connection) -> dict:
    stored = set(decrypt_secrets(c.secrets)) if c.secrets else set()
    return {
        "id": c.id,
        "name": c.name,
        "type": c.type,
        "config": c.config,
        "secrets_set": sorted(stored),
        "created_at": c.created_at,
        "updated_at": c.updated_at,
    }


def _get(db: Session, user: User, conn_id: int) -> Connection:
    conn = db.get(Connection, conn_id)
    if conn is None or conn.organization_id != user.organization_id:
        raise HTTPException(404, "Connection not found")
    return conn


@router.get("/connections")
def list_connections(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Connection).where(Connection.organization_id == user.organization_id).order_by(Connection.name)
    )
    return [connection_out(c) for c in rows]


@router.post("/connections")
def create_connection(body: ConnectionIn, user: User = Depends(require("editor")), db: Session = Depends(get_db)):
    _, plan = org_plan(db, user)
    count = (
        db.scalar(
            select(func.count()).select_from(Connection).where(Connection.organization_id == user.organization_id)
        )
        or 0
    )
    enforce(plan.max_connections, count, "connections")
    plain, hidden = _split(body.type, body.config)
    conn = Connection(
        organization_id=user.organization_id,
        name=body.name,
        type=body.type,
        config=plain,
        secrets=encrypt_secrets(hidden),
    )
    db.add(conn)
    db.commit()
    return connection_out(conn)


@router.put("/connections/{conn_id}")
def update_connection(
    conn_id: int, body: ConnectionIn, user: User = Depends(require("editor")), db: Session = Depends(get_db)
):
    conn = _get(db, user, conn_id)
    plain, hidden = _split(body.type, body.config)
    secrets = decrypt_secrets(conn.secrets) if body.type == conn.type else {}
    secrets.update(hidden)  # blank secret fields keep their stored value
    conn.name, conn.type, conn.config, conn.secrets = body.name, body.type, plain, encrypt_secrets(secrets)
    db.commit()
    return connection_out(conn)


@router.delete("/connections/{conn_id}")
def delete_connection(conn_id: int, user: User = Depends(require("editor")), db: Session = Depends(get_db)):
    conn = _get(db, user, conn_id)
    for job in db.scalars(select(Job).where(Job.organization_id == user.organization_id)):
        if conn_id in (job.config.get("source_connection_id"), job.config.get("target_connection_id")):
            raise HTTPException(409, f"Connection is used by job '{job.name}'")
    db.delete(conn)
    db.commit()
    return {"ok": True}


@router.post("/connections/test")
def test_connection(body: TestIn, user: User = Depends(require("editor")), db: Session = Depends(get_db)):
    plain, hidden = _split(body.type, body.config)
    if body.connection_id:
        stored = _get(db, user, body.connection_id)
        if stored.type == body.type:
            hidden = {**decrypt_secrets(stored.secrets), **hidden}
    connector = get_connector(body.type, {**plain, **hidden})
    try:
        connector.test()
        tables = connector.list_tables()
        return {"ok": True, "message": f"Connected. {len(tables)} table(s) visible.", "tables": tables[:500]}
    except ConnectorError as exc:
        return {"ok": False, "message": str(exc)}
    except Exception as exc:
        return {"ok": False, "message": str(exc).splitlines()[0] if str(exc) else type(exc).__name__}
    finally:
        connector.close()


@router.get("/connections/{conn_id}/tables")
def list_tables(conn_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    connector = connector_for(_get(db, user, conn_id))
    try:
        return connector.list_tables()
    except ConnectorError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        connector.close()


@router.get("/connections/{conn_id}/columns")
def list_columns(conn_id: int, table: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    connector = connector_for(_get(db, user, conn_id))
    try:
        cols = connector.get_columns(table)
        pk = connector.get_primary_key(table)
        return {"columns": [asdict(c) for c in cols], "primary_key": pk}
    except ConnectorError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        connector.close()
