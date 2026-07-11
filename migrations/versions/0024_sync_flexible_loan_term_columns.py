"""Synchronize flexible loan term columns for production.

Revision ID: 0024_sync_loan_terms
Revises: 0023_flexible_loan_terms
Create Date: 2026-07-11
"""
from alembic import op
import sqlalchemy as sa

revision = "0024_sync_loan_terms"
down_revision = "0023_flexible_loan_terms"
branch_labels = None
depends_on = None


def column_exists(table_name, column_name):
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def add_column_if_missing(table_name, column):
    if not column_exists(table_name, column.name):
        op.add_column(table_name, column)


def table_columns(table_name):
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {column["name"] for column in inspector.get_columns(table_name)}


def sync_columns(table_name, columns):
    for column in columns:
        add_column_if_missing(table_name, column)


def upgrade():
    sync_columns(
        "loan_applications",
        [
            sa.Column("loan_days", sa.Integer(), nullable=True),
            sa.Column("term_type", sa.String(length=20), nullable=True),
            sa.Column("term_value", sa.Integer(), nullable=True),
            sa.Column("repayment_frequency", sa.String(length=20), nullable=True),
            sa.Column("number_of_installments", sa.Integer(), nullable=True),
            sa.Column("installment_count", sa.Integer(), nullable=True),
            sa.Column("installment_amount", sa.Numeric(12, 2), nullable=True),
            sa.Column("total_repayment", sa.Numeric(12, 2), nullable=True),
            sa.Column("total_interest", sa.Numeric(12, 2), nullable=True),
            sa.Column("interest_type", sa.String(length=20), nullable=True),
            sa.Column("interest_rate_basis", sa.String(length=20), nullable=True),
        ],
    )
    sync_columns(
        "loans",
        [
            sa.Column("loan_days", sa.Integer(), nullable=True),
            sa.Column("tenure_months", sa.Integer(), nullable=True),
            sa.Column("term_type", sa.String(length=20), nullable=True),
            sa.Column("term_value", sa.Integer(), nullable=True),
            sa.Column("repayment_frequency", sa.String(length=20), nullable=True),
            sa.Column("number_of_installments", sa.Integer(), nullable=True),
            sa.Column("installment_count", sa.Integer(), nullable=True),
            sa.Column("installment_amount", sa.Numeric(12, 2), nullable=True),
            sa.Column("total_repayment", sa.Numeric(12, 2), nullable=True),
            sa.Column("total_interest", sa.Numeric(12, 2), nullable=True),
            sa.Column("interest_type", sa.String(length=20), nullable=True),
            sa.Column("interest_rate_basis", sa.String(length=20), nullable=True),
            sa.Column("maturity_date", sa.Date(), nullable=True),
            sa.Column("final_installment_due_date", sa.Date(), nullable=True),
        ],
    )

    app_cols = table_columns("loan_applications")
    if {"term_type", "term_value", "loan_days"} <= app_cols:
        op.execute("""
            UPDATE loan_applications
               SET term_type = 'DAYS', term_value = loan_days
             WHERE term_type IS NULL AND loan_days IS NOT NULL
        """)
    if {"term_type", "term_value", "tenure_months"} <= app_cols:
        op.execute("""
            UPDATE loan_applications
               SET term_type = 'MONTHS', term_value = tenure_months
             WHERE term_type IS NULL AND tenure_months IS NOT NULL
        """)
    if {"installment_count", "number_of_installments"} <= app_cols:
        op.execute("""
            UPDATE loan_applications
               SET installment_count = number_of_installments
             WHERE installment_count IS NULL AND number_of_installments IS NOT NULL
        """)
        op.execute("""
            UPDATE loan_applications
               SET number_of_installments = installment_count
             WHERE number_of_installments IS NULL AND installment_count IS NOT NULL
        """)
    if {"interest_rate_basis", "interest_type"} <= app_cols:
        op.execute("""
            UPDATE loan_applications
               SET interest_rate_basis = 'FLAT_TERM'
             WHERE interest_rate_basis IS NULL
               AND (interest_type IS NULL OR UPPER(interest_type) = 'FLAT')
        """)

    loan_cols = table_columns("loans")
    if {"loan_days", "total_days"} <= loan_cols:
        op.execute("UPDATE loans SET loan_days = total_days WHERE loan_days IS NULL AND total_days IS NOT NULL")
    if {"term_type", "term_value", "loan_days"} <= loan_cols:
        op.execute("""
            UPDATE loans
               SET term_type = 'DAYS', term_value = loan_days
             WHERE term_type IS NULL AND loan_days IS NOT NULL
        """)
    if {"term_type", "term_value", "tenure_months"} <= loan_cols:
        op.execute("""
            UPDATE loans
               SET term_type = 'MONTHS', term_value = tenure_months
             WHERE term_type IS NULL AND tenure_months IS NOT NULL
        """)
    if {"installment_count", "number_of_installments"} <= loan_cols:
        op.execute("UPDATE loans SET installment_count = number_of_installments WHERE installment_count IS NULL AND number_of_installments IS NOT NULL")
        op.execute("UPDATE loans SET number_of_installments = installment_count WHERE number_of_installments IS NULL AND installment_count IS NOT NULL")
    if {"installment_amount", "daily_installment"} <= loan_cols:
        op.execute("UPDATE loans SET installment_amount = daily_installment WHERE installment_amount IS NULL AND daily_installment IS NOT NULL")
    if {"total_repayment", "total_payable"} <= loan_cols:
        op.execute("UPDATE loans SET total_repayment = total_payable WHERE total_repayment IS NULL AND total_payable IS NOT NULL")
    if {"total_interest", "total_repayment", "principal_amount"} <= loan_cols:
        op.execute("UPDATE loans SET total_interest = total_repayment - principal_amount WHERE total_interest IS NULL AND total_repayment IS NOT NULL AND principal_amount IS NOT NULL")
    if {"maturity_date", "end_date"} <= loan_cols:
        op.execute("UPDATE loans SET maturity_date = end_date WHERE maturity_date IS NULL AND end_date IS NOT NULL")
    if {"final_installment_due_date", "maturity_date"} <= loan_cols:
        op.execute("UPDATE loans SET final_installment_due_date = maturity_date WHERE final_installment_due_date IS NULL AND maturity_date IS NOT NULL")
    if {"interest_rate_basis", "interest_type"} <= loan_cols:
        op.execute("""
            UPDATE loans
               SET interest_rate_basis = 'FLAT_TERM'
             WHERE interest_rate_basis IS NULL
               AND (interest_type IS NULL OR UPPER(interest_type) = 'FLAT')
        """)


def downgrade():
    pass
