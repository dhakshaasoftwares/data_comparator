from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import Connection, Job, Run, User, get_db
from .deps import current_user
from .jobs import run_out

router = APIRouter(prefix="/api", tags=["dashboard"])


@router.get("/dashboard")
def dashboard(user: User = Depends(current_user), db: Session = Depends(get_db)):
    org = user.organization_id
    n_conn = db.scalar(select(func.count()).select_from(Connection).where(Connection.organization_id == org)) or 0
    jobs = list(db.scalars(select(Job).where(Job.organization_id == org)))
    recent = list(db.scalars(select(Run).where(Run.organization_id == org).order_by(Run.id.desc()).limit(60)))

    # Latest completed run of each job = current state of the migration portfolio.
    latest: dict[int, Run] = {}
    for r in recent:
        if r.status == "completed" and r.job_id not in latest:
            latest[r.job_id] = r
    for j in jobs:
        if j.id not in latest:
            r = db.scalar(
                select(Run).where(Run.job_id == j.id, Run.status == "completed").order_by(Run.id.desc()).limit(1)
            )
            if r:
                latest[j.id] = r

    tables, status_counts = [], {"pass": 0, "warn": 0, "fail": 0, "error": 0}
    for job_id, r in latest.items():
        for t in (r.result or {}).get("tables", []):
            status_counts[t.get("status", "error")] = status_counts.get(t.get("status", "error"), 0) + 1
            tables.append(
                {
                    "job_id": job_id,
                    "job_name": r.job.name,
                    "run_id": r.id,
                    "source_table": t["source_table"],
                    "target_table": t["target_table"],
                    "score": t.get("score"),
                    "status": t.get("status"),
                    "top_issue": (t.get("insights") or [{}])[0].get("title")
                    or ((t.get("deductions") or [{}])[0].get("reason"))
                    or t.get("error"),
                }
            )
    scores = [r.score for r in latest.values() if r.score is not None]
    rows = sum(((r.result or {}).get("summary", {}).get("source_rows", 0) or 0) for r in latest.values())

    trend = [
        {"run_id": r.id, "job_name": r.job.name, "score": r.score, "status": r.result_status, "at": r.finished_at}
        for r in reversed(recent)
        if r.status == "completed" and r.score is not None
    ][-30:]
    worst = sorted((t for t in tables if t["score"] is not None), key=lambda t: t["score"])[:8]

    return {
        "kpis": {
            "connections": n_conn,
            "jobs": len(jobs),
            "tables_tracked": len(tables),
            "average_score": round(sum(scores) / len(scores), 2) if scores else None,
            "rows_validated": rows,
            "runs_total": db.scalar(select(func.count()).select_from(Run).where(Run.organization_id == org)) or 0,
            "running": sum(1 for r in recent if r.status in ("queued", "running")),
        },
        "table_status": status_counts,
        "trend": trend,
        "worst_tables": worst,
        "recent_runs": [run_out(r) for r in recent[:10]],
        "jobs": [
            {
                "id": j.id,
                "name": j.name,
                "score": latest[j.id].score if j.id in latest else None,
                "status": latest[j.id].result_status if j.id in latest else None,
                "last_run_at": latest[j.id].finished_at if j.id in latest else None,
                "tables": len(j.config.get("tables", [])),
            }
            for j in jobs
        ],
    }
