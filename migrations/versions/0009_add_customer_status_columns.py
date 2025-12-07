"""Add customer status columns with server defaults

Revision ID: 0009
Revises: 0008
Create Date: 2025-01-01 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "customers",
        sa.Column("lead_status", sa.String(length=32), nullable=False, server_default="NEW"),
    )
    op.add_column(
        "customers",
        sa.Column("kyc_status", sa.String(length=32), nullable=False, server_default="PENDING"),
    )
    op.add_column(
        "customers",
        sa.Column(
            "eligibility_status", sa.String(length=32), nullable=False, server_default="UNKNOWN"
        ),
    )


def downgrade():
    op.drop_column("customers", "eligibility_status")
    op.drop_column("customers", "kyc_status")
    op.drop_column("customers", "lead_status")
