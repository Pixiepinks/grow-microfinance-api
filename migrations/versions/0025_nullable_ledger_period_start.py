"""Allow legacy loan ledger rows without period start dates.

Revision ID: 0025_nullable_ledger_period_start
Revises: 0024_sync_loan_terms
Create Date: 2026-07-12
"""

from alembic import op
import sqlalchemy as sa

revision = "0025_nullable_ledger_period_start"
down_revision = "0024_sync_loan_terms"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("loan_ledger") as batch_op:
        batch_op.alter_column("period_start_date", existing_type=sa.Date(), nullable=True)


def downgrade():
    with op.batch_alter_table("loan_ledger") as batch_op:
        batch_op.alter_column("period_start_date", existing_type=sa.Date(), nullable=False)
