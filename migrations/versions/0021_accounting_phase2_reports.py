"""Accounting phase 2 financial reporting

Revision ID: 0021
Revises: 0020
Create Date: 2026-07-11 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

CLASSIFICATIONS = {
    "1000": ("CURRENT_ASSET", 10), "1010": ("CURRENT_ASSET", 20), "1100": ("CURRENT_ASSET", 30),
    "1110": ("CURRENT_ASSET", 40), "1120": ("CURRENT_ASSET", 50), "1990": ("CURRENT_ASSET", 90),
    "2000": ("CURRENT_LIABILITY", 10), "2100": ("CURRENT_LIABILITY", 20), "3000": ("EQUITY", 10),
    "3100": ("EQUITY", 20), "4000": ("OPERATING_INCOME", 10), "4010": ("OPERATING_INCOME", 20),
    "4020": ("OPERATING_INCOME", 30), "5000": ("STAFF_EXPENSE", 10), "5010": ("ADMIN_EXPENSE", 20),
    "5020": ("ADMIN_EXPENSE", 30), "5030": ("TRANSPORT_EXPENSE", 40), "5040": ("ADMIN_EXPENSE", 50),
    "5050": ("IMPAIRMENT_EXPENSE", 60),
}

def upgrade():
    bind = op.get_bind(); insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("accounting_accounts")}
    if "financial_statement_group" not in cols:
        op.add_column("accounting_accounts", sa.Column("financial_statement_group", sa.String(40), nullable=True))
    if "financial_statement_order" not in cols:
        op.add_column("accounting_accounts", sa.Column("financial_statement_order", sa.Integer(), nullable=True))
    if "cash_flow_group" not in cols:
        op.add_column("accounting_accounts", sa.Column("cash_flow_group", sa.String(40), nullable=True))
    for code, (group, order) in CLASSIFICATIONS.items():
        op.execute(sa.text("""
            UPDATE accounting_accounts
            SET financial_statement_group = COALESCE(financial_statement_group, :grp),
                financial_statement_order = COALESCE(financial_statement_order, :ord)
            WHERE account_code = :code
        """).bindparams(code=code, grp=group, ord=order))
    indexes = {ix["name"] for ix in insp.get_indexes("accounting_journal_entries")}
    if "ix_accounting_journal_entries_status_journal_date" not in indexes:
        op.create_index("ix_accounting_journal_entries_status_journal_date", "accounting_journal_entries", ["status", "journal_date"])
    line_indexes = {ix["name"] for ix in insp.get_indexes("accounting_journal_lines")}
    if "ix_accounting_journal_lines_account_entry" not in line_indexes:
        op.create_index("ix_accounting_journal_lines_account_entry", "accounting_journal_lines", ["account_id", "journal_entry_id"])

def downgrade():
    op.drop_index("ix_accounting_journal_lines_account_entry", table_name="accounting_journal_lines")
    op.drop_index("ix_accounting_journal_entries_status_journal_date", table_name="accounting_journal_entries")
    op.drop_column("accounting_accounts", "cash_flow_group")
    op.drop_column("accounting_accounts", "financial_statement_order")
    op.drop_column("accounting_accounts", "financial_statement_group")
