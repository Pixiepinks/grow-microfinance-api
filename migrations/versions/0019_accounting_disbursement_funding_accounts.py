"""Accounting disbursement funding account support

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-10 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("accounting_accounts", sa.Column("cash_flow_category", sa.String(20), nullable=False, server_default="NONE"))
    op.execute("UPDATE accounting_accounts SET cash_flow_category='CASH', is_system_account=true WHERE account_code='1000'")
    op.execute("UPDATE accounting_accounts SET cash_flow_category='BANK', is_system_account=true WHERE account_code='1010'")
    op.execute("UPDATE accounting_accounts SET cash_flow_category='RECEIVABLE', is_system_account=true WHERE account_code IN ('1100','1110','1120')")
    op.execute("UPDATE accounting_accounts SET is_system_account=true WHERE account_code IN ('4000','4010')")
    for key, code in [
        ('DEFAULT_DISBURSEMENT_ACCOUNT','1010'), ('CASH_ACCOUNT','1000'), ('BANK_ACCOUNT','1010'),
        ('LOAN_RECEIVABLE_ACCOUNT','1100'), ('INTEREST_RECEIVABLE_ACCOUNT','1110'), ('PENALTY_RECEIVABLE_ACCOUNT','1120'),
        ('INTEREST_INCOME_ACCOUNT','4000'), ('PENALTY_INCOME_ACCOUNT','4010')
    ]:
        op.execute(sa.text("""
            INSERT INTO accounting_settings (setting_key, setting_value, created_at, updated_at)
            VALUES (:key, :code, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT (setting_key) DO NOTHING
        """).bindparams(key=key, code=code))

def downgrade():
    op.drop_column("accounting_accounts", "cash_flow_category")
