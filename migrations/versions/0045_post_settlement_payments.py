"""add post-settlement payment audit fields

Revision ID: 0045_post_settlement_payments
Revises: 0044_customer_master_profile
"""
from alembic import op
import sqlalchemy as sa

revision = "0045_post_settlement_payments"
down_revision = "0044_customer_master_profile"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("payments", sa.Column("transaction_type", sa.String(40), nullable=False, server_default="LOAN_PAYMENT"))
    op.add_column("payments", sa.Column("idempotency_key", sa.String(160), nullable=True))
    op.create_index("ix_payments_transaction_type", "payments", ["transaction_type"])
    op.create_index("ix_payments_idempotency_key", "payments", ["idempotency_key"], unique=True)


def downgrade():
    op.drop_index("ix_payments_idempotency_key", table_name="payments")
    op.drop_index("ix_payments_transaction_type", table_name="payments")
    op.drop_column("payments", "idempotency_key")
    op.drop_column("payments", "transaction_type")
