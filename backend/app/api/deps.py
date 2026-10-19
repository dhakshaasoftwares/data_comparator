from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from ..db import Organization, User, get_db
from ..plans import Plan, get_plan
from ..security import decode_token

_bearer = HTTPBearer(auto_error=False)
ROLE_RANK = {"viewer": 0, "editor": 1, "admin": 2}


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(_bearer), db: Session = Depends(get_db)) -> User:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        payload = decode_token(creds.credentials)
        user = db.get(User, int(payload["sub"]))
    except Exception:
        user = None
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired session")
    return user


def require(role: str):
    def dep(user: User = Depends(current_user)) -> User:
        if ROLE_RANK.get(user.role, 0) < ROLE_RANK[role]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires {role} role")
        return user

    return dep


def org_plan(db: Session, user: User) -> tuple[Organization, Plan]:
    org = db.get(Organization, user.organization_id)
    return org, get_plan(org.plan)
