"""Ensure customer_kyc_profiles JSONB fields and unique customer constraint

Revision ID: 0014
Revises: 0013
Create Date: 2026-02-25 00:00:00.000000
"""

from alembic import op


revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


JSONB_COLUMNS = [
    "permanent_address",
    "current_address",
    "employment",
    "business",
    "guarantor",
    "consents",
]


def upgrade():
    for column in JSONB_COLUMNS:
        op.execute(
            f"""
            ALTER TABLE customer_kyc_profiles
            ALTER COLUMN {column}
            TYPE JSONB
            USING {column}::jsonb
            """
        )

    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'customer_kyc_profiles_customer_id_key'
                  AND conrelid = 'customer_kyc_profiles'::regclass
            ) THEN
                ALTER TABLE customer_kyc_profiles
                ADD CONSTRAINT customer_kyc_profiles_customer_id_key UNIQUE (customer_id);
            END IF;
        END
        $$;
        """
    )


def downgrade():
    op.execute(
        """
        ALTER TABLE customer_kyc_profiles
        DROP CONSTRAINT IF EXISTS customer_kyc_profiles_customer_id_key
        """
    )

    for column in JSONB_COLUMNS:
        op.execute(
            f"""
            ALTER TABLE customer_kyc_profiles
            ALTER COLUMN {column}
            TYPE JSON
            USING {column}::json
            """
        )
