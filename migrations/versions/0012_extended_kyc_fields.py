"""Add extended KYC fields to customers

Revision ID: 0012
Revises: 0011
Create Date: 2025-03-05 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("customers", sa.Column("date_of_birth", sa.Date(), nullable=True))
    op.add_column("customers", sa.Column("civil_status", sa.String(length=20), nullable=True))
    op.add_column(
        "customers", sa.Column("permanent_address_line1", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "customers", sa.Column("permanent_address_line2", sa.String(length=255), nullable=True)
    )
    op.add_column("customers", sa.Column("permanent_city", sa.String(length=100), nullable=True))
    op.add_column(
        "customers", sa.Column("permanent_district", sa.String(length=100), nullable=True)
    )
    op.add_column(
        "customers", sa.Column("permanent_province", sa.String(length=100), nullable=True)
    )
    op.add_column(
        "customers", sa.Column("permanent_postal_code", sa.String(length=20), nullable=True)
    )
    op.add_column(
        "customers", sa.Column("current_address_line1", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "customers", sa.Column("current_address_line2", sa.String(length=255), nullable=True)
    )
    op.add_column("customers", sa.Column("current_city", sa.String(length=100), nullable=True))
    op.add_column(
        "customers", sa.Column("current_district", sa.String(length=100), nullable=True)
    )
    op.add_column(
        "customers", sa.Column("current_province", sa.String(length=100), nullable=True)
    )
    op.add_column(
        "customers", sa.Column("current_postal_code", sa.String(length=20), nullable=True)
    )
    op.add_column("customers", sa.Column("current_address_since", sa.String(length=10), nullable=True))
    op.add_column("customers", sa.Column("household_size", sa.Integer(), nullable=True))
    op.add_column("customers", sa.Column("dependents_count", sa.Integer(), nullable=True))
    op.add_column("customers", sa.Column("customer_type", sa.String(length=20), nullable=True))
    op.add_column("customers", sa.Column("employer_name", sa.String(length=255), nullable=True))
    op.add_column("customers", sa.Column("employer_address", sa.String(length=255), nullable=True))
    op.add_column("customers", sa.Column("occupation", sa.String(length=100), nullable=True))
    op.add_column("customers", sa.Column("monthly_income", sa.Numeric(12, 2), nullable=True))
    op.add_column("customers", sa.Column("business_name", sa.String(length=255), nullable=True))
    op.add_column("customers", sa.Column("business_address", sa.String(length=255), nullable=True))
    op.add_column("customers", sa.Column("guarantor_name", sa.String(length=255), nullable=True))
    op.add_column(
        "customers", sa.Column("guarantor_relationship", sa.String(length=100), nullable=True)
    )
    op.add_column("customers", sa.Column("guarantor_mobile", sa.String(length=30), nullable=True))
    op.add_column(
        "customers",
        sa.Column(
            "consent_data_processing", sa.Boolean(), nullable=True, server_default=sa.false()
        ),
    )
    op.add_column(
        "customers",
        sa.Column("consent_credit_checks", sa.Boolean(), nullable=True, server_default=sa.false()),
    )


def downgrade():
    op.drop_column("customers", "consent_credit_checks")
    op.drop_column("customers", "consent_data_processing")
    op.drop_column("customers", "guarantor_mobile")
    op.drop_column("customers", "guarantor_relationship")
    op.drop_column("customers", "guarantor_name")
    op.drop_column("customers", "business_address")
    op.drop_column("customers", "business_name")
    op.drop_column("customers", "monthly_income")
    op.drop_column("customers", "occupation")
    op.drop_column("customers", "employer_address")
    op.drop_column("customers", "employer_name")
    op.drop_column("customers", "customer_type")
    op.drop_column("customers", "dependents_count")
    op.drop_column("customers", "household_size")
    op.drop_column("customers", "current_address_since")
    op.drop_column("customers", "current_postal_code")
    op.drop_column("customers", "current_province")
    op.drop_column("customers", "current_district")
    op.drop_column("customers", "current_city")
    op.drop_column("customers", "current_address_line2")
    op.drop_column("customers", "current_address_line1")
    op.drop_column("customers", "permanent_postal_code")
    op.drop_column("customers", "permanent_province")
    op.drop_column("customers", "permanent_district")
    op.drop_column("customers", "permanent_city")
    op.drop_column("customers", "permanent_address_line2")
    op.drop_column("customers", "permanent_address_line1")
    op.drop_column("customers", "civil_status")
    op.drop_column("customers", "date_of_birth")
