from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import Organization, User, get_db
from ..plans import enforce, get_plan
from ..security import create_token, hash_password, verify_password
from ..settings import get_settings
from .deps import current_user, require

router = APIRouter(prefix="/api/auth", tags=["auth"])
users_router = APIRouter(prefix="/api/users", tags=["users"])


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    name: str = ""
    organization: str = Field(min_length=1, max_length=200)


class LoginIn(BaseModel):
    email: str
    password: str


class UserIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    name: str = ""
    role: str = Field("editor", pattern="^(admin|editor|viewer)$")


def user_out(u: User) -> dict:
    org = u.organization
    return {
        "id": u.id,
        "email": u.email,
        "name": u.name,
        "role": u.role,
        "organization": {
            "id": org.id,
            "name": org.name,
            "plan": org.plan,
            "subscription_status": org.subscription_status,
        },
    }


def _session(u: User) -> dict:
    return {"token": create_token(u.id, u.organization_id), "user": user_out(u)}


@router.post("/register")
def register(body: RegisterIn, db: Session = Depends(get_db)):
    email = body.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "An account with this email already exists")
    org = Organization(name=body.organization, plan=get_settings().default_plan)
    db.add(org)
    db.flush()
    user = User(
        email=email, name=body.name, password_hash=hash_password(body.password), role="admin", organization_id=org.id
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return _session(user)


@router.post("/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if user is None or not verify_password(body.password, user.password_hash) or not user.is_active:
        raise HTTPException(401, "Incorrect email or password")
    return _session(user)


@router.get("/me")
def me(user: User = Depends(current_user)):
    return user_out(user)


@users_router.get("")
def list_users(user: User = Depends(current_user), db: Session = Depends(get_db)):
    users = db.scalars(select(User).where(User.organization_id == user.organization_id).order_by(User.id))
    return [{"id": u.id, "email": u.email, "name": u.name, "role": u.role, "is_active": u.is_active} for u in users]


@users_router.post("")
def add_user(body: UserIn, user: User = Depends(require("admin")), db: Session = Depends(get_db)):
    plan = get_plan(user.organization.plan)
    count = db.scalar(
        select(func.count())
        .select_from(User)
        .where(User.organization_id == user.organization_id, User.is_active.is_(True))
    )
    enforce(plan.max_users, count or 0, "users")
    if db.scalar(select(User).where(User.email == body.email.lower())):
        raise HTTPException(409, "An account with this email already exists")
    new = User(
        email=body.email.lower(),
        name=body.name,
        password_hash=hash_password(body.password),
        role=body.role,
        organization_id=user.organization_id,
    )
    db.add(new)
    db.commit()
    return {"id": new.id, "email": new.email, "name": new.name, "role": new.role, "is_active": True}


@users_router.delete("/{user_id}")
def remove_user(user_id: int, user: User = Depends(require("admin")), db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    if target is None or target.organization_id != user.organization_id:
        raise HTTPException(404, "User not found")
    if target.id == user.id:
        raise HTTPException(400, "You cannot remove yourself")
    target.is_active = False
    db.commit()
    return {"ok": True}
