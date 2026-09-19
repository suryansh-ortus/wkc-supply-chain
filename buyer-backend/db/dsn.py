"""
Connection settings that survive passwords with special characters.

Supabase generates passwords containing @ # % / ? : & — all of which are
reserved characters in a URI. Pasting one raw into SUPABASE_DB_URL produces
either a parse error or a connection to the wrong host.

Two ways to configure, in priority order:

  1. Discrete parts (RECOMMENDED — no encoding, paste the password as-is):

        SUPABASE_DB_HOST=db.abcdefgh.supabase.co
        SUPABASE_DB_PORT=5432
        SUPABASE_DB_USER=postgres
        SUPABASE_DB_PASSWORD=p@ss/w#rd:123
        SUPABASE_DB_NAME=postgres

  2. A single URL:

        SUPABASE_DB_URL=postgresql+asyncpg://postgres:p%40ss@db.x.supabase.co:5432/postgres

     If the password is not percent-encoded, it is encoded automatically here,
     so a raw password in the URL still works.
"""

from __future__ import annotations

import os
import re
from urllib.parse import quote, unquote

# scheme://user:password@host[:port]/dbname[?query]
# `password` is greedy so it matches up to the LAST '@' — this is what allows
# an '@' inside the password itself.
_URL_RE = re.compile(
    r"^(?P<scheme>[a-z+]+)://"
    r"(?P<user>[^:/@]+)"
    r":(?P<password>.*)"
    r"@(?P<host>[^@/:]+)"
    r"(?::(?P<port>\d+))?"
    r"/(?P<database>[^?]*)"
    r"(?:\?(?P<query>.*))?$",
    re.IGNORECASE,
)


def _ssl_setting() -> str:
    """Supabase requires TLS; allow an override for local Postgres."""
    return os.getenv("DB_SSL", "require")


def get_conn_kwargs() -> dict:
    """
    Keyword arguments for `asyncpg.connect(**kwargs)`.

    Passing the password as a separate argument avoids URI parsing entirely,
    so no escaping is ever needed.
    """
    host = os.getenv("SUPABASE_DB_HOST")
    if host:
        return {
            "host": host,
            "port": int(os.getenv("SUPABASE_DB_PORT", "5432")),
            "user": os.getenv("SUPABASE_DB_USER", "postgres"),
            "password": os.getenv("SUPABASE_DB_PASSWORD", ""),
            "database": os.getenv("SUPABASE_DB_NAME", "postgres"),
            "ssl": _ssl_setting(), "statement_cache_size": 0,
        }

    url = os.getenv("SUPABASE_DB_URL")
    if not url:
        raise RuntimeError(
            "No database configuration found. Set either SUPABASE_DB_HOST/"
            "SUPABASE_DB_PASSWORD/... or SUPABASE_DB_URL in backend/.env"
        )

    parts = _parse_url(url)
    return {
        "host": parts["host"],
        "port": int(parts["port"] or 5432),
        "user": parts["user"],
        "password": parts["password"],
        "database": parts["database"] or "postgres",
        "ssl": _ssl_setting(), "statement_cache_size": 0,
    }


def get_sqlalchemy_url() -> str:
    """
    A properly escaped `postgresql+asyncpg://...` URL for SQLAlchemy
    (used from step 3 onward). Reserved characters are percent-encoded.
    """
    k = get_conn_kwargs()
    user = quote(str(k["user"]), safe="")
    password = quote(str(k["password"]), safe="")
    return (
        f"postgresql+asyncpg://{user}:{password}"
        f"@{k['host']}:{k['port']}/{k['database']}"
    )


def describe() -> str:
    """Connection summary with the password fully masked, for logs."""
    k = get_conn_kwargs()
    return (f"{k['user']}:{'*' * 8}@{k['host']}:{k['port']}"
            f"/{k['database']} (ssl={k['ssl']})")


def _parse_url(url: str) -> dict:
    m = _URL_RE.match(url.strip())
    if not m:
        raise RuntimeError(
            "SUPABASE_DB_URL could not be parsed. Expected\n"
            "  postgresql://USER:PASSWORD@HOST:PORT/DBNAME\n"
            "If your password contains special characters, prefer the discrete "
            "SUPABASE_DB_HOST / SUPABASE_DB_PASSWORD variables instead."
        )
    d = m.groupdict()
    raw_pw = d["password"] or ""
    # Decode if it was already percent-encoded; leave a raw password untouched.
    # (unquote is a no-op on text containing no % escapes.)
    d["password"] = unquote(raw_pw)
    return d
