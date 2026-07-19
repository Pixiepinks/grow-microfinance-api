"""add delay-interest accrual waiver audit fields

Revision ID: 0040_delay_interest_waiver
Revises: 0040_early_loan_settlement
"""
from alembic import op
import sqlalchemy as sa

revision = "0040_delay_interest_waiver"
down_revision = "0040_early_loan_settlement"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("loan_ledger", sa.Column("delay_interest_waived", sa.Numeric(18, 2), nullable=False, server_default="0.00"))
    op.add_column("customer_credit_balances", sa.Column("applied_to_loan_id", sa.Integer(), nullable=True))
    op.add_column("customer_credit_balances", sa.Column("correcting_journal_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_credit_applied_loan", "customer_credit_balances", "loans", ["applied_to_loan_id"], ["id"])
    op.create_foreign_key("fk_credit_correcting_journal", "customer_credit_balances", "accounting_journal_entries", ["correcting_journal_id"], ["id"])
    op.create_table("loan_charge_waivers", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("waiver_number", sa.String(40), nullable=False, unique=True), sa.Column("loan_id", sa.Integer(), nullable=False), sa.Column("customer_id", sa.Integer(), nullable=False), sa.Column("ledger_entry_id", sa.Integer()), sa.Column("waiver_type", sa.String(30), nullable=False), sa.Column("waiver_date", sa.Date(), nullable=False), sa.Column("amount", sa.Numeric(18,2), nullable=False), sa.Column("receivable_account_id", sa.Integer(), nullable=False), sa.Column("expense_account_id", sa.Integer(), nullable=False), sa.Column("journal_entry_id", sa.Integer()), sa.Column("approval_reference", sa.String(120)), sa.Column("reason", sa.Text()), sa.Column("status", sa.String(20), nullable=False), sa.Column("approved_by", sa.Integer()), sa.Column("approved_at", sa.DateTime()), sa.Column("reversed_by", sa.Integer()), sa.Column("reversed_at", sa.DateTime()), sa.Column("reversal_journal_id", sa.Integer()), sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("updated_at", sa.DateTime(), nullable=False))

def downgrade():
    op.drop_table("loan_charge_waivers")
    op.drop_column("customer_credit_balances", "correcting_journal_id"); op.drop_column("customer_credit_balances", "applied_to_loan_id")
    op.drop_column("loan_ledger", "delay_interest_waived")
