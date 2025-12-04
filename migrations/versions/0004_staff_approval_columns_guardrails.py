"""Ensure staff approval columns exist"""

from alembic import op
import sqlalchemy as sa


revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


STAFF_APPROVED_BY_FK = 'fk_loan_applications_staff_approved_by_id_users'

def _column_exists(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(col['name'] == column_name for col in inspector.get_columns(table_name))


def _fk_exists(table_name: str, fk_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(fk['name'] == fk_name for fk in inspector.get_foreign_keys(table_name))


def upgrade():
    with op.batch_alter_table('loan_applications') as batch_op:
        if not _column_exists('loan_applications', 'staff_approved_at'):
            batch_op.add_column(sa.Column('staff_approved_at', sa.DateTime(timezone=True), nullable=True))
        if not _column_exists('loan_applications', 'staff_approved_by_id'):
            batch_op.add_column(sa.Column('staff_approved_by_id', sa.Integer(), nullable=True))

    if not _fk_exists('loan_applications', STAFF_APPROVED_BY_FK):
        if _column_exists('loan_applications', 'staff_approved_by_id'):
            op.create_foreign_key(
                STAFF_APPROVED_BY_FK,
                'loan_applications',
                'users',
                ['staff_approved_by_id'],
                ['id'],
            )


def downgrade():
    if _fk_exists('loan_applications', STAFF_APPROVED_BY_FK):
        op.drop_constraint(STAFF_APPROVED_BY_FK, 'loan_applications', type_='foreignkey')

    with op.batch_alter_table('loan_applications') as batch_op:
        if _column_exists('loan_applications', 'staff_approved_by_id'):
            batch_op.drop_column('staff_approved_by_id')
        if _column_exists('loan_applications', 'staff_approved_at'):
            batch_op.drop_column('staff_approved_at')
