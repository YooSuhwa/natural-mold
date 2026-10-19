#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["anyio>=4,<5", "asyncpg>=0.29,<1", "sqlalchemy[asyncio]>=2,<3"]
# ///

# ─── How to run ───
# 1. Install uv: curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Set RLS_DATABASE_URL to a freshly seeded disposable PostgreSQL database.
# 3. Run: uv run scripts/p0/rls_proto.py
# ──────────────────

from __future__ import annotations

import os
import time
from types import TracebackType
from typing import Final

import anyio
from sqlalchemy import String, column, event, select, table, text, update
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, SessionTransaction

TA: Final = "aaaaaaaa-0000-0000-0000-000000000001"
TB: Final = "bbbbbbbb-0000-0000-0000-000000000002"


class ShieldedAsyncSession(AsyncSession):  # app/database.py:10 와 같은 형태
    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        with anyio.CancelScope(shield=True):
            await self.close()


@event.listens_for(Session, "after_begin")
def _apply_scope(
    session: Session, _transaction: SessionTransaction, connection: Connection
) -> None:
    t = session.info.get("tenant_id")
    s = session.info.get("scope")
    if t is None and s is None:
        return
    connection.execute(
        text("SELECT set_config('app.tenant_id', :t, true), set_config('app.scope', :s, true)"),
        {"t": t or "", "s": s or ""},
    )


def tenant_session(
    factory: async_sessionmaker[ShieldedAsyncSession], tid: str
) -> ShieldedAsyncSession:
    session = factory()
    session.info["tenant_id"] = tid
    return session


def platform_session(
    factory: async_sessionmaker[ShieldedAsyncSession], reason: str
) -> ShieldedAsyncSession:
    session = factory()
    session.info["scope"] = "platform"
    session.info["reason"] = reason
    return session


async def names(db: AsyncSession) -> list[str]:
    query = select(column("name", String)).select_from(table("agents"))
    return sorted((await db.scalars(query)).all())


async def run_scenarios(
    engine: AsyncEngine,
    factory: async_sessionmaker[ShieldedAsyncSession],
    f2: async_sessionmaker[ShieldedAsyncSession],
) -> int:
    results: list[tuple[str, bool, str]] = []

    def rec(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, detail))
        print(("PASS" if ok else "FAIL"), name, detail)

    # 1 컨텍스트 없는 세션
    async with factory() as db:
        n = await names(db)
        rec("1 컨텍스트 없음 조회 0행", n == [], str(n))
        try:
            await db.execute(text("insert into agents(tenant_id,name) values (:t,'x')"), {"t": TA})
            await db.commit()
            rec("1b 컨텍스트 없음 쓰기 거부", False, "insert succeeded")
        except DBAPIError as e:
            await db.rollback()
            rec("1b 컨텍스트 없음 쓰기 거부", "row-level security" in str(e), type(e.orig).__name__)
    # 2 회사 A
    async with tenant_session(factory, TA) as db:
        n = await names(db)
        rec("2 회사 A는 A 행만", n == ["a1", "a2"], str(n))
        # 3 커밋 후 두 번째 트랜잭션
        await db.commit()
        n = await names(db)
        rec("3 커밋 후 두 번째 트랜잭션도 적용", n == ["a1", "a2"], str(n))
        await db.execute(text("insert into agents(tenant_id,name) values (:t,'a3')"), {"t": TA})
        await db.commit()
        n = await names(db)
        rec("3b 세 번째 트랜잭션 쓰기·조회", n == ["a1", "a2", "a3"], str(n))
        try:
            await db.execute(
                text("insert into agents(tenant_id,name) values (:t,'evil')"), {"t": TB}
            )
            await db.commit()
            rec("4 다른 회사 행 쓰기 거부(WITH CHECK)", False, "insert succeeded")
        except DBAPIError as e:
            await db.rollback()
            rec(
                "4 다른 회사 행 쓰기 거부(WITH CHECK)",
                "row-level security" in str(e),
                type(e.orig).__name__,
            )
        # 5 롤백 후
        n = await names(db)
        rec("5 롤백 후 새 트랜잭션도 적용", n == ["a1", "a2", "a3"], str(n))
        # 6 savepoint
        async with db.begin_nested():
            n = await names(db)
        rec("6 begin_nested 안에서도 적용", n == ["a1", "a2", "a3"], str(n))
        await db.commit()
        agents = table("agents", column("name", String))
        upd = await db.execute(
            update(agents)
            .values(name=agents.c.name + "!")
            .where(agents.c.name == "b1")
            .returning(agents.c.name)
        )
        await db.commit()
        updated_rows = len(upd.all())
        rec("7 다른 회사 행 수정 0건", updated_rows == 0, f"rowcount={updated_rows}")
    # 8 같은 연결 재사용(풀 1개): 이전 회사 값이 남지 않음
    async with factory() as db:
        v = (await db.execute(text("select current_setting('app.tenant_id', true)"))).scalar()
        n = await names(db)
        rec(
            "8 연결 재사용 시 이전 값 미잔류",
            (v in (None, "")) and n == [],
            f"setting={v!r} rows={n}",
        )
    async with tenant_session(factory, TB) as db:
        n = await names(db)
        rec("8b 같은 연결, 회사 B", n == ["b1"], str(n))
    # 9 플랫폼 범위
    async with platform_session(factory, "catalog_refresh") as db:
        n = await names(db)
        rec("9 플랫폼 범위 전체 조회", n == ["a1", "a2", "a3", "b1"], str(n))
    # 10 engine.connect() 직접 사용(advisory lock 경로): 닫힌 쪽으로 실패
    async with engine.connect() as conn:
        n = sorted(
            (await conn.scalars(select(column("name", String)).select_from(table("agents")))).all()
        )
        rec("10 engine.connect() 직접 조회 0행", n == [], str(n))
    # 11 동시 요청: 서로 다른 회사 세션이 섞이지 않음
    bads = [0] * 8

    async def worker(tid: str, expect: list[str], k: int, index: int) -> None:
        bad = 0
        for _ in range(k):
            s = f2()
            s.info["tenant_id"] = tid
            async with s as db:
                if await names(db) != expect:
                    bad += 1
                await db.commit()
                if await names(db) != expect:
                    bad += 1
        bads[index] = bad

    async with anyio.create_task_group() as tasks:
        for i in range(8):
            tasks.start_soon(
                worker,
                TA if i % 2 == 0 else TB,
                ["a1", "a2", "a3"] if i % 2 == 0 else ["b1"],
                100,
                i,
            )
    rec("11 동시 8개 작업 x 100회 교차 오염 0건", sum(bads) == 0, f"bad={sum(bads)}")

    # 12 오버헤드: 트랜잭션당 set_config 1회 추가
    async def bench(scoped: bool, k: int = 1000) -> float:
        t0 = time.perf_counter()
        for _ in range(k):
            s = f2()
            if scoped:
                s.info["tenant_id"] = TA
            else:
                s.info["scope"] = None
            async with s as db:
                await db.execute(text("select 1"))
                await db.commit()
        return (time.perf_counter() - t0) / k * 1000

    await bench(True, 100)
    base = await bench(False)
    scoped = await bench(True)
    rec(
        "12 트랜잭션당 추가 지연",
        True,
        f"없음 {base:.3f}ms, 적용 {scoped:.3f}ms, 차이 {scoped - base:.3f}ms",
    )
    passed = sum(1 for result in results if result[1])
    print("SUMMARY", passed, "/", len(results))
    return 0 if passed == len(results) == 15 else 1


async def main() -> int:
    url = os.environ["RLS_DATABASE_URL"]
    engine = create_async_engine(url, pool_size=1, max_overflow=0)
    try:
        parallel_engine = create_async_engine(url, pool_size=4, max_overflow=0)
        try:
            factory = async_sessionmaker(
                engine, class_=ShieldedAsyncSession, expire_on_commit=False
            )
            parallel_factory = async_sessionmaker(
                parallel_engine, class_=ShieldedAsyncSession, expire_on_commit=False
            )
            return await run_scenarios(engine, factory, parallel_factory)
        finally:
            with anyio.CancelScope(shield=True):
                await parallel_engine.dispose()
    finally:
        with anyio.CancelScope(shield=True):
            await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(anyio.run(main))
