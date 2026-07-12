"""Loan accrual accounting fields.

Revision ID: 0026_loan_accrual
Revises: 0025_ledger_start_nullable
Create Date: 2026-07-12
"""
from alembic import op
import sqlalchemy as sa

revision = "0026_loan_accrual"
down_revision = "0025_ledger_start_nullable"
branch_labels = None
depends_on = None


def _cols(inspector, table):
    return {c["name"] for c in inspector.get_columns(table)}

def _add(table, column):
    bind = op.get_bind(); inspector = sa.inspect(bind)
    if column.name not in _cols(inspector, table):
        op.add_column(table, column)

def _idx(name, table, cols, unique=False):
    bind = op.get_bind(); inspector = sa.inspect(bind)
    if name not in {i["name"] for i in inspector.get_indexes(table)}:
        op.create_index(name, table, cols, unique=unique)


def upgrade():
    _add("loan_ledger", sa.Column("interest_accrued", sa.Boolean(), nullable=False, server_default=sa.false()))
    _add("loan_ledger", sa.Column("interest_accrued_at", sa.DateTime(), nullable=True))
    _add("loan_ledger", sa.Column("interest_accrual_journal_id", sa.Integer(), nullable=True))
    _add("loan_ledger", sa.Column("delay_interest_accrued", sa.Numeric(18,2), nullable=False, server_default="0"))
    _add("loan_ledger", sa.Column("delay_interest_accrued_at", sa.DateTime(), nullable=True))
    _add("loan_ledger", sa.Column("delay_interest_accrual_journal_id", sa.Integer(), nullable=True))
    _add("loan_ledger", sa.Column("principal_paid", sa.Numeric(18,2), nullable=False, server_default="0"))
    _add("loan_ledger", sa.Column("interest_paid", sa.Numeric(18,2), nullable=False, server_default="0"))
    _add("loan_ledger", sa.Column("delay_interest_paid", sa.Numeric(18,2), nullable=False, server_default="0"))
    _add("loan_ledger", sa.Column("unapplied_amount", sa.Numeric(18,2), nullable=False, server_default="0"))
    _add("loans", sa.Column("interest_accounting_method", sa.String(32), nullable=False, server_default="ACCRUAL_BY_INSTALLMENT"))
    _add("loans", sa.Column("historical_accrual_mode", sa.String(16), nullable=False, server_default="AUTO"))
    _add("loans", sa.Column("accrual_processed_through", sa.Date(), nullable=True))
    _add("loans", sa.Column("disbursement_journal_id", sa.Integer(), nullable=True))
    _add("loans", sa.Column("reversed_at", sa.DateTime(), nullable=True))
    _add("loans", sa.Column("reversal_journal_id", sa.Integer(), nullable=True))
    for col in [
        sa.Column("source_type", sa.String(80), nullable=True), sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("loan_id", sa.Integer(), nullable=True), sa.Column("customer_id", sa.Integer(), nullable=True),
        sa.Column("reversal_of_journal_id", sa.Integer(), nullable=True), sa.Column("is_reversal", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("accounting_date", sa.Date(), nullable=True)]:
        _add("accounting_journal_entries", col)
    _add("payments", sa.Column("journal_id", sa.Integer(), nullable=True))
    _add("payments", sa.Column("reversed_at", sa.DateTime(), nullable=True))
    _add("payments", sa.Column("reversal_journal_id", sa.Integer(), nullable=True))
    _add("payments", sa.Column("reversal_reason", sa.Text(), nullable=True))
    bind = op.get_bind(); inspector = sa.inspect(bind)
    if "payment_allocations" not in inspector.get_table_names():
        op.create_table("payment_allocations", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("payment_id", sa.Integer(), nullable=False), sa.Column("loan_id", sa.Integer(), nullable=False), sa.Column("ledger_id", sa.Integer(), nullable=True), sa.Column("allocation_type", sa.String(32), nullable=False), sa.Column("amount", sa.Numeric(18,2), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()))
    if "accounting_periods" not in inspector.get_table_names():
        op.create_table("accounting_periods", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("period", sa.String(7), nullable=False, unique=True), sa.Column("start_date", sa.Date(), nullable=False), sa.Column("end_date", sa.Date(), nullable=False), sa.Column("is_locked", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("locked_at", sa.DateTime()), sa.Column("locked_by_id", sa.Integer()), sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()))
    _idx("ix_loan_ledger_due_accrued", "loan_ledger", ["due_date", "interest_accrued"])
    _idx("ix_aje_source", "accounting_journal_entries", ["source_type", "source_id"])
    _idx("uq_aje_logical_source", "accounting_journal_entries", ["source_type", "source_id"], unique=True)


def downgrade():
    pass
