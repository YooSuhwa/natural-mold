"""Plan 1.3 upgrade lane: a pre-org-authz (m77) database with an existing user's data.

The rows are written with plain SQL against the m77 schema, the way a user of the
last release before org-authz has them (no tenant/org columns exist yet). The live
app is then started as a real uvicorn process against the upgraded database.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.engine import URL, Engine, make_url

from alembic import command
from app.agent_runtime.identity import make_agent_runtime_name
from app.auth.password import hash_password
from app.security import cipher

BACKEND = Path(__file__).resolve().parents[2]
BASELINE_REVISION = "m77_side_chat_link"  # head of origin/main 9657ca60, before org-authz
PASSWORD = "legacy upgrade password 42"
API_KEY = "sk-legacy-upgrade-0000"
ENCRYPTION_KEY = "5a" * 32


@dataclass(frozen=True, slots=True)
class LegacyRows:
    user_id: UUID
    email: str
    agent_id: UUID
    credential_id: UUID
    conversation_id: UUID


@dataclass(frozen=True, slots=True)
class UpgradeLab:
    engine: Engine
    config: Config
    url: URL


@contextmanager
def upgrade_lab() -> Iterator[UpgradeLab]:
    """An owned database migrated to the pre-org-authz baseline only."""
    source = make_url(os.environ["INTEGRATION_DATABASE_URL"])
    database = "moldy_pg_lane_upgrade_" + uuid4().hex
    admin = sa.create_engine(source, isolation_level="AUTOCOMMIT")
    url = source.set(database=database)
    engine = sa.create_engine(url)
    created = False
    try:
        with admin.connect() as connection:
            connection.execute(sa.text(f'CREATE DATABASE "{database}"'))
            created = True
        config = Config(str(BACKEND / "alembic.ini"))
        config.set_main_option("script_location", str(BACKEND / "alembic"))
        config.attributes["database_url"] = url.set(
            drivername="postgresql+asyncpg"
        ).render_as_string(hide_password=False)
        command.upgrade(config, BASELINE_REVISION)
        yield UpgradeLab(engine, config, url)
    finally:
        engine.dispose()
        if created:
            with admin.connect() as connection:
                connection.execute(sa.text(f'DROP DATABASE "{database}" WITH (FORCE)'))
        admin.dispose()


def seed_legacy_rows(engine: Engine) -> LegacyRows:
    """One user with a model, an agent, an LLM credential and a conversation (m77 shape)."""
    now = datetime.now(UTC).replace(tzinfo=None)
    rows = LegacyRows(uuid4(), "legacy.user@moldy.dev", uuid4(), uuid4(), uuid4())
    model_id = uuid4()
    key = cipher.CipherKey.from_hex(ENCRYPTION_KEY)
    blob = cipher.encrypt(json.dumps({"api_key": API_KEY}), key)
    statements = (
        (
            "INSERT INTO users (id, email, name, created_at, hashed_password) "
            "VALUES (:id, :email, 'Legacy User', :now, :hashed)",
            {
                "id": rows.user_id,
                "email": rows.email,
                "now": now,
                "hashed": hash_password(PASSWORD),
            },
        ),
        (
            "INSERT INTO models (id, provider, model_name, display_name, is_visible, "
            "supports_function_calling, input_modalities, output_modalities, source) VALUES "
            "(:id, 'e2e_scripted', 'document-artifact-scripted', 'Legacy scripted model', true, "
            "true, '[\"text\"]', '[\"text\"]', 'manual')",
            {"id": model_id},
        ),
        (
            "INSERT INTO credentials (id, user_id, definition_key, name, data_encrypted, key_id, "
            "field_keys) VALUES (:id, :user, 'openai', 'My OpenAI key', :blob, :key_id, "
            "'[\"api_key\"]')",
            {"id": rows.credential_id, "user": rows.user_id, "blob": blob, "key_id": key.key_id},
        ),
        (
            "INSERT INTO agents (id, user_id, name, description, system_prompt, model_id, status, "
            "created_at, updated_at, runtime_name) VALUES (:id, :user, 'Legacy helper', "
            "'Made before org-authz', 'You help.', :model, 'active', :now, :now, :runtime)",
            {
                "id": rows.agent_id,
                "user": rows.user_id,
                "model": model_id,
                "now": now,
                "runtime": make_agent_runtime_name(rows.agent_id),
            },
        ),
        (
            "INSERT INTO conversations (id, agent_id, title, created_at, updated_at, "
            "unread_count, last_activity_source) VALUES (:id, :agent, 'Before the upgrade', "
            ":now, :now, 0, 'user')",
            {"id": rows.conversation_id, "agent": rows.agent_id, "now": now},
        ),
    )
    with engine.begin() as connection:
        for statement, values in statements:
            connection.execute(sa.text(statement), values)
    return rows


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@contextmanager
def live_backend(url: URL, run_root: Path) -> Iterator[str]:
    """The current app as a real uvicorn process (scripted model on, .env off)."""
    for relative in ("data/skills", "data/conversations", "data/uploads", "data/artifacts"):
        (run_root / "backend" / relative).mkdir(parents=True, exist_ok=True)
    port = _free_port()
    rendered = url.render_as_string(hide_password=False)
    keep = {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "USER"}
    env = {name: value for name, value in os.environ.items() if name in keep}
    env.update(
        {
            "MOLDY_DISABLE_ENV_FILE": "true",
            "PYTHON_DOTENV_DISABLED": "1",
            "MOLDY_TEST_RUN_ROOT": str(run_root),
            "DATABASE_URL": make_url(rendered)
            .set(drivername="postgresql+asyncpg")
            .render_as_string(hide_password=False),
            "DATABASE_URL_SYNC": make_url(rendered)
            .set(drivername="postgresql")
            .render_as_string(hide_password=False),
            "ENCRYPTION_KEYS": ENCRYPTION_KEY,
            "JWT_SECRET": "legacy-upgrade-" + uuid4().hex,
            "E2E_SCRIPTED_MODEL_ENABLED": "true",
            "RATE_LIMIT_ENABLED": "false",
            "CHECKPOINTER_POOL_MIN_SIZE": "1",
            "CHECKPOINTER_POOL_MAX_SIZE": "2",
        }
    )
    log_path = run_root / "backend.log"
    with log_path.open("wb") as log:
        process = subprocess.Popen(  # noqa: S603 - fixed interpreter and module
            [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)],
            cwd=BACKEND,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        base = f"http://127.0.0.1:{port}"
        try:
            _wait_healthy(base, process, log_path)
            yield base
        finally:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def _wait_healthy(base: str, process: subprocess.Popen[bytes], log_path: Path) -> None:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError("backend exited:\n" + log_path.read_text()[-4000:])
        try:
            if httpx.get(f"{base}/api/health", timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise AssertionError("backend never became healthy:\n" + log_path.read_text()[-4000:])
