"""The live historical integer ledger must retain fractional audio minutes."""

import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from app.db.models import AppUser


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="requires disposable test database")
async def test_integer_ledger_upgrade_preserves_legacy_and_fractional_minutes():
    url = make_url(os.environ["TEST_DATABASE_URL"])
    assert "test" in (url.database or "").lower()
    engine = create_async_engine(str(url))
    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = AppUser(telegram_id=730980000)
        session.add(user)
        await session.commit()
    await engine.dispose()

    async def connect():
        return await asyncpg.connect(
            user=url.username, password=url.password, database=url.database,
            host=url.host, port=url.port,
        )

    conn = await connect()
    try:
        tier_id = await conn.fetchval("SELECT id FROM subscription_tier WHERE name='advanced'")
        # The metadata fixture creates the ORM's float type. Recreate the
        # historical physical integer type before running the real migration.
        await conn.execute(
            "ALTER TABLE request_ledger ALTER COLUMN cost TYPE integer USING cost::integer"
        )
        await conn.execute("""
            INSERT INTO request_ledger
              (id, user_id, tier_id, request_id, model_name, feature, cost, state, created_at)
            VALUES ($1,$2,$3,$4,'legacy-model','text',2,'consumed',$5)
        """, uuid.uuid4(), user.id, tier_id, "legacy-" + uuid.uuid4().hex,
             datetime.now(UTC).replace(tzinfo=None))
    finally:
        await conn.close()

    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["DATABASE_URL"] = env["TEST_DATABASE_URL"]

    def alembic(*args):
        return subprocess.run(
            [sys.executable, "-m", "alembic", *args], cwd=root, env=env,
            capture_output=True, text=True, timeout=40,
        )

    stamped = alembic("stamp", "xw0e1f2a3b54")
    assert stamped.returncode == 0, stamped.stderr
    upgraded = alembic("upgrade", "xw0e1f2a3b55")
    assert upgraded.returncode == 0, upgraded.stderr

    conn = await connect()
    try:
        column_type = await conn.fetchval("""
            SELECT data_type FROM information_schema.columns
            WHERE table_name='request_ledger' AND column_name='cost'
        """)
        legacy_cost = await conn.fetchval(
            "SELECT cost FROM request_ledger WHERE feature='text'"
        )
        assert column_type == "double precision"
        assert legacy_cost == 2.0
        request_id = "audio-" + uuid.uuid4().hex
        await conn.execute("""
            INSERT INTO request_ledger
              (id, user_id, tier_id, request_id, model_name, feature, cost, state, created_at)
            VALUES ($1,$2,$3,$4,'gpt-transcribe','transcription',$5,'reserved',$6)
        """, uuid.uuid4(), user.id, tier_id, request_id, 0.2,
             datetime.now(UTC).replace(tzinfo=None))
        reserved = await conn.fetchval("""
            SELECT sum(cost) FROM request_ledger
            WHERE user_id=$1 AND tier_id=$2 AND feature='transcription'
              AND state IN ('reserved','consumed')
        """, user.id, tier_id)
        assert reserved == pytest.approx(0.2)
        settled_minutes = 9.704313 / 60
        await conn.execute("""
            UPDATE request_ledger SET cost=$1, state='consumed' WHERE request_id=$2
        """, settled_minutes, request_id)
        used = await conn.fetchval("""
            SELECT sum(cost) FROM request_ledger
            WHERE user_id=$1 AND tier_id=$2 AND feature='transcription'
              AND state IN ('reserved','consumed')
        """, user.id, tier_id)
        assert used == pytest.approx(settled_minutes)
    finally:
        await conn.close()

    refused = alembic("downgrade", "xw0e1f2a3b54")
    assert refused.returncode != 0
    assert "fractional or out-of-range" in refused.stderr
    conn = await connect()
    try:
        await conn.execute("DELETE FROM request_ledger WHERE feature='transcription'")
    finally:
        await conn.close()
    downgraded = alembic("downgrade", "xw0e1f2a3b54")
    assert downgraded.returncode == 0, downgraded.stderr
    conn = await connect()
    try:
        integer_type = await conn.fetchval("""
            SELECT data_type FROM information_schema.columns
            WHERE table_name='request_ledger' AND column_name='cost'
        """)
        assert integer_type == "integer"
        assert await conn.fetchval("SELECT cost FROM request_ledger WHERE feature='text'") == 2
    finally:
        await conn.close()
