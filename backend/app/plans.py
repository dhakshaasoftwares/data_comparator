"""Subscription plans and their limits."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from fastapi import HTTPException


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    price_month_usd: int | None  # None = contact sales
    max_users: int | None
    max_connections: int | None
    max_jobs: int | None
    max_tables_per_job: int | None
    max_rows_per_table: int | None  # staged rows per side
    runs_per_month: int | None
    modes: tuple[str, ...]
    quality_rules: bool
    api_access: bool
    self_hosted_agent: bool
    sso: bool
    support: str
    tagline: str = ""


ALL_MODES = ("counts", "profile", "full")


def _plan(**kw) -> Plan:
    kw.setdefault("modes", ALL_MODES)
    return Plan(**kw)


# fmt: off
PLANS: dict[str, Plan] = {p.id: p for p in [
    _plan(id="free", name="Free", price_month_usd=0, max_users=1, max_connections=2, max_jobs=3,
          max_tables_per_job=5, max_rows_per_table=100_000, runs_per_month=30, quality_rules=False,
          api_access=False, self_hosted_agent=False, sso=False, support="Community",
          tagline="Try it on small datasets"),
    _plan(id="trial", name="Trial (14 days)", price_month_usd=0, max_users=5, max_connections=10, max_jobs=50,
          max_tables_per_job=100, max_rows_per_table=5_000_000, runs_per_month=500, quality_rules=True,
          api_access=True, self_hosted_agent=False, sso=False, support="Email",
          tagline="Every Team feature, free for 14 days"),
    _plan(id="team", name="Team", price_month_usd=499, max_users=5, max_connections=10, max_jobs=50,
          max_tables_per_job=100, max_rows_per_table=5_000_000, runs_per_month=1_000, quality_rules=True,
          api_access=True, self_hosted_agent=False, sso=False, support="Email",
          tagline="For migration squads"),
    _plan(id="business", name="Business", price_month_usd=1_999, max_users=25, max_connections=None, max_jobs=None,
          max_tables_per_job=1_000, max_rows_per_table=100_000_000, runs_per_month=None, quality_rules=True,
          api_access=True, self_hosted_agent=True, sso=True, support="Priority",
          tagline="For programmes and system integrators"),
    _plan(id="enterprise", name="Enterprise", price_month_usd=None, max_users=None, max_connections=None,
          max_jobs=None, max_tables_per_job=None, max_rows_per_table=None, runs_per_month=None, quality_rules=True,
          api_access=True, self_hosted_agent=True, sso=True, support="Dedicated + SLA",
          tagline="On-prem, audit and custom connectors"),
]}
# fmt: on

PUBLIC_PLANS = ["free", "team", "business", "enterprise"]


def get_plan(plan_id: str) -> Plan:
    return PLANS.get(plan_id) or PLANS["free"]


def plan_dict(plan: Plan) -> dict:
    return asdict(plan)


def enforce(limit: int | None, current: int, what: str) -> None:
    """Raise 402 when adding one more `what` would exceed the plan limit."""
    if limit is not None and current >= limit:
        raise HTTPException(status_code=402, detail=f"Your plan allows {limit} {what}. Upgrade to add more.")
