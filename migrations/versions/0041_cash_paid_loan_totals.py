"""separate cash receipts from loan settlement adjustments

Revision ID: 0041_cash_paid_loan_totals
Revises: 0040_delay_interest_waiver
"""
from alembic import op
import sqlalchemy as sa

revision = "0041_cash_paid_loan_totals"
down_revision = "0040_delay_interest_waiver"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("loans") as batch:
        batch.add_column(sa.Column("delay_interest_waiver_amount", sa.Numeric(18, 2), nullable=False, server_default="0.00"))
        batch.add_column(sa.Column("cash_paid_cache", sa.Numeric(18, 2), nullable=True))
    with op.batch_alter_table("loan_ledger") as batch:
        batch.add_column(sa.Column("waived_delay_interest_amount", sa.Numeric(18, 2), nullable=False, server_default="0.00"))


def downgrade():
    with op.batch_alter_table("loan_ledger") as batch:
        batch.drop_column("waived_delay_interest_amount")
    with op.batch_alter_table("loans") as batch:
        batch.drop_column("cash_paid_cache")
        batch.drop_column("delay_interest_waiver_amount")
