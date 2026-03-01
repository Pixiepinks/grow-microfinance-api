"""Drop legacy JSON columns from customer_kyc_profiles

Revision ID: 0016
Revises: 0015
Create Date: 2026-03-01 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


JSON_COLUMNS = [
    "permanent_address",
    "current_address",
    "employment",
    "business",
    "guarantor",
    "consents",
]


def upgrade():
    for column in JSON_COLUMNS:
        op.drop_column("customer_kyc_profiles", column)


def downgrade():
    for column in JSON_COLUMNS:
        op.add_column(
            "customer_kyc_profiles",
            sa.Column(column, postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        )
