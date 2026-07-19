"""add row-level last payment date

Revision ID: 0043_ledger_last_payment_date
Revises: 0042_merge_heads
"""
from alembic import op
import sqlalchemy as sa

revision = "0043_ledger_last_payment_date"
down_revision = "0042_merge_heads"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("loan_ledger", sa.Column("last_payment_date", sa.Date(), nullable=True))


def downgrade():
    op.drop_column("loan_ledger", "last_payment_date")
