"""customer search indexes

Revision ID: 0038_customer_search_indexes
Revises: 0037_manual_journal_workflow
Create Date: 2026-07-18
"""
from alembic import op
import sqlalchemy as sa

revision = "0038_customer_search_indexes"
down_revision = "0037_manual_journal_workflow"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("customers") as batch:
        batch.create_index("ix_customers_nic_number", ["nic_number"])
        batch.create_index("ix_customers_mobile", ["mobile"])
    op.create_index("ix_customers_lower_full_name", "customers", [sa.text("lower(full_name)")])


def downgrade():
    op.drop_index("ix_customers_lower_full_name", table_name="customers")
    with op.batch_alter_table("customers") as batch:
        batch.drop_index("ix_customers_mobile")
        batch.drop_index("ix_customers_nic_number")
