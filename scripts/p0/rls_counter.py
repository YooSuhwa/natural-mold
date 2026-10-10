#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["anyio>=4,<5", "asyncpg>=0.29,<1", "sqlalchemy[asyncio]>=2,<3"]
# ///

# ─── How to run ───
# 1. Install uv: curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Set RLS_DATABASE_URL to a seeded disposable PostgreSQL database.
# 3. Run: uv run scripts/p0/rls_counter.py
# ──────────────────

from __future__ import annotations

import os
from typing import Final

import anyio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

TA: Final = "aaaaaaaa-0000-0000-0000-000000000001"


async def counterexamples(engine: AsyncEngine) -> int:
    # A: nullif 없이 캐스팅하면 재사용 연결에서 오류
    invalid_uuid = False
    async with engine.connect() as connection:
        await connection.execute(text("select set_config('app.tenant_id', :t, true)"), {"t": TA})
        await connection.commit()
        try:
            await connection.execute(text("select current_setting('app.tenant_id', true)::uuid"))
            print("FAIL A no error")
        except DBAPIError as error:
            invalid_uuid = "invalid input syntax for type uuid" in str(error)
            print(
                "PASS" if invalid_uuid else "FAIL",
                "A nullif 없이 ''::uuid ->",
                type(error.__cause__ or error).__name__,
                str(error).splitlines()[0][:90],
            )
            await connection.rollback()
    # B: is_local=false 로 설정하면 풀의 다음 사용자에게 값이 남음
    async with engine.connect() as connection:
        await connection.execute(text("select set_config('app.tenant_id', :t, false)"), {"t": TA})
        await connection.commit()
    async with engine.connect() as connection:
        try:
            count = (await connection.execute(text("select count(*) from agents"))).scalar()
            leaked = count is not None and count > 0
            print(
                "PASS" if leaked else "FAIL",
                "B is_local=false 후 다음 연결 사용자가 보는 행 수:",
                count,
            )
        finally:
            with anyio.CancelScope(shield=True):
                await connection.rollback()
                await connection.execute(text("reset app.tenant_id"))
                await connection.commit()
    passed = int(invalid_uuid) + int(leaked)
    print("SUMMARY", passed, "/", 2)
    return 0 if passed == 2 else 1


async def main() -> int:
    engine = create_async_engine(os.environ["RLS_DATABASE_URL"], pool_size=1, max_overflow=0)
    try:
        return await counterexamples(engine)
    finally:
        with anyio.CancelScope(shield=True):
            await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(anyio.run(main))
