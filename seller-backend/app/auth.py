"""Seller login only. Buyers cannot authenticate against this service."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import HTTPException, Header

from app import config

ALGO = "HS256"


def make_token(seller_id: str) -> str:
    return jwt.encode(
        {"role": "seller", "uid": seller_id, "seller_id": seller_id,
         "exp": datetime.now(timezone.utc) + timedelta(days=30)},
        config.JWT_SECRET, algorithm=ALGO)


def read_token(token: str) -> dict:
    try:
        return jwt.decode(token, config.JWT_SECRET, algorithms=[ALGO])
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid or expired token")


async def login(pool, username: str, password: str) -> dict:
    row = await pool.fetchrow("""
        SELECT s.seller_id, s.email, s.password_hash, se.company_name, se.location
        FROM seller_users s JOIN sellers se USING (seller_id)
        WHERE s.seller_id = $1 OR lower(s.email) = lower($1)
    """, username)
    if not row:
        raise HTTPException(401, "No such seller")
    try:
        ok = bcrypt.checkpw(password.encode(), row["password_hash"].encode())
    except ValueError:
        ok = False
    if not ok:
        raise HTTPException(401, "Wrong password")

    await pool.execute(
        "UPDATE seller_users SET last_login_at = now() WHERE seller_id = $1",
        row["seller_id"])
    return {"token": make_token(row["seller_id"]), "role": "seller",
            "seller_id": row["seller_id"], "company_name": row["company_name"],
            "email": row["email"], "location": row["location"]}


async def current_seller(authorization: str = Header(default="")) -> dict:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing token")
    user = read_token(authorization[7:])
    if user.get("role") != "seller":
        raise HTTPException(403, "Sellers only")
    return user


def check_internal(secret: str = Header(default="", alias="X-Internal-Secret")) -> bool:
    if secret != config.INTERNAL_SECRET:
        raise HTTPException(403, "Bad internal secret")
    return True
