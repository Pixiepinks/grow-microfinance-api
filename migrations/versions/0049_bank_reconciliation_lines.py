"""persist bank reconciliation selections

Revision ID: 0049_bank_reconciliation_lines
Revises: 0048_bank_reconciliation
"""
from alembic import op
import sqlalchemy as sa

revision = "0049_bank_reconciliation_lines"
down_revision = "0048_bank_reconciliation"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("bank_reconciliation_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bank_reconciliation_id", sa.Integer(), sa.ForeignKey("bank_reconciliations.id"), nullable=False),
        sa.Column("journal_line_id", sa.Integer(), sa.ForeignKey("accounting_journal_lines.id"), nullable=False),
        sa.Column("debit", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("credit", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("statement_reference", sa.String(255)), sa.Column("reconciled_date", sa.Date(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("bank_reconciliation_id", "journal_line_id", name="uq_bank_reconciliation_line"),
        sa.UniqueConstraint("journal_line_id", name="uq_bank_reconciliation_journal_line"))
    op.create_index("ix_bank_reconciliation_lines_rec", "bank_reconciliation_lines", ["bank_reconciliation_id"])


def downgrade():
    op.drop_table("bank_reconciliation_lines")
