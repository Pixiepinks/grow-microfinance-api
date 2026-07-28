"""bank reconciliation at journal-line level

Revision ID: 0048_bank_reconciliation
Revises: 0047_collection_sheet_clearance
"""
from alembic import op
import sqlalchemy as sa

revision = "0048_bank_reconciliation"
down_revision = "0047_collection_sheet_clearance"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("bank_reconciliations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("reconciliation_number", sa.String(40), nullable=False),
        sa.Column("bank_account_id", sa.Integer(), sa.ForeignKey("accounting_accounts.id"), nullable=False),
        sa.Column("statement_date_from", sa.Date(), nullable=False), sa.Column("statement_date_to", sa.Date(), nullable=False),
        sa.Column("statement_opening_balance", sa.Numeric(18, 2), nullable=False), sa.Column("statement_closing_balance", sa.Numeric(18, 2), nullable=False),
        sa.Column("gl_opening_balance", sa.Numeric(18, 2), nullable=False, server_default="0"), sa.Column("gl_closing_balance", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("total_reconciled_debits", sa.Numeric(18, 2), nullable=False, server_default="0"), sa.Column("total_reconciled_credits", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("total_unreconciled_debits", sa.Numeric(18, 2), nullable=False, server_default="0"), sa.Column("total_unreconciled_credits", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"), sa.Column("notes", sa.Text()),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id")), sa.Column("approved_by_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()), sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()), sa.Column("completed_at", sa.DateTime()),
        sa.CheckConstraint("status in ('DRAFT','IN_PROGRESS','COMPLETED','REOPENED','CANCELLED')", name="ck_bank_reconciliations_status"),
        sa.UniqueConstraint("reconciliation_number", name="uq_bank_reconciliation_number"))
    op.create_index("ix_bank_reconciliations_account", "bank_reconciliations", ["bank_account_id"])
    op.create_index("ix_bank_reconciliations_number", "bank_reconciliations", ["reconciliation_number"])

    with op.batch_alter_table("accounting_journal_lines") as batch:
        batch.add_column(sa.Column("is_reconciled", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("reconciled_at", sa.DateTime()))
        batch.add_column(sa.Column("reconciled_date", sa.Date()))
        batch.add_column(sa.Column("reconciled_by_id", sa.Integer()))
        batch.add_column(sa.Column("bank_reconciliation_id", sa.Integer()))
        batch.add_column(sa.Column("bank_statement_reference", sa.String(255)))
        batch.add_column(sa.Column("reconciliation_note", sa.Text()))
        batch.create_foreign_key("fk_jline_reconciled_by", "users", ["reconciled_by_id"], ["id"])
        batch.create_foreign_key("fk_jline_bank_reconciliation", "bank_reconciliations", ["bank_reconciliation_id"], ["id"])
        batch.create_index("ix_jline_is_reconciled", ["is_reconciled"])
        batch.create_index("ix_jline_bank_reconciliation", ["bank_reconciliation_id"])

    op.create_table("bank_reconciliation_audits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bank_reconciliation_id", sa.Integer(), sa.ForeignKey("bank_reconciliations.id"), nullable=False),
        sa.Column("action", sa.String(30), nullable=False), sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("bank_account_id", sa.Integer(), sa.ForeignKey("accounting_accounts.id"), nullable=False),
        sa.Column("journal_line_id", sa.Integer(), sa.ForeignKey("accounting_journal_lines.id")),
        sa.Column("reconciliation_number", sa.String(40), nullable=False), sa.Column("reason", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()))
    op.create_index("ix_bank_rec_audits_reconciliation", "bank_reconciliation_audits", ["bank_reconciliation_id"])


def downgrade():
    op.drop_table("bank_reconciliation_audits")
    with op.batch_alter_table("accounting_journal_lines") as batch:
        batch.drop_index("ix_jline_bank_reconciliation"); batch.drop_index("ix_jline_is_reconciled")
        batch.drop_constraint("fk_jline_bank_reconciliation", type_="foreignkey"); batch.drop_constraint("fk_jline_reconciled_by", type_="foreignkey")
        for name in ("reconciliation_note", "bank_statement_reference", "bank_reconciliation_id", "reconciled_by_id", "reconciled_date", "reconciled_at", "is_reconciled"):
            batch.drop_column(name)
    op.drop_table("bank_reconciliations")
