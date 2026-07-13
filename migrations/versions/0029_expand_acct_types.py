"""Expand accounting account type metadata columns.

Revision ID: 0029_expand_acct_types
Revises: 0028_collector_master
Create Date: 2026-07-13
"""

from alembic import op
import sqlalchemy as sa


revision = "0029_expand_acct_types"
down_revision = "0028_collector_master"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "accounting_accounts",
        "cash_flow_category",
        existing_type=sa.String(length=20),
        type_=sa.String(length=50),
        existing_nullable=False,
        existing_server_default="NONE",
    )
    op.alter_column(
        "accounting_accounts",
        "account_subtype",
        existing_type=sa.String(length=40),
        type_=sa.String(length=50),
        existing_nullable=False,
        existing_server_default="OTHER",
    )
    op.alter_column(
        "accounting_accounts",
        "account_role",
        existing_type=sa.String(length=20),
        type_=sa.String(length=50),
        existing_nullable=True,
    )


def downgrade():
    op.alter_column(
        "accounting_accounts",
        "account_role",
        existing_type=sa.String(length=50),
        type_=sa.String(length=20),
        existing_nullable=True,
    )
    op.alter_column(
        "accounting_accounts",
        "account_subtype",
        existing_type=sa.String(length=50),
        type_=sa.String(length=40),
        existing_nullable=False,
        existing_server_default="OTHER",
    )
    op.alter_column(
        "accounting_accounts",
        "cash_flow_category",
        existing_type=sa.String(length=50),
        type_=sa.String(length=20),
        existing_nullable=False,
        existing_server_default="NONE",
    )
