"""Add staff approval metadata to loan applications"""

from alembic import op
import sqlalchemy as sa


revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('loan_applications', sa.Column('staff_approved_at', sa.DateTime(), nullable=True))
    op.add_column('loan_applications', sa.Column('staff_approved_by_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_loan_applications_staff_approved_by_id_users',
        'loan_applications',
        'users',
        ['staff_approved_by_id'],
        ['id'],
    )


def downgrade():
    op.drop_constraint(
        'fk_loan_applications_staff_approved_by_id_users',
        'loan_applications',
        type_='foreignkey',
    )
    op.drop_column('loan_applications', 'staff_approved_by_id')
    op.drop_column('loan_applications', 'staff_approved_at')
