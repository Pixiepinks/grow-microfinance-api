"""Add loan repayment ledger

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-09 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "loans",
        sa.Column("payment_interval_days", sa.Integer(), nullable=False, server_default="7"),
    )
    op.create_table(
        "loan_ledger",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("loan_id", sa.Integer(), sa.ForeignKey("loans.id"), nullable=False),
        sa.Column("installment_no", sa.Integer(), nullable=False),
        sa.Column("period_start_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("period_days", sa.Integer(), nullable=False),
        sa.Column("opening_balance", sa.Numeric(12, 2), nullable=False),
        sa.Column("interest_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("principal_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("installment_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("closing_balance", sa.Numeric(12, 2), nullable=False),
        sa.Column("paid_amount", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("paid_date", sa.Date(), nullable=True),
        sa.Column("delay_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("delay_interest", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("loan_id", "installment_no", name="uq_loan_ledger_loan_installment"),
    )
    op.create_index("ix_loan_ledger_loan_id", "loan_ledger", ["loan_id"])


def downgrade():
    op.drop_index("ix_loan_ledger_loan_id", table_name="loan_ledger")
    op.drop_table("loan_ledger")
    op.drop_column("loans", "payment_interval_days")
