"""Accounting phase 1 foundation

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-10 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("payments", sa.Column("principal_paid", sa.Numeric(12,2), nullable=False, server_default="0"))
    op.add_column("payments", sa.Column("interest_paid", sa.Numeric(12,2), nullable=False, server_default="0"))
    op.add_column("payments", sa.Column("penalty_paid", sa.Numeric(12,2), nullable=False, server_default="0"))
    op.add_column("payments", sa.Column("other_fee_paid", sa.Numeric(12,2), nullable=False, server_default="0"))
    op.create_table("accounting_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_code", sa.String(32), nullable=False),
        sa.Column("account_name", sa.String(150), nullable=False),
        sa.Column("account_type", sa.String(20), nullable=False),
        sa.Column("normal_balance", sa.String(10), nullable=False),
        sa.Column("parent_id", sa.Integer(), sa.ForeignKey("accounting_accounts.id")),
        sa.Column("description", sa.Text()),
        sa.Column("is_system_account", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("allow_manual_posting", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("account_type in ('ASSET','LIABILITY','EQUITY','INCOME','EXPENSE')", name="ck_accounting_accounts_type"),
        sa.CheckConstraint("normal_balance in ('DEBIT','CREDIT')", name="ck_accounting_accounts_normal_balance"),
    )
    op.create_index("ix_accounting_accounts_account_code", "accounting_accounts", ["account_code"], unique=True)
    op.create_table("accounting_journal_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("journal_no", sa.String(40), nullable=False),
        sa.Column("journal_date", sa.Date(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("reference_type", sa.String(50)),
        sa.Column("reference_id", sa.String(64)),
        sa.Column("source_module", sa.String(50)),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("posted_at", sa.DateTime()),
        sa.Column("posted_by_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("reversal_of_id", sa.Integer(), sa.ForeignKey("accounting_journal_entries.id")),
        sa.Column("idempotency_key", sa.String(160)),
        sa.Column("total_debit", sa.Numeric(18,2), nullable=False, server_default="0"),
        sa.Column("total_credit", sa.Numeric(18,2), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_accounting_journal_entries_journal_no", "accounting_journal_entries", ["journal_no"], unique=True)
    op.create_index("ix_accounting_journal_entries_journal_date", "accounting_journal_entries", ["journal_date"])
    op.create_index("ix_accounting_journal_entries_idempotency_key", "accounting_journal_entries", ["idempotency_key"], unique=True)
    op.create_table("accounting_journal_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("journal_entry_id", sa.Integer(), sa.ForeignKey("accounting_journal_entries.id"), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounting_accounts.id"), nullable=False),
        sa.Column("debit", sa.Numeric(18,2), nullable=False, server_default="0"),
        sa.Column("credit", sa.Numeric(18,2), nullable=False, server_default="0"),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id")),
        sa.Column("loan_id", sa.Integer(), sa.ForeignKey("loans.id")),
        sa.Column("payment_id", sa.Integer(), sa.ForeignKey("payments.id")),
        sa.Column("collection_id", sa.Integer()),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("journal_entry_id", "line_no", name="uq_accounting_journal_line_no"),
        sa.CheckConstraint("debit >= 0", name="ck_accounting_journal_lines_debit_nonnegative"),
        sa.CheckConstraint("credit >= 0", name="ck_accounting_journal_lines_credit_nonnegative"),
        sa.CheckConstraint("(debit > 0 and credit = 0) or (credit > 0 and debit = 0)", name="ck_accounting_journal_lines_one_sided"),
    )
    op.create_index("ix_accounting_journal_lines_journal_entry_id", "accounting_journal_lines", ["journal_entry_id"])
    op.create_index("ix_accounting_journal_lines_account_id", "accounting_journal_lines", ["account_id"])
    op.create_table("accounting_settings", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("setting_key", sa.String(80), nullable=False, unique=True), sa.Column("setting_value", sa.Text(), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("updated_at", sa.DateTime(), nullable=False))
    op.create_table("accounting_audit_logs", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("action", sa.String(80), nullable=False), sa.Column("entity_type", sa.String(80), nullable=False), sa.Column("entity_id", sa.String(64)), sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id")), sa.Column("details", sa.Text()), sa.Column("created_at", sa.DateTime(), nullable=False))

def downgrade():
    op.drop_table("accounting_audit_logs"); op.drop_table("accounting_settings"); op.drop_table("accounting_journal_lines"); op.drop_table("accounting_journal_entries"); op.drop_table("accounting_accounts")
    for c in ["other_fee_paid","penalty_paid","interest_paid","principal_paid"]: op.drop_column("payments", c)
