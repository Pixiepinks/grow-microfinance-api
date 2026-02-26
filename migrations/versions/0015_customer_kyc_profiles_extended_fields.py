"""Add extended flat fields to customer_kyc_profiles and enforce uniqueness

Revision ID: 0015
Revises: 0014
Create Date: 2026-02-26 00:00:00.000000
"""

from alembic import op


revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        ALTER TABLE customer_kyc_profiles
        ADD COLUMN IF NOT EXISTS permanent_address_line1 VARCHAR(255),
        ADD COLUMN IF NOT EXISTS permanent_address_line2 VARCHAR(255),
        ADD COLUMN IF NOT EXISTS permanent_city VARCHAR(100),
        ADD COLUMN IF NOT EXISTS permanent_district VARCHAR(100),
        ADD COLUMN IF NOT EXISTS permanent_province VARCHAR(100),
        ADD COLUMN IF NOT EXISTS permanent_postal_code VARCHAR(20),
        ADD COLUMN IF NOT EXISTS current_address_line1 VARCHAR(255),
        ADD COLUMN IF NOT EXISTS current_address_line2 VARCHAR(255),
        ADD COLUMN IF NOT EXISTS current_city VARCHAR(100),
        ADD COLUMN IF NOT EXISTS current_district VARCHAR(100),
        ADD COLUMN IF NOT EXISTS current_province VARCHAR(100),
        ADD COLUMN IF NOT EXISTS current_postal_code VARCHAR(20),
        ADD COLUMN IF NOT EXISTS current_address_since VARCHAR(10),
        ADD COLUMN IF NOT EXISTS employer_name VARCHAR(255),
        ADD COLUMN IF NOT EXISTS employer_address VARCHAR(255),
        ADD COLUMN IF NOT EXISTS occupation VARCHAR(100),
        ADD COLUMN IF NOT EXISTS monthly_income NUMERIC(12,2),
        ADD COLUMN IF NOT EXISTS business_name VARCHAR(255),
        ADD COLUMN IF NOT EXISTS business_address VARCHAR(255),
        ADD COLUMN IF NOT EXISTS guarantor_name VARCHAR(255),
        ADD COLUMN IF NOT EXISTS guarantor_relationship VARCHAR(100),
        ADD COLUMN IF NOT EXISTS guarantor_mobile VARCHAR(30),
        ADD COLUMN IF NOT EXISTS consent_data_processing BOOLEAN,
        ADD COLUMN IF NOT EXISTS consent_credit_checks BOOLEAN;
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
    op.drop_column("customer_kyc_profiles", "consent_credit_checks")
    op.drop_column("customer_kyc_profiles", "consent_data_processing")
    op.drop_column("customer_kyc_profiles", "guarantor_mobile")
    op.drop_column("customer_kyc_profiles", "guarantor_relationship")
    op.drop_column("customer_kyc_profiles", "guarantor_name")
    op.drop_column("customer_kyc_profiles", "business_address")
    op.drop_column("customer_kyc_profiles", "business_name")
    op.drop_column("customer_kyc_profiles", "monthly_income")
    op.drop_column("customer_kyc_profiles", "occupation")
    op.drop_column("customer_kyc_profiles", "employer_address")
    op.drop_column("customer_kyc_profiles", "employer_name")
    op.drop_column("customer_kyc_profiles", "current_address_since")
    op.drop_column("customer_kyc_profiles", "current_postal_code")
    op.drop_column("customer_kyc_profiles", "current_province")
    op.drop_column("customer_kyc_profiles", "current_district")
    op.drop_column("customer_kyc_profiles", "current_city")
    op.drop_column("customer_kyc_profiles", "current_address_line2")
    op.drop_column("customer_kyc_profiles", "current_address_line1")
    op.drop_column("customer_kyc_profiles", "permanent_postal_code")
    op.drop_column("customer_kyc_profiles", "permanent_province")
    op.drop_column("customer_kyc_profiles", "permanent_district")
    op.drop_column("customer_kyc_profiles", "permanent_city")
    op.drop_column("customer_kyc_profiles", "permanent_address_line2")
    op.drop_column("customer_kyc_profiles", "permanent_address_line1")
