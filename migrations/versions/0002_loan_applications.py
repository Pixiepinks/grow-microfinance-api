"""Loan applications structure"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import sqlite


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


STREAM_JSON_TYPE = sa.JSON().with_variant(sqlite.JSON(), 'sqlite')


def upgrade():
    op.create_table(
        'loan_applications',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('application_number', sa.String(length=50), nullable=False),
        sa.Column('customer_id', sa.Integer(), nullable=False),
        sa.Column('loan_type', sa.String(length=50), nullable=False),
        sa.Column('status', sa.String(length=50), nullable=False),
        sa.Column('applied_amount', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('tenure_months', sa.Integer(), nullable=False),
        sa.Column('interest_rate', sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column('approved_amount', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('approved_tenure', sa.Integer(), nullable=True),
        sa.Column('review_notes', sa.Text(), nullable=True),
        sa.Column('reject_reason', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('submitted_at', sa.DateTime(), nullable=True),
        sa.Column('approved_at', sa.DateTime(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('assigned_officer_id', sa.Integer(), nullable=True),
        sa.Column('full_name', sa.String(length=150), nullable=False),
        sa.Column('nic_number', sa.String(length=50), nullable=False),
        sa.Column('mobile_number', sa.String(length=20), nullable=False),
        sa.Column('email', sa.String(length=120), nullable=True),
        sa.Column('address_line1', sa.String(length=255), nullable=True),
        sa.Column('address_line2', sa.String(length=255), nullable=True),
        sa.Column('city', sa.String(length=120), nullable=True),
        sa.Column('district', sa.String(length=120), nullable=True),
        sa.Column('province', sa.String(length=120), nullable=True),
        sa.Column('date_of_birth', sa.Date(), nullable=True),
        sa.Column('monthly_income', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('monthly_expenses', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('has_existing_loans', sa.Boolean(), nullable=True),
        sa.Column('existing_loan_details', sa.Text(), nullable=True),
        sa.Column('extra_data', STREAM_JSON_TYPE, nullable=True),
        sa.ForeignKeyConstraint(['assigned_officer_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('application_number')
    )
    op.create_index(op.f('ix_loan_applications_application_number'), 'loan_applications', ['application_number'], unique=False)

    op.create_table(
        'loan_application_documents',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('loan_application_id', sa.Integer(), nullable=False),
        sa.Column('document_type', sa.String(length=50), nullable=False),
        sa.Column('file_path', sa.String(length=255), nullable=False),
        sa.Column('uploaded_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['loan_application_id'], ['loan_applications.id'], ),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade():
    op.drop_table('loan_application_documents')
    op.drop_index(op.f('ix_loan_applications_application_number'), table_name='loan_applications')
    op.drop_table('loan_applications')
