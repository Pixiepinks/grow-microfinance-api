"""Collector clearing collections and deposits.

Revision ID: 0027_collector_collect
Revises: 0026_loan_accrual
Create Date: 2026-07-12
"""
from alembic import op
import sqlalchemy as sa

revision = "0027_collector_collect"
down_revision = "0026_loan_accrual"
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
    bind = op.get_bind(); inspector = sa.inspect(bind)
    try:
        op.drop_constraint("ck_accounting_accounts_subtype", "accounting_accounts", type_="check")
    except Exception:
        pass
    try:
        op.create_check_constraint("ck_accounting_accounts_subtype", "accounting_accounts", "account_subtype in ('CASH','BANK','COLLECTION_CLEARING','LOAN_RECEIVABLE','INTEREST_RECEIVABLE','PENALTY_RECEIVABLE','OTHER_CURRENT_ASSET','FIXED_ASSET','ACCOUNTS_PAYABLE','BORROWING','CAPITAL','RETAINED_EARNINGS','INTEREST_INCOME','PENALTY_INCOME','FEE_INCOME','OPERATING_EXPENSE','WRITE_OFF_EXPENSE','SUSPENSE','OTHER')")
    except Exception:
        pass
    for col in [
        sa.Column("collector_id", sa.Integer(), nullable=True),
        sa.Column("is_collection_account", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("account_role", sa.String(50), nullable=True),
        sa.Column("parent_account_id", sa.Integer(), nullable=True),
    ]:
        _add("accounting_accounts", col)
    for col in [
        sa.Column("payment_date", sa.Date(), nullable=True),
        sa.Column("accounting_date", sa.Date(), nullable=True),
        sa.Column("collection_method", sa.String(50), nullable=True),
        sa.Column("collection_account_id", sa.Integer(), nullable=True),
        sa.Column("collector_id", sa.Integer(), nullable=True),
        sa.Column("bank_reference", sa.String(120), nullable=True),
        sa.Column("receipt_number", sa.String(40), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="POSTED"),
        sa.Column("reversed_by", sa.Integer(), nullable=True),
        sa.Column("deposited_amount", sa.Numeric(18,2), nullable=False, server_default="0"),
        sa.Column("deposit_status", sa.String(30), nullable=False, server_default="NOT_APPLICABLE"),
    ]:
        _add("payments", col)
    bind = op.get_bind(); inspector = sa.inspect(bind)
    if "collection_deposit_batches" not in inspector.get_table_names():
        op.create_table("collection_deposit_batches",
            sa.Column("id", sa.Integer(), primary_key=True), sa.Column("deposit_number", sa.String(40), nullable=False, unique=True),
            sa.Column("collector_id", sa.Integer(), nullable=False), sa.Column("collector_account_id", sa.Integer(), nullable=False), sa.Column("bank_account_id", sa.Integer(), nullable=False),
            sa.Column("deposit_date", sa.Date(), nullable=False), sa.Column("accounting_date", sa.Date(), nullable=False), sa.Column("total_amount", sa.Numeric(18,2), nullable=False),
            sa.Column("bank_reference", sa.String(120)), sa.Column("deposit_slip_reference", sa.String(120)), sa.Column("remarks", sa.Text()),
            sa.Column("journal_entry_id", sa.Integer()), sa.Column("reversal_journal_id", sa.Integer()), sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
            sa.Column("created_by", sa.Integer()), sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()), sa.Column("reversed_at", sa.DateTime()), sa.Column("reversal_reason", sa.Text()))
    if "collection_deposit_allocations" not in inspector.get_table_names():
        op.create_table("collection_deposit_allocations", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("deposit_batch_id", sa.Integer(), nullable=False), sa.Column("payment_id", sa.Integer(), nullable=False), sa.Column("allocated_amount", sa.Numeric(18,2), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()))
    _idx("ix_payments_receipt_number", "payments", ["receipt_number"], unique=True)
    _idx("ix_collection_deposit_batches_no", "collection_deposit_batches", ["deposit_number"], unique=True)
    _idx("ix_cda_batch", "collection_deposit_allocations", ["deposit_batch_id"])
    _idx("ix_cda_payment", "collection_deposit_allocations", ["payment_id"])
    op.execute("UPDATE payments SET payment_date = collection_date WHERE payment_date IS NULL")
    op.execute("UPDATE payments SET accounting_date = collection_date WHERE accounting_date IS NULL")
    op.execute("UPDATE payments SET collection_method = CASE WHEN lower(coalesce(payment_method,'')) LIKE '%bank%' THEN 'BANK_TRANSFER' ELSE 'CASH_OFFICE' END WHERE collection_method IS NULL")


def downgrade():
    pass
