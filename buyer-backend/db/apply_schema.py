"""
Apply schema.sql to the Supabase database.

Convenience wrapper so you don't need psql installed:

    python backend/db/apply_schema.py

Connection settings come from backend/.env — see db/dsn.py for the two
supported forms (discrete parts, or a single URL).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from db.dsn import describe, get_conn_kwargs  # noqa: E402

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


async def main() -> int:
    try:
        kwargs = get_conn_kwargs()
    except RuntimeError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 1

    print(f"Connecting to {describe()}")
    try:
        conn = await asyncpg.connect(**kwargs)
    except Exception as exc:
        print(f"\nConnection failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(
            "\nCommon causes:\n"
            "  * Wrong password — copy it from Supabase > Project Settings > Database\n"
            "  * Direct connection blocked on your network — use the Session pooler\n"
            "    host (aws-0-<region>.pooler.supabase.com, port 5432, user\n"
            "    postgres.<project-ref>)\n"
            "  * Special characters in the password — use SUPABASE_DB_HOST /\n"
            "    SUPABASE_DB_PASSWORD instead of SUPABASE_DB_URL",
            file=sys.stderr,
        )
        return 1

    try:
        print(f"Applying {SCHEMA_PATH.name} ...")
        await conn.execute(SCHEMA_PATH.read_text())
        tables = await conn.fetchval(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'"
        )
    finally:
        await conn.close()

    print(f"Schema applied — {tables} tables/views in public.")
    print("Next:  python seeds/seed.py --reset")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
