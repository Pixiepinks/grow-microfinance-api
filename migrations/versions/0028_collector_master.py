"""Collector master management fields.

Revision ID: 0028_collector_master
Revises: 0027_collector_collect
Create Date: 2026-07-12
"""
from alembic import op
import sqlalchemy as sa

revision = "0028_collector_master"
down_revision = "0027_collector_collect"
branch_labels = None
depends_on = None

ACCOUNT_SUBTYPE_CHECK = "account_subtype in ('CASH','BANK','COLLECTION_CLEARING','COLLECTION_CLEARING_CONTROL','LOAN_RECEIVABLE','INTEREST_RECEIVABLE','PENALTY_RECEIVABLE','OTHER_CURRENT_ASSET','FIXED_ASSET','ACCOUNTS_PAYABLE','BORROWING','CAPITAL','RETAINED_EARNINGS','INTEREST_INCOME','PENALTY_INCOME','FEE_INCOME','OPERATING_EXPENSE','WRITE_OFF_EXPENSE','SUSPENSE','OTHER')"
COLLECTOR_STATUS_CHECK = "collector_status in ('ACTIVE','INACTIVE','SUSPENDED')"


def _columns(bind, table):
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def _checks(bind, table):
    return {c.get("name") for c in sa.inspect(bind).get_check_constraints(table) if c.get("name")}


def _indexes(bind, table):
    return {i.get("name") for i in sa.inspect(bind).get_indexes(table) if i.get("name")}


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "users" not in tables or "accounting_accounts" not in tables:
        raise RuntimeError("Missing required tables: users, accounting_accounts")

    user_cols = _columns(bind, "users")
    additions = [
        ("is_collector", sa.Column("is_collector", sa.Boolean(), nullable=False, server_default=sa.false())),
        ("collector_code", sa.Column("collector_code", sa.String(50), nullable=True)),
        ("collector_status", sa.Column("collector_status", sa.String(20), nullable=False, server_default="ACTIVE")),
        ("default_collection_account_id", sa.Column("default_collection_account_id", sa.Integer(), nullable=True)),
        ("can_collect_cash", sa.Column("can_collect_cash", sa.Boolean(), nullable=False, server_default=sa.false())),
    ]
    for name, column in additions:
        if name not in user_cols:
            op.add_column("users", column)

    if bind.dialect.name == "postgresql":
        checks = _checks(bind, "accounting_accounts")
        if "ck_accounting_accounts_subtype" in checks:
            op.drop_constraint("ck_accounting_accounts_subtype", "accounting_accounts", type_="check")
        op.create_check_constraint("ck_accounting_accounts_subtype", "accounting_accounts", ACCOUNT_SUBTYPE_CHECK)
        if "ck_users_collector_status" not in _checks(bind, "users"):
            op.create_check_constraint("ck_users_collector_status", "users", COLLECTOR_STATUS_CHECK)

    if "ix_users_collector_code" not in _indexes(bind, "users"):
        op.create_index("ix_users_collector_code", "users", ["collector_code"], unique=True)
    if "ix_users_collector_flags" not in _indexes(bind, "users"):
        op.create_index("ix_users_collector_flags", "users", ["is_collector", "collector_status", "can_collect_cash"])

    op.execute("UPDATE accounting_accounts SET account_subtype = 'COLLECTION_CLEARING_CONTROL', allow_manual_posting = false, is_collection_account = false, collector_id = NULL WHERE account_code = '1050'")


def downgrade():
    pass
