"""Add customer KYC profiles table

Revision ID: 0013
Revises: 0012
Create Date: 2026-02-10 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "customer_kyc_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("date_of_birth", sa.Date(), nullable=True),
        sa.Column("civil_status", sa.String(length=50), nullable=True),
        sa.Column("permanent_address", sa.JSON(), nullable=True),
        sa.Column("current_address", sa.JSON(), nullable=True),
        sa.Column("household_size", sa.Integer(), nullable=True),
        sa.Column("dependents_count", sa.Integer(), nullable=True),
        sa.Column("customer_type", sa.String(length=50), nullable=True),
        sa.Column("employment", sa.JSON(), nullable=True),
        sa.Column("business", sa.JSON(), nullable=True),
        sa.Column("guarantor", sa.JSON(), nullable=True),
        sa.Column("consents", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("customer_id"),
    )


def downgrade():
    op.drop_table("customer_kyc_profiles")
