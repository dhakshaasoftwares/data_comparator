"""Subscription plans, usage and Stripe Checkout / webhooks."""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import Connection, Job, Organization, UsageRecord, User, get_db
from ..plans import PLANS, PUBLIC_PLANS, get_plan, plan_dict
from ..settings import get_settings
from .deps import current_user, require

router = APIRouter(prefix="/api/billing", tags=["billing"])
STRIPE_API = "https://api.stripe.com/v1"


class PlanIn(BaseModel):
    plan: str


def _stripe_ready() -> bool:
    s = get_settings()
    return s.billing_enabled and bool(s.stripe_secret_key)


@router.get("/plans")
def public_plans():
    """Public pricing table (also usable from the marketing website)."""
    return [plan_dict(PLANS[p]) for p in PUBLIC_PLANS]


@router.get("")
def billing(user: User = Depends(current_user), db: Session = Depends(get_db)):
    org = db.get(Organization, user.organization_id)
    plan = get_plan(org.plan)
    period = dt.date.today().strftime("%Y-%m")
    usage = db.scalar(select(UsageRecord).where(UsageRecord.organization_id == org.id, UsageRecord.period == period))
    oid = org.id
    return {
        "plan": plan_dict(plan),
        "subscription_status": org.subscription_status,
        "billing_enabled": get_settings().billing_enabled,
        "checkout_available": _stripe_ready(),
        "plans": [plan_dict(PLANS[p]) for p in PUBLIC_PLANS],
        "usage": {
            "period": period,
            "runs": usage.runs if usage else 0,
            "rows_compared": usage.rows_compared if usage else 0,
            "connections": db.scalar(
                select(func.count()).select_from(Connection).where(Connection.organization_id == oid)
            ),
            "jobs": db.scalar(select(func.count()).select_from(Job).where(Job.organization_id == oid)),
            "users": db.scalar(
                select(func.count()).select_from(User).where(User.organization_id == oid, User.is_active.is_(True))
            ),
        },
    }


@router.post("/plan")
def change_plan(body: PlanIn, user: User = Depends(require("admin")), db: Session = Depends(get_db)):
    """Evaluation mode only: switch plans without payment."""
    if get_settings().billing_enabled:
        raise HTTPException(400, "Billing is enabled; use checkout to change plans")
    if body.plan not in PLANS:
        raise HTTPException(404, "Unknown plan")
    org = db.get(Organization, user.organization_id)
    org.plan, org.subscription_status = body.plan, "active"
    db.commit()
    return {"plan": plan_dict(get_plan(org.plan))}


@router.post("/checkout")
def checkout(body: PlanIn, user: User = Depends(require("admin")), db: Session = Depends(get_db)):
    """Create a Stripe Checkout session for a subscription and return its URL."""
    s = get_settings()
    price = s.stripe_prices.get(body.plan)
    if not _stripe_ready() or not price:
        raise HTTPException(501, "Online checkout is not configured. Contact sales to subscribe.")
    org = db.get(Organization, user.organization_id)
    data = {
        "mode": "subscription",
        "line_items[0][price]": price,
        "line_items[0][quantity]": "1",
        "success_url": f"{s.app_url}/billing?checkout=success",
        "cancel_url": f"{s.app_url}/billing?checkout=cancelled",
        "client_reference_id": str(org.id),
        "metadata[organization_id]": str(org.id),
        "metadata[plan]": body.plan,
        "subscription_data[metadata][organization_id]": str(org.id),
        "subscription_data[metadata][plan]": body.plan,
    }
    if org.billing_customer_id:
        data["customer"] = org.billing_customer_id
    else:
        data["customer_email"] = user.email
    resp = httpx.post(f"{STRIPE_API}/checkout/sessions", data=data, auth=(s.stripe_secret_key, ""), timeout=20)
    if resp.status_code >= 400:
        raise HTTPException(502, resp.json().get("error", {}).get("message", "Stripe error"))
    return {"url": resp.json()["url"]}


def _verify_stripe(payload: bytes, header: str, secret: str, tolerance: int = 300) -> None:
    pairs = [p.strip().split("=", 1) for p in header.split(",") if "=" in p]
    ts = next((v for k, v in pairs if k == "t"), None)
    sig = [v for k, v in pairs if k == "v1"]
    if not ts or not sig:
        raise HTTPException(400, "Invalid signature header")
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, s) for s in sig) or abs(time.time() - int(ts)) > tolerance:
        raise HTTPException(400, "Invalid signature")


@router.post("/webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    s = get_settings()
    if not s.stripe_webhook_secret:
        raise HTTPException(501, "Webhook not configured")
    payload = await request.body()
    _verify_stripe(payload, request.headers.get("stripe-signature", ""), s.stripe_webhook_secret)
    event = json.loads(payload)
    obj = event.get("data", {}).get("object", {})
    meta = obj.get("metadata") or {}
    org_id = meta.get("organization_id") or obj.get("client_reference_id")
    org = db.get(Organization, int(org_id)) if org_id else None
    if org is None:
        return {"ignored": True}
    kind = event.get("type", "")
    if kind == "checkout.session.completed":
        org.plan = meta.get("plan", org.plan)
        org.subscription_status = "active"
        org.billing_customer_id = obj.get("customer") or org.billing_customer_id
    elif kind in ("customer.subscription.updated", "customer.subscription.created"):
        org.subscription_status = obj.get("status", org.subscription_status)
        if meta.get("plan") in PLANS:
            org.plan = meta["plan"]
    elif kind == "customer.subscription.deleted":
        org.plan, org.subscription_status = "free", "canceled"
    db.commit()
    return {"ok": True}
