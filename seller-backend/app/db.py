"""One asyncpg pool for the whole app."""

import asyncpg

from app import config  # noqa: F401  (loads .env)
from app.dsn import get_conn_kwargs

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        kwargs = get_conn_kwargs()
        # statement_cache_size=0 is required behind Supabase's transaction pooler
        _pool = await asyncpg.create_pool(
            min_size=1, max_size=5,
            **kwargs,
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def connect() -> asyncpg.Connection:
    """Single connection, for scripts."""
    return await asyncpg.connect(**get_conn_kwargs())
