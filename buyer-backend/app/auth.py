"""Buyer login only. Sellers cannot authenticate against this service."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Header

from app import config

ALGO = "HS256"


def make_token(email: str) -> str:
    return jwt.encode(
        {"role": "buyer", "uid": email,
         "exp": datetime.now(timezone.utc) + timedelta(days=30)},
        config.JWT_SECRET, algorithm=ALGO)


def read_token(token: str) -> dict:
    try:
        return jwt.decode(token, config.JWT_SECRET, algorithms=[ALGO])
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid or expired token")


async def login(pool, username: str, password: str) -> dict:
    row = await pool.fetchrow("""
        SELECT user_id, email, display_name, password_hash
        FROM buyer_users WHERE lower(email) = lower($1)
    """, username)
    if not row:
        raise HTTPException(401, "No such buyer")
    try:
        ok = bcrypt.checkpw(password.encode(), row["password_hash"].encode())
    except ValueError:
        ok = False
    if not ok:
        raise HTTPException(401, "Wrong password")

    await pool.execute("UPDATE buyer_users SET last_login_at = now() WHERE user_id = $1",
                       row["user_id"])
    return {"token": make_token(row["email"]), "role": "buyer",
            "email": row["email"], "display_name": row["display_name"]}


async def current_buyer(authorization: str = Header(default="")) -> dict:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing token")
    user = read_token(authorization[7:])
    if user.get("role") != "buyer":
        raise HTTPException(403, "Buyers only")
    return user


def check_internal(secret: str = Header(default="", alias="X-Internal-Secret")) -> bool:
    """Guard for the endpoints the seller service calls."""
    if secret != config.INTERNAL_SECRET:
        raise HTTPException(403, "Bad internal secret")
    return True
