"""Create customer_documents table

Revision ID: 0011
Revises: 0010
Create Date: 2025-02-18 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "customer_documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("document_type", sa.String(length=64), nullable=False),
        sa.Column("file_path", sa.String(length=512), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], name="fk_customer_documents_customer_id"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_customer_documents_customer_id",
        "customer_documents",
        ["customer_id"],
        unique=False,
    )


def downgrade():
    op.drop_index("ix_customer_documents_customer_id", table_name="customer_documents")
    op.drop_table("customer_documents")
