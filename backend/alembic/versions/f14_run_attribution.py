"""F-14 (plan m89 ``m89_audit_spend_org``, 6.2, 8.7, 11.10, Q8, Q22): run attribution.

* ``conversation_runs``: ``org_id``/``tenant_id`` (copied from the conversation),
  ``run_source``, ``invoker_type``, ``resource_owner_user_id``, ``on_behalf_of_user_id``,
  ``credential_subject_user_id``; ``user_id`` stays the requester/billed user.
* ``token_usages``: the same scope plus ``caller_user_id`` (copied from the run).
* ``conversation_run_agents``: each participant's ``invoker_type``, owner, acting user and
  account (subagents keep their own identity).
* ``daily_spend_user|agent|model``: ``org_id``/``tenant_id``; the UPSERT key becomes
  ``(org_id, date, target)``.
* ``audit_events.org_id`` (nullable) and index ``(org_id, created_at)``.

Backfill: every existing run was the owner's (legacy runs are owner-only), so the
requester, acting user and account are the run's ``user_id``; source is ``API`` for runs
behind an Agent API run, ``TRIGGER`` behind a trigger run, else ``CHAT``. Spend rows get
the agent's organization, the user's company default organization, or the model company's
default organization; rows that cannot be placed stay NULL (sums are unaffected).
Audit rows are not backfilled. Each table is backfilled in one set-based UPDATE (no
id-range batching: UUID keys and alembic's single transaction make it impractical), and
the new indexes are built afterwards; on large ``token_usages``/``conversation_runs``
tables run this in a maintenance window. Downgrade folds per-organization spend rows back
into one row per ``(date, target)`` (sums added) before restoring the old unique key.
``agent_trigger_runs.reason_code`` (same plan row) belongs to the 11.6 trigger blocking
work and is not added here.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.migrations.org_authz_v1.contracts import NAMING
from app.migrations.org_authz_v1.scope_ddl import add_org_pair, drop_org_pair

revision = "f14_run_attribution"
down_revision = "f09_agent_memo"
branch_labels = None
depends_on = None

ACTING = ("resource_owner_user_id", "on_behalf_of_user_id", "credential_subject_user_id")
SOURCE = ("run_source", "invoker_type")
INDEXES = (
    ("ix_conversation_runs_org_created", "conversation_runs", ("org_id", "created_at")),
    ("ix_token_usages_org_created", "token_usages", ("org_id", "created_at")),
    ("ix_token_usages_agent_caller", "token_usages", ("agent_id", "caller_user_id")),
    ("ix_audit_events_org_created", "audit_events", ("org_id", "created_at")),
)
SPEND = (
    ("daily_spend_user", "user"),
    ("daily_spend_agent", "agent"),
    ("daily_spend_model", "model"),
)


def _add_users(table: str, names: tuple[str, ...]) -> None:
    with op.batch_alter_table(table, naming_convention=NAMING) as batch:
        for name in names:
            batch.add_column(sa.Column(name, sa.Uuid(), nullable=True))
            batch.create_foreign_key(
                f"fk_{table}_{name}_users", "users", [name], ["id"], ondelete="SET NULL"
            )


def _add_strings(table: str, names: tuple[str, ...]) -> None:
    with op.batch_alter_table(table, naming_convention=NAMING) as batch:
        for name in names:
            batch.add_column(sa.Column(name, sa.String(20), nullable=True))


def _drop(table: str, users: tuple[str, ...], strings: tuple[str, ...]) -> None:
    with op.batch_alter_table(table, naming_convention=NAMING) as batch:
        for name in users:
            batch.drop_constraint(f"fk_{table}_{name}_users", type_="foreignkey")
            batch.drop_column(name)
        for name in strings:
            batch.drop_column(name)


def _schema() -> None:
    add_org_pair("conversation_runs")
    _add_strings("conversation_runs", SOURCE)
    _add_users("conversation_runs", ACTING)
    add_org_pair("token_usages")
    _add_strings("token_usages", SOURCE)
    _add_users("token_usages", ("caller_user_id", *ACTING))
    _add_strings("conversation_run_agents", ("invoker_type",))
    _add_users("conversation_run_agents", ACTING)
    add_org_pair("audit_events", tenant_exists=True)
    for table, axis in SPEND:
        add_org_pair(table)
        with op.batch_alter_table(table, naming_convention=NAMING) as batch:
            batch.drop_constraint(f"uq_{table}_date_{axis}", type_="unique")
            batch.create_unique_constraint(
                f"uq_{table}_org_date_{axis}", ["org_id", "date", f"{axis}_id"]
            )


_SOURCE_CASE = (
    "CASE WHEN id IN (SELECT conversation_run_id FROM agent_api_runs) THEN '{api}' "
    "WHEN id IN (SELECT conversation_run_id FROM agent_trigger_runs) THEN '{trigger}' "
    "ELSE '{chat}' END"
)
_RUNS = (
    "UPDATE conversation_runs SET "
    "org_id = (SELECT c.org_id FROM conversations c "
    "WHERE c.id = conversation_runs.conversation_id), "
    "tenant_id = (SELECT c.tenant_id FROM conversations c "
    "WHERE c.id = conversation_runs.conversation_id), "
    f"run_source = {_SOURCE_CASE.format(api='API', trigger='TRIGGER', chat='CHAT')}, "
    f"invoker_type = {_SOURCE_CASE.format(api='api', trigger='schedule', chat='user')}, "
    "resource_owner_user_id = COALESCE((SELECT a.user_id FROM agents a "
    "WHERE a.id = conversation_runs.agent_id), user_id), "
    "on_behalf_of_user_id = user_id, credential_subject_user_id = user_id",
)
_RUN_OF = "(SELECT r.{col} FROM conversation_runs r WHERE r.id = token_usages.run_id)"
_CONV_OF = "(SELECT c.{col} FROM conversations c WHERE c.id = token_usages.conversation_id)"
_CALLER = f"COALESCE({_RUN_OF.format(col='user_id')}, {_CONV_OF.format(col='user_id')})"
_USAGE = (  # one pass over token_usages
    "UPDATE token_usages SET "
    + ", ".join(
        f"{col} = COALESCE({_RUN_OF.format(col=col)}, {_CONV_OF.format(col=col)})"
        for col in ("org_id", "tenant_id")
    )
    + f", caller_user_id = {_CALLER}, "
    + ", ".join(f"{col} = {_RUN_OF.format(col=col)}" for col in SOURCE)
    + ", resource_owner_user_id = COALESCE((SELECT a.user_id FROM agents a "
    f"WHERE a.id = token_usages.agent_id), {_CALLER}), "
    f"on_behalf_of_user_id = {_CALLER}, credential_subject_user_id = {_CALLER}",
)
_OWNER_OF = (
    "COALESCE((SELECT a.user_id FROM agents a "
    "WHERE a.id = conversation_run_agents.agent_id_snapshot), (SELECT r.user_id FROM "
    "conversation_runs r WHERE r.id = conversation_run_agents.run_id))"
)
_PARTICIPANTS = (
    "UPDATE conversation_run_agents SET invoker_type = CASE WHEN role = 'subagent' "
    "THEN 'subagent' ELSE (SELECT r.invoker_type FROM conversation_runs r "
    "WHERE r.id = conversation_run_agents.run_id) END, "
    f"resource_owner_user_id = {_OWNER_OF}, on_behalf_of_user_id = {_OWNER_OF}, "
    f"credential_subject_user_id = {_OWNER_OF}",
)
_DEFAULT_ORG = (
    "SELECT o.{col} FROM organizations o WHERE o.is_default = :yes AND o.tenant_id = ({tenant}) "
    "ORDER BY o.id LIMIT 1"
)
_USER_TENANT = (
    "SELECT tm.tenant_id FROM tenant_members tm WHERE tm.user_id = daily_spend_user.user_id "
    "ORDER BY tm.tenant_id LIMIT 1"
)
_MODEL_TENANT = "SELECT m.tenant_id FROM models m WHERE m.id = daily_spend_model.model_id"
_SPEND = (
    "UPDATE daily_spend_agent SET "
    "org_id = (SELECT a.org_id FROM agents a WHERE a.id = daily_spend_agent.agent_id), "
    "tenant_id = (SELECT a.tenant_id FROM agents a WHERE a.id = daily_spend_agent.agent_id)",
    *(
        f"UPDATE {table} SET "
        + ", ".join(
            f"{col} = ({_DEFAULT_ORG.format(col=key, tenant=tenant)})"
            for col, key in (("org_id", "id"), ("tenant_id", "tenant_id"))
        )
        for table, tenant in (
            ("daily_spend_user", _USER_TENANT),
            ("daily_spend_model", _MODEL_TENANT),
        )
    ),
)


def upgrade() -> None:
    _schema()
    bind = op.get_bind()
    for statement in (*_RUNS, *_USAGE, *_PARTICIPANTS, *_SPEND):
        bind.execute(sa.text(statement), {"yes": True} if ":yes" in statement else {})
    # Indexes after the backfill, so the UPDATEs do not maintain them row by row.
    for name, table, columns in INDEXES:
        op.create_index(name, table, list(columns))


def _merge_spend(table: str, axis: str) -> None:
    """Fold per-organization rows back into one ``(date, target)`` row before the old key."""
    same = f"x.date = {table}.date AND x.{axis}_id = {table}.{axis}_id"
    keeper = f"(SELECT MIN(CAST(x.id AS TEXT)) FROM {table} x WHERE {same})"
    sums = ", ".join(
        f"{col} = (SELECT SUM(x.{col}) FROM {table} x WHERE {same})"
        for col in ("total_tokens_in", "total_tokens_out", "total_cost_usd", "request_count")
    )
    bind = op.get_bind()
    bind.execute(
        sa.text(
            f"UPDATE {table} SET {sums}, updated_at = (SELECT MAX(x.updated_at) FROM {table} x "  # noqa: S608
            f"WHERE {same}) WHERE CAST(id AS TEXT) = {keeper}"
        )
    )
    bind.execute(sa.text(f"DELETE FROM {table} WHERE CAST(id AS TEXT) <> {keeper}"))  # noqa: S608


def downgrade() -> None:
    for name, table, _columns in reversed(INDEXES):
        op.drop_index(name, table_name=table)
    for table, axis in SPEND:
        _merge_spend(table, axis)
        with op.batch_alter_table(table, naming_convention=NAMING) as batch:
            batch.drop_constraint(f"uq_{table}_org_date_{axis}", type_="unique")
            batch.create_unique_constraint(f"uq_{table}_date_{axis}", ["date", f"{axis}_id"])
        drop_org_pair(table)
    drop_org_pair("audit_events", keep_tenant=True)
    _drop("conversation_run_agents", ACTING, ("invoker_type",))
    _drop("token_usages", ("caller_user_id", *ACTING), SOURCE)
    drop_org_pair("token_usages")
    _drop("conversation_runs", ACTING, SOURCE)
    drop_org_pair("conversation_runs")
