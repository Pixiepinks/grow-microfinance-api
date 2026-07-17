"""manual journal workflow optional dimensions

Revision ID: 0037_manual_journal_workflow
Revises: 0036_investor_agreement_seq
Create Date: 2026-07-17
"""
from alembic import op
import sqlalchemy as sa

revision = "0037_manual_journal_workflow"
down_revision = "0036_investor_agreement_seq"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE SEQUENCE IF NOT EXISTS accounting_journal_number_seq")
    with op.batch_alter_table("accounting_accounts") as batch:
        batch.add_column(sa.Column("requires_customer", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("requires_loan", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("allows_customer", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch.add_column(sa.Column("allows_loan", sa.Boolean(), nullable=False, server_default=sa.true()))
    with op.batch_alter_table("accounting_journal_entries") as batch:
        batch.add_column(sa.Column("reference", sa.String(length=120), nullable=True))
        batch.add_column(sa.Column("reversed_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("reversal_journal_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_accounting_journal_entries_reversal_journal_id", "accounting_journal_entries", ["reversal_journal_id"], ["id"])
    with op.batch_alter_table("accounting_journal_lines") as batch:
        batch.add_column(sa.Column("investor_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("investor_agreement_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("collector_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_accounting_journal_lines_collector_id", "users", ["collector_id"], ["id"])


def downgrade():
    with op.batch_alter_table("accounting_journal_lines") as batch:
        batch.drop_constraint("fk_accounting_journal_lines_collector_id", type_="foreignkey")
        batch.drop_column("collector_id")
        batch.drop_column("investor_agreement_id")
        batch.drop_column("investor_id")
    with op.batch_alter_table("accounting_journal_entries") as batch:
        batch.drop_constraint("fk_accounting_journal_entries_reversal_journal_id", type_="foreignkey")
        batch.drop_column("reversal_journal_id")
        batch.drop_column("reversed_at")
        batch.drop_column("reference")
    with op.batch_alter_table("accounting_accounts") as batch:
        batch.drop_column("allows_loan")
        batch.drop_column("allows_customer")
        batch.drop_column("requires_loan")
        batch.drop_column("requires_customer")
