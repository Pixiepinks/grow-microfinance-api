"""add calendar days to investor interest accruals

Revision ID: 0038_inv_accr_cal_days
Revises: 0037_manual_journal_workflow
Create Date: 2026-07-18 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0038_inv_accr_cal_days"
down_revision = "0037_manual_journal_workflow"
branch_labels = None
depends_on = None


def _cols(table):
    bind = op.get_bind()
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade():
    if "calendar_days_in_month" not in _cols("investor_interest_accruals"):
        op.add_column("investor_interest_accruals", sa.Column("calendar_days_in_month", sa.Integer(), nullable=False, server_default="0"))


def downgrade():
    if "calendar_days_in_month" in _cols("investor_interest_accruals"):
        op.drop_column("investor_interest_accruals", "calendar_days_in_month")
