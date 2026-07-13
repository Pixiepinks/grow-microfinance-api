"""Payment accounting integrity constraints.

Revision ID: 0030_payment_accounting_integrity
Revises: 0029_expand_acct_types
Create Date: 2026-07-13
"""
from alembic import op
import sqlalchemy as sa

revision = "0030_payment_accounting_integrity"
down_revision = "0029_expand_acct_types"
branch_labels = None
depends_on = None


def _indexes(bind, table):
    return {i.get("name") for i in sa.inspect(bind).get_indexes(table)}


def upgrade():
    bind = op.get_bind()
    indexes = _indexes(bind, "accounting_journal_entries")
    if "uq_journal_source_posted" not in indexes:
        op.create_index(
            "uq_journal_source_posted",
            "accounting_journal_entries",
            ["source_type", "source_id"],
            unique=True,
            postgresql_where=sa.text("source_type is not null and source_id is not null and status != 'REVERSED'"),
        )


def downgrade():
    bind = op.get_bind()
    if "uq_journal_source_posted" in _indexes(bind, "accounting_journal_entries"):
        op.drop_index("uq_journal_source_posted", table_name="accounting_journal_entries")
