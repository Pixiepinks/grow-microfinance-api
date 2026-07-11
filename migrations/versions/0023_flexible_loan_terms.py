"""Add flexible approved loan terms.

Revision ID: 0023_flexible_loan_terms
Revises: 0022_core_accounts
Create Date: 2026-07-11
"""
from alembic import op
import sqlalchemy as sa

revision = "0023_flexible_loan_terms"
down_revision = "0022_core_accounts"
branch_labels = None
depends_on = None


def _add_column_if_missing(table_name, column):
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {col["name"] for col in inspector.get_columns(table_name)}
    if column.name not in existing:
        op.add_column(table_name, column)


def upgrade():
    _add_column_if_missing("loan_applications", sa.Column("loan_days", sa.Integer(), nullable=True))
    _add_column_if_missing("loan_applications", sa.Column("repayment_frequency", sa.String(length=20), nullable=True))
    _add_column_if_missing("loan_applications", sa.Column("number_of_installments", sa.Integer(), nullable=True))
    _add_column_if_missing("loan_applications", sa.Column("installment_amount", sa.Numeric(12, 2), nullable=True))
    _add_column_if_missing("loan_applications", sa.Column("total_repayment", sa.Numeric(12, 2), nullable=True))
    _add_column_if_missing("loan_applications", sa.Column("total_interest", sa.Numeric(12, 2), nullable=True))
    _add_column_if_missing("loan_applications", sa.Column("interest_type", sa.String(length=20), nullable=True))
    _add_column_if_missing("loans", sa.Column("loan_days", sa.Integer(), nullable=True))
    _add_column_if_missing("loans", sa.Column("repayment_frequency", sa.String(length=20), nullable=True))
    _add_column_if_missing("loans", sa.Column("number_of_installments", sa.Integer(), nullable=True))
    _add_column_if_missing("loans", sa.Column("installment_amount", sa.Numeric(12, 2), nullable=True))
    _add_column_if_missing("loans", sa.Column("total_repayment", sa.Numeric(12, 2), nullable=True))
    _add_column_if_missing("loans", sa.Column("total_interest", sa.Numeric(12, 2), nullable=True))
    _add_column_if_missing("loans", sa.Column("interest_type", sa.String(length=20), nullable=True))
    _add_column_if_missing("loans", sa.Column("maturity_date", sa.Date(), nullable=True))
    _add_column_if_missing("loans", sa.Column("final_installment_due_date", sa.Date(), nullable=True))

    op.execute("UPDATE loans SET loan_days = total_days WHERE loan_days IS NULL AND total_days IS NOT NULL")
    op.execute("UPDATE loans SET total_repayment = total_payable WHERE total_repayment IS NULL AND total_payable IS NOT NULL")
    op.execute("UPDATE loans SET installment_amount = daily_installment WHERE installment_amount IS NULL AND daily_installment IS NOT NULL")
    op.execute("UPDATE loans SET maturity_date = end_date WHERE maturity_date IS NULL AND end_date IS NOT NULL")


def downgrade():
    pass
