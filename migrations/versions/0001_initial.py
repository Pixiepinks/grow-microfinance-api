"""Initial database structure"""

from alembic import op
import sqlalchemy as sa

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('email', sa.String(length=120), nullable=False),
        sa.Column('password_hash', sa.String(length=255), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('role', sa.String(length=20), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email')
    )
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=False)

    op.create_table(
        'customers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('customer_code', sa.String(length=50), nullable=False),
        sa.Column('full_name', sa.String(length=150), nullable=False),
        sa.Column('nic_number', sa.String(length=50), nullable=True),
        sa.Column('mobile', sa.String(length=20), nullable=True),
        sa.Column('address', sa.String(length=255), nullable=True),
        sa.Column('business_type', sa.String(length=120), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('customer_code'),
        sa.UniqueConstraint('user_id')
    )
    op.create_index(op.f('ix_customers_customer_code'), 'customers', ['customer_code'], unique=False)

    op.create_table(
        'loans',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('loan_number', sa.String(length=50), nullable=False),
        sa.Column('customer_id', sa.Integer(), nullable=False),
        sa.Column('principal_amount', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('interest_rate', sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column('total_days', sa.Integer(), nullable=False),
        sa.Column('daily_installment', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('total_payable', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('start_date', sa.Date(), nullable=False),
        sa.Column('end_date', sa.Date(), nullable=False),
        sa.Column('status', sa.String(length=50), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('loan_number')
    )
    op.create_index(op.f('ix_loans_loan_number'), 'loans', ['loan_number'], unique=False)

    op.create_table(
        'payments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('loan_id', sa.Integer(), nullable=False),
        sa.Column('collection_date', sa.Date(), nullable=False),
        sa.Column('amount_collected', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('collected_by_id', sa.Integer(), nullable=False),
        sa.Column('payment_method', sa.String(length=50), nullable=True),
        sa.Column('remarks', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['collected_by_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['loan_id'], ['loans.id'], ),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade():
    op.drop_table('payments')
    op.drop_index(op.f('ix_loans_loan_number'), table_name='loans')
    op.drop_table('loans')
    op.drop_index(op.f('ix_customers_customer_code'), table_name='customers')
    op.drop_table('customers')
    op.drop_index(op.f('ix_users_email'), table_name='users')
    op.drop_table('users')
