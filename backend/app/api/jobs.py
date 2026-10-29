from __future__ import annotations

import datetime as dt
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..connectors import ConnectorError
from ..connectors.errors import connector_http_error
from ..db import Connection, Job, Run, UsageRecord, User, get_db
from ..engine.config import ColumnMapping, JobConfig
from ..engine.keys import detect_unique_key
from ..engine.recommend import recommend
from ..engine.schema_compare import compare_schema
from ..engine.types import parse_type
from ..plans import Plan, enforce
from ..services import connector_for, submit_run
from .deps import current_user, org_plan, require

router = APIRouter(prefix="/api", tags=["jobs"])


class JobIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    config: dict[str, Any]


class PreviewIn(BaseModel):
    source_connection_id: int
    target_connection_id: int
    source_table: str
    target_table: str
    column_mappings: list[ColumnMapping] = Field(default_factory=list)
    exclude_columns: list[str] = Field(default_factory=list)


class AutoPairIn(BaseModel):
    source_connection_id: int
    target_connection_id: int


def _conn(db: Session, user: User, conn_id: int) -> Connection:
    conn = db.get(Connection, conn_id)
    if conn is None or conn.organization_id != user.organization_id:
        raise HTTPException(404, f"Connection {conn_id} not found")
    return conn


def _validate(db: Session, user: User, plan: Plan, raw: dict) -> JobConfig:
    try:
        cfg = JobConfig.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first["loc"])
        raise HTTPException(422, f"Invalid configuration at {loc}: {first['msg']}") from exc
    _conn(db, user, cfg.source_connection_id)
    _conn(db, user, cfg.target_connection_id)
    if plan.max_tables_per_job is not None and len(cfg.tables) > plan.max_tables_per_job:
        raise HTTPException(402, f"Your plan allows {plan.max_tables_per_job} tables per job. Upgrade to add more.")
    if cfg.options.mode not in plan.modes:
        raise HTTPException(402, f"Mode '{cfg.options.mode}' is not included in your plan")
    if not plan.quality_rules and any(t.rules for t in cfg.tables):
        raise HTTPException(402, "Data quality rules are not included in your plan. Upgrade to use them.")
    return cfg


def run_out(r: Run, full: bool = False) -> dict:
    out = {
        "id": r.id,
        "job_id": r.job_id,
        "job_name": r.job.name if r.job else None,
        "status": r.status,
        "progress": r.progress,
        "current_table": r.current_table,
        "score": r.score,
        "result_status": r.result_status,
        "error": r.error,
        "created_at": r.created_at,
        "started_at": r.started_at,
        "finished_at": r.finished_at,
        "summary": (r.result or {}).get("summary"),
    }
    if full:
        out["result"] = r.result
        out["config"] = r.config
    return out


def previous_score(db: Session, run: Run) -> float | None:
    """Score of the job's completed run before ``run``, to show the trend."""
    return db.scalar(
        select(Run.score)
        .where(Run.job_id == run.job_id, Run.id < run.id, Run.status == "completed")
        .order_by(Run.id.desc())
        .limit(1)
    )


def job_out(j: Job, last: Run | None = None) -> dict:
    return {
        "id": j.id,
        "name": j.name,
        "description": j.description,
        "config": j.config,
        "created_at": j.created_at,
        "updated_at": j.updated_at,
        "table_count": len(j.config.get("tables", [])),
        "last_run": run_out(last) if last else None,
    }


def _get_job(db: Session, user: User, job_id: int) -> Job:
    job = db.get(Job, job_id)
    if job is None or job.organization_id != user.organization_id:
        raise HTTPException(404, "Job not found")
    return job


def _last_run(db: Session, job_id: int) -> Run | None:
    return db.scalar(select(Run).where(Run.job_id == job_id).order_by(Run.id.desc()).limit(1))


@router.get("/jobs")
def list_jobs(user: User = Depends(current_user), db: Session = Depends(get_db)):
    jobs = db.scalars(select(Job).where(Job.organization_id == user.organization_id).order_by(Job.updated_at.desc()))
    out = []
    for j in jobs:
        last = _last_run(db, j.id)
        item = job_out(j, last)
        if last is not None:
            item["last_run"]["previous_score"] = previous_score(db, last)
        out.append(item)
    return out


@router.post("/jobs")
def create_job(body: JobIn, user: User = Depends(require("editor")), db: Session = Depends(get_db)):
    _, plan = org_plan(db, user)
    count = db.scalar(select(func.count()).select_from(Job).where(Job.organization_id == user.organization_id)) or 0
    enforce(plan.max_jobs, count, "jobs")
    cfg = _validate(db, user, plan, body.config)
    job = Job(
        organization_id=user.organization_id,
        name=body.name,
        description=body.description,
        config=cfg.model_dump(by_alias=True),
        created_by=user.id,
    )
    db.add(job)
    db.commit()
    return job_out(job)


@router.get("/jobs/{job_id}")
def get_job(job_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job = _get_job(db, user, job_id)
    return job_out(job, _last_run(db, job.id))


@router.put("/jobs/{job_id}")
def update_job(job_id: int, body: JobIn, user: User = Depends(require("editor")), db: Session = Depends(get_db)):
    job = _get_job(db, user, job_id)
    _, plan = org_plan(db, user)
    cfg = _validate(db, user, plan, body.config)
    job.name, job.description, job.config = body.name, body.description, cfg.model_dump(by_alias=True)
    db.commit()
    return job_out(job, _last_run(db, job.id))


@router.delete("/jobs/{job_id}")
def delete_job(job_id: int, user: User = Depends(require("editor")), db: Session = Depends(get_db)):
    job = _get_job(db, user, job_id)
    if db.scalar(
        select(func.count()).select_from(Run).where(Run.job_id == job_id, Run.status.in_(("queued", "running")))
    ):
        raise HTTPException(409, "Job has a run in progress")
    db.delete(job)
    db.commit()
    return {"ok": True}


@router.post("/jobs/{job_id}/run")
def start_run(job_id: int, user: User = Depends(require("editor")), db: Session = Depends(get_db)):
    job = _get_job(db, user, job_id)
    _, plan = org_plan(db, user)
    period = dt.date.today().strftime("%Y-%m")
    used = (
        db.scalar(
            select(UsageRecord.runs).where(
                UsageRecord.organization_id == user.organization_id, UsageRecord.period == period
            )
        )
        or 0
    )
    enforce(plan.runs_per_month, used, "runs per month")
    cfg = _validate(db, user, plan, job.config)
    run = Run(
        organization_id=user.organization_id, job_id=job.id, config=cfg.model_dump(by_alias=True), triggered_by=user.id
    )
    db.add(run)
    db.commit()
    submit_run(run.id)
    return run_out(run)


@router.post("/preview/schema")
def preview_schema(body: PreviewIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Schema comparison + key suggestion for the job editor (no data is read)."""
    src = connector_for(_conn(db, user, body.source_connection_id))
    tgt = connector_for(_conn(db, user, body.target_connection_id))
    try:
        s_cols, t_cols = src.get_columns(body.source_table), tgt.get_columns(body.target_table)
        result = compare_schema(s_cols, t_cols, src.dialect, tgt.dialect, body.column_mappings, body.exclude_columns)
        pk = src.get_primary_key(body.source_table)
        return {
            **result.to_dict(),
            "suggested_keys": pk,
            "source_columns": [c.name for c in s_cols],
            "target_columns": [c.name for c in t_cols],
        }
    except ConnectorError as exc:
        raise connector_http_error(exc) from exc
    finally:
        src.close()
        tgt.close()


def _norm(name: str) -> str:
    base = name.rsplit(".", 1)[-1].lower()
    base = re.sub(r"\.(parquet|csv|json)$", "", base)
    return re.sub(r"[^a-z0-9]", "", base)


@router.post("/preview/auto-pair")
def auto_pair(body: AutoPairIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Bulk mode: pair every source table with the target table of the same (normalised) name."""
    src = connector_for(_conn(db, user, body.source_connection_id))
    tgt = connector_for(_conn(db, user, body.target_connection_id))
    try:
        s_tables, t_tables = src.list_tables(), tgt.list_tables()
    except ConnectorError as exc:
        raise connector_http_error(exc) from exc
    finally:
        src.close()
        tgt.close()
    index = {_norm(t): t for t in t_tables}
    pairs = [{"source_table": s, "target_table": index[_norm(s)]} for s in s_tables if _norm(s) in index]
    matched_t = {p["target_table"] for p in pairs}
    return {
        "pairs": pairs,
        "unmatched_source": [s for s in s_tables if _norm(s) not in index],
        "unmatched_target": [t for t in t_tables if t not in matched_t],
    }


@router.get("/runs")
def list_runs(
    job_id: int | None = None, limit: int = 50, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    q = select(Run).where(Run.organization_id == user.organization_id)
    if job_id:
        q = q.where(Run.job_id == job_id)
    return [run_out(r) for r in db.scalars(q.order_by(Run.id.desc()).limit(min(limit, 500)))]


@router.get("/runs/{run_id}")
def get_run(run_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    run = db.get(Run, run_id)
    if run is None or run.organization_id != user.organization_id:
        raise HTTPException(404, "Run not found")
    return {**run_out(run, full=True), "previous_score": previous_score(db, run)}


@router.delete("/runs/{run_id}")
def delete_run(run_id: int, user: User = Depends(require("editor")), db: Session = Depends(get_db)):
    run = db.get(Run, run_id)
    if run is None or run.organization_id != user.organization_id:
        raise HTTPException(404, "Run not found")
    if run.status in ("queued", "running"):
        raise HTTPException(409, "Run is in progress")
    db.delete(run)
    db.commit()
    return {"ok": True}


class DetectKeyIn(BaseModel):
    source_connection_id: int
    source_table: str
    source_filter: str | None = None


@router.post("/preview/detect-key")
def detect_key(body: DetectKeyIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Primary key, or a column that is unique and never NULL (one aggregate query in the source)."""
    src = connector_for(_conn(db, user, body.source_connection_id))
    try:
        pk = src.get_primary_key(body.source_table)
        if pk:
            return {"key_columns": pk, "how": "primary_key"}
        cols = src.get_columns(body.source_table)
        types = {c.name: parse_type(c.native_type, src.dialect) for c in cols}
        found = detect_unique_key(src, body.source_table, [c.name for c in cols], types, body.source_filter)
        return {"key_columns": found, "how": "detected" if found else "none"}
    except ConnectorError as exc:
        raise connector_http_error(exc) from exc
    finally:
        src.close()


@router.get("/preview/recommendations")
def recommendations(
    source_connection_id: int,
    target_connection_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    src = connector_for(_conn(db, user, source_connection_id))
    tgt = connector_for(_conn(db, user, target_connection_id))
    return recommend(src.dialect, tgt.dialect)


@router.post("/jobs/{job_id}/duplicate")
def duplicate_job(job_id: int, user: User = Depends(require("editor")), db: Session = Depends(get_db)):
    job = _get_job(db, user, job_id)
    _, plan = org_plan(db, user)
    count = db.scalar(select(func.count()).select_from(Job).where(Job.organization_id == user.organization_id)) or 0
    enforce(plan.max_jobs, count, "jobs")
    copy = Job(
        organization_id=user.organization_id,
        name=f"{job.name} (copy)",
        description=job.description,
        config=job.config,
        created_by=user.id,
    )
    db.add(copy)
    db.commit()
    return job_out(copy)
