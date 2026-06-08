"""Agent login, sessions, and role-based permissions (Phase 6).

Design — layered on top of the existing HTTP Basic gate (which stays as the
outer perimeter so the whole app remains private and no legacy route changes):

  • The **owner** reaches the app via HTTP Basic and, when no agent is logged
    in, is treated as an admin (preserves the existing single-operator flow).
  • **Teammates** log in with their agent account (email + password an admin
    set). Their role (admin|agent) then governs admin-only actions, and inbox
    activity is attributed to them.

Sessions are a signed, expiring cookie (HMAC over agent id + expiry) — no DB
session table, no extra dependency. Passwords are PBKDF2-HMAC-SHA256.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import Agent

COOKIE_NAME = "schild_agent"
_PBKDF2_ROUNDS = 200_000


# ── Password hashing ─────────────────────────────────────────────────────────


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt_hex, hash_hex = stored.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


# ── Signed session cookie ────────────────────────────────────────────────────


def _sign(value: str) -> str:
    sig = hmac.new(settings.session_secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()
    return sig


def make_session_token(agent_id: int) -> str:
    expiry = int(time.time()) + settings.session_ttl_hours * 3600
    payload = f"{agent_id}.{expiry}"
    token = f"{payload}.{_sign(payload)}"
    return base64.urlsafe_b64encode(token.encode("utf-8")).decode("utf-8")


def parse_session_token(token: str) -> int | None:
    try:
        decoded = base64.urlsafe_b64decode(token.encode("utf-8")).decode("utf-8")
        agent_id_str, expiry_str, sig = decoded.rsplit(".", 2)
        payload = f"{agent_id_str}.{expiry_str}"
        if not hmac.compare_digest(sig, _sign(payload)):
            return None
        if int(expiry_str) < int(time.time()):
            return None
        return int(agent_id_str)
    except Exception:
        return None


# ── Current agent / role helpers ─────────────────────────────────────────────


def current_agent(request: Request, db: Session) -> Agent | None:
    token = request.cookies.get(COOKIE_NAME, "")
    if not token:
        return None
    agent_id = parse_session_token(token)
    if agent_id is None:
        return None
    agent = db.get(Agent, agent_id)
    if agent and agent.is_active:
        return agent
    return None


def is_admin(request: Request, db: Session) -> bool:
    """Owner (no agent session, reached via HTTP Basic) is admin. A logged-in
    agent is admin only if their role is 'admin'.
    """
    agent = current_agent(request, db)
    if agent is None:
        return True  # owner via HTTP Basic
    return agent.role == "admin"


def actor_label(request: Request, db: Session) -> str:
    agent = current_agent(request, db)
    return agent.name if agent else "owner"


def authenticate(db: Session, email: str, password: str) -> Agent | None:
    agent = db.scalar(select(Agent).where(Agent.email == email.strip().lower(), Agent.is_active.is_(True)))
    if agent and agent.password_hash and verify_password(password, agent.password_hash):
        return agent
    return None


def require_admin_role(request: Request, db: Session = Depends(get_db)) -> bool:
    """FastAPI dependency that blocks non-admin agents from admin-only actions."""
    if not is_admin(request, db):
        raise HTTPException(status_code=403, detail="Admin role required for this action.")
    return True
