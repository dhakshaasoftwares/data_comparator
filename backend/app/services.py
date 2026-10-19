"""Glue between stored entities and the comparison engine."""

from __future__ import annotations

import datetime as dt
import logging
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select
from sqlalchemy.orm import Session

from .connectors import Connector, get_connector
from .db import Connection, Organization, Run, SessionLocal, UsageRecord, utcnow
from .engine.config import JobConfig
from .engine.runner import run_job
from .plans import get_plan
from .security import decrypt_secrets
from .settings import get_settings

log = logging.getLogger(__name__)
_executor: ThreadPoolExecutor | None = None


def connector_for(conn: Connection) -> Connector:
    return get_connector(conn.type, {**(conn.config or {}), **decrypt_secrets(conn.secrets)})


def executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=get_settings().max_workers, thread_name_prefix="run")
    return _executor


def submit_run(run_id: int) -> None:
    executor().submit(execute_run, run_id)


def _record_usage(db: Session, org_id: int, rows: int) -> None:
    period = dt.date.today().strftime("%Y-%m")
    rec = db.scalar(select(UsageRecord).where(UsageRecord.organization_id == org_id, UsageRecord.period == period))
    if rec is None:
        rec = UsageRecord(organization_id=org_id, period=period, runs=0, rows_compared=0)
        db.add(rec)
    rec.runs += 1
    rec.rows_compared += rows


def execute_run(run_id: int) -> None:
    db = SessionLocal()
    src = tgt = None
    try:
        run = db.get(Run, run_id)
        if run is None or run.status != "queued":
            return
        run.status, run.started_at = "running", utcnow()
        db.commit()

        config = JobConfig.model_validate(run.config)
        org = db.get(Organization, run.organization_id)
        plan = get_plan(org.plan if org else "free")
        if plan.max_rows_per_table is not None:
            limit = config.options.max_rows
            config.options.max_rows = min(limit, plan.max_rows_per_table) if limit else plan.max_rows_per_table

        conns = {}
        for cid in (config.source_connection_id, config.target_connection_id):
            conn = db.get(Connection, cid)
            if conn is None or conn.organization_id != run.organization_id:
                raise ValueError(f"Connection {cid} not found")
            conns[cid] = conn
        src = connector_for(conns[config.source_connection_id])
        tgt = connector_for(conns[config.target_connection_id])

        def progress(done: int, total: int, table: str) -> None:
            run.progress = round(100.0 * done / total, 1) if total else 100.0
            run.current_table = table
            db.commit()

        result = run_job(config, src, tgt, progress)
        run.result = result
        run.score = result["summary"]["score"]
        run.result_status = result["summary"]["status"]
        run.status = "completed"
        _record_usage(db, run.organization_id, result["summary"]["source_rows"] + result["summary"]["target_rows"])
    except Exception as exc:
        log.exception("Run %s failed", run_id)
        db.rollback()
        run = db.get(Run, run_id)
        if run is not None:
            run.status = "failed"
            run.error = str(exc).splitlines()[0][:1000] if str(exc) else type(exc).__name__
    finally:
        for c in (src, tgt):
            if c is not None:
                c.close()
        run = db.get(Run, run_id)
        if run is not None:
            run.finished_at = utcnow()
            run.current_table = ""
            if run.status == "completed":
                run.progress = 100.0
        db.commit()
        db.close()


def recover_runs() -> None:
    """After a restart: re-queue waiting runs, fail runs that were interrupted."""
    db = SessionLocal()
    try:
        for run in db.scalars(select(Run).where(Run.status.in_(("queued", "running")))):
            if run.status == "running":
                run.status, run.error, run.finished_at = "failed", "Interrupted by a server restart", utcnow()
        db.commit()
        for run_id in db.scalars(select(Run.id).where(Run.status == "queued")):
            submit_run(run_id)
    finally:
        db.close()
