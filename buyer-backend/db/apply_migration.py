"""
Apply a single .sql file.

    python db/apply_migration.py db/migration_02_holds.sql
"""

import asyncio
import sys
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from db.dsn import describe, get_conn_kwargs  # noqa: E402


async def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python db/apply_migration.py <file.sql>", file=sys.stderr)
        return 1

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"not found: {path}", file=sys.stderr)
        return 1

    print(f"Connecting to {describe()}")
    conn = await asyncpg.connect(**get_conn_kwargs())
    try:
        await conn.execute(path.read_text())
    finally:
        await conn.close()
    print(f"Applied {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
