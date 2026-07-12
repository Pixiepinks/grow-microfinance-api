"""Allow legacy loan ledger rows without period start dates.

Revision ID: 0025_ledger_start_nullable
Revises: 0024_sync_loan_terms
Create Date: 2026-07-12
"""

from alembic import op
import sqlalchemy as sa

revision = "0025_ledger_start_nullable"
down_revision = "0024_sync_loan_terms"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"]: column for column in inspector.get_columns("loan_ledger")}
    period_start = columns.get("period_start_date")

    if period_start and not period_start.get("nullable", True):
        op.alter_column(
            "loan_ledger",
            "period_start_date",
            existing_type=sa.Date(),
            nullable=True,
        )


def downgrade():
    bind = op.get_bind()
    null_count = bind.execute(
        sa.text(
            """
            SELECT COUNT(*)
            FROM loan_ledger
            WHERE period_start_date IS NULL
            """
        )
    ).scalar_one()

    if null_count:
        raise RuntimeError(
            "Cannot downgrade: loan_ledger contains NULL period_start_date values"
        )

    op.alter_column(
        "loan_ledger",
        "period_start_date",
        existing_type=sa.Date(),
        nullable=False,
    )
