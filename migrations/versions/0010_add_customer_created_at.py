"""Add created_at column to customers

Revision ID: 0010
Revises: 0009
Create Date: 2025-01-01 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "customers",
        sa.Column("created_at", sa.DateTime(), nullable=True, server_default=sa.func.now()),
    )


def downgrade():
    op.drop_column("customers", "created_at")
