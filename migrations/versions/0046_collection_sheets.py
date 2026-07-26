"""collection sheet approval and posting

Revision ID: 0046_collection_sheets
Revises: 0045_post_settlement_payments
"""
from alembic import op
import sqlalchemy as sa

revision = "0046_collection_sheets"
down_revision = "0045_post_settlement_payments"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("collection_sheets",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("sheet_number", sa.String(40), nullable=False),
        sa.Column("collector_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False), sa.Column("collection_date", sa.Date(), nullable=False),
        sa.Column("gross_collection", sa.Numeric(18,2), nullable=False, server_default="0"), sa.Column("total_expenses", sa.Numeric(18,2), nullable=False, server_default="0"),
        sa.Column("expected_deposit", sa.Numeric(18,2), nullable=False, server_default="0"), sa.Column("actual_deposit", sa.Numeric(18,2)),
        sa.Column("difference", sa.Numeric(18,2), nullable=False, server_default="0"), sa.Column("bank_account_id", sa.Integer(), sa.ForeignKey("accounting_accounts.id")),
        sa.Column("bank_journal_id", sa.Integer(), sa.ForeignKey("accounting_journal_entries.id")), sa.Column("deposit_date", sa.Date()),
        sa.Column("deposit_reference", sa.String(120)), sa.Column("status", sa.String(30), nullable=False, server_default="DRAFT"),
        sa.Column("notes", sa.Text()), sa.Column("posting_key", sa.String(160)), sa.Column("posting_result", sa.JSON()),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False), sa.Column("submitted_by_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("approved_by_id", sa.Integer(), sa.ForeignKey("users.id")), sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False), sa.Column("submitted_at", sa.DateTime()), sa.Column("approved_at", sa.DateTime()),
        sa.Column("posted_at", sa.DateTime()), sa.Column("reconciled_at", sa.DateTime()), sa.Column("reversal_of_id", sa.Integer(), sa.ForeignKey("collection_sheets.id")),
        sa.Column("reversed_at", sa.DateTime()), sa.Column("reversed_by_id", sa.Integer(), sa.ForeignKey("users.id")), sa.Column("reversal_reason", sa.Text()),
        sa.UniqueConstraint("sheet_number"), sa.UniqueConstraint("posting_key"))
    op.create_index("ix_collection_sheets_sheet_number", "collection_sheets", ["sheet_number"], unique=True)
    op.create_index("ix_collection_sheets_status", "collection_sheets", ["status"])
    op.create_index("ix_collection_sheets_date_collector", "collection_sheets", ["collection_date", "collector_id"])
    op.create_table("collection_sheet_items",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("collection_sheet_id", sa.Integer(), sa.ForeignKey("collection_sheets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False), sa.Column("loan_id", sa.Integer(), sa.ForeignKey("loans.id"), nullable=False),
        sa.Column("amount", sa.Numeric(18,2), nullable=False), sa.Column("payment_id", sa.Integer(), sa.ForeignKey("payments.id"), unique=True),
        sa.Column("posting_status", sa.String(20), nullable=False, server_default="PENDING"), sa.Column("posting_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("collection_sheet_id", "loan_id", name="uq_collection_sheet_loan"))
    op.create_index("ix_collection_sheet_items_collection_sheet_id", "collection_sheet_items", ["collection_sheet_id"])
    op.create_index("ix_collection_sheet_items_loan_id", "collection_sheet_items", ["loan_id"])
    op.create_table("collection_sheet_expenses",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("collection_sheet_id", sa.Integer(), sa.ForeignKey("collection_sheets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expense_account_id", sa.Integer(), sa.ForeignKey("accounting_accounts.id"), nullable=False), sa.Column("amount", sa.Numeric(18,2), nullable=False),
        sa.Column("description", sa.String(255), nullable=False), sa.Column("reference", sa.String(120)),
        sa.Column("journal_entry_id", sa.Integer(), sa.ForeignKey("accounting_journal_entries.id"), unique=True),
        sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("updated_at", sa.DateTime(), nullable=False))
    op.create_index("ix_collection_sheet_expenses_collection_sheet_id", "collection_sheet_expenses", ["collection_sheet_id"])


def downgrade():
    op.drop_table("collection_sheet_expenses")
    op.drop_table("collection_sheet_items")
    op.drop_table("collection_sheets")
