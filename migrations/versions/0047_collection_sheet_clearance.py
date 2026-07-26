"""link collection-sheet receipts to their existing deposit journal

Revision ID: 0047_collection_sheet_clearance
Revises: 0046_collection_sheets
"""
from alembic import op
import sqlalchemy as sa

revision = "0047_collection_sheet_clearance"
down_revision = "0046_collection_sheets"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("payments", sa.Column("collection_clearance_status", sa.String(30), nullable=False, server_default="UNDEPOSITED"))
    op.add_column("payments", sa.Column("collection_sheet_id", sa.Integer(), sa.ForeignKey("collection_sheets.id")))
    op.add_column("payments", sa.Column("collection_sheet_deposit_journal_id", sa.Integer(), sa.ForeignKey("accounting_journal_entries.id")))
    op.create_index("ix_payments_collection_clearance_status", "payments", ["collection_clearance_status"])
    op.create_index("ix_payments_collection_sheet_id", "payments", ["collection_sheet_id"])


def downgrade():
    op.drop_index("ix_payments_collection_sheet_id", table_name="payments")
    op.drop_index("ix_payments_collection_clearance_status", table_name="payments")
    op.drop_column("payments", "collection_sheet_deposit_journal_id")
    op.drop_column("payments", "collection_sheet_id")
    op.drop_column("payments", "collection_clearance_status")
