"""add controlled early loan settlement concessions

Revision ID: 0040_early_loan_settlement
Revises: 0039_loan_settlement
"""
from alembic import op
import sqlalchemy as sa
revision='0040_early_loan_settlement'; down_revision='0039_loan_settlement'; branch_labels=None; depends_on=None

def upgrade():
    op.create_table('loan_early_settlements',
      sa.Column('id',sa.Integer,primary_key=True),sa.Column('settlement_number',sa.String(60),nullable=False,unique=True),sa.Column('loan_id',sa.Integer,sa.ForeignKey('loans.id'),nullable=False,index=True),sa.Column('customer_id',sa.Integer,sa.ForeignKey('customers.id'),nullable=False),sa.Column('settlement_date',sa.Date,nullable=False),sa.Column('requested_at',sa.DateTime,nullable=False),sa.Column('requested_by_id',sa.Integer,sa.ForeignKey('users.id')),sa.Column('approved_at',sa.DateTime),sa.Column('approved_by_id',sa.Integer,sa.ForeignKey('users.id')),
      *[sa.Column(n,sa.Numeric(18,2),nullable=False,server_default='0.00') for n in ('original_principal','original_total_payable','total_paid_before_settlement','principal_outstanding_before','accrued_interest_outstanding_before','future_unearned_interest_before','penalty_outstanding_before','delay_interest_outstanding_before','fee_outstanding_before','approved_interest_rebate','future_interest_rebate','accrued_interest_rebate','approved_penalty_waiver','final_settlement_amount')],
      sa.Column('settlement_payment_id',sa.Integer,sa.ForeignKey('payments.id')),sa.Column('rebate_journal_entry_id',sa.Integer,sa.ForeignKey('accounting_journal_entries.id')),sa.Column('settlement_journal_entry_id',sa.Integer,sa.ForeignKey('accounting_journal_entries.id')),sa.Column('approval_reference',sa.String(120)),sa.Column('reason',sa.Text),sa.Column('status',sa.String(20),nullable=False),sa.Column('created_at',sa.DateTime,nullable=False),sa.Column('updated_at',sa.DateTime,nullable=False))
    with op.batch_alter_table('loans') as b:
      for n,t in [('settlement_type',sa.String(50)),('early_settlement_id',sa.Integer),('interest_rebate_amount',sa.Numeric(18,2)),('penalty_waiver_amount',sa.Numeric(18,2)),('outstanding_amount',sa.Numeric(18,2))]: b.add_column(sa.Column(n,t))
    with op.batch_alter_table('loan_ledger') as b:
      for n,t in [('waived_interest_amount',sa.Numeric(18,2)),('waived_penalty_amount',sa.Numeric(18,2)),('waiver_reason',sa.String(100)),('early_settlement_id',sa.Integer),('original_interest_amount',sa.Numeric(18,2)),('revised_interest_amount',sa.Numeric(18,2))]: b.add_column(sa.Column(n,t))
    op.execute("INSERT INTO accounting_accounts (account_code,account_name,account_type,normal_balance,account_subtype,is_active,allow_manual_posting,is_system_account,cash_flow_category,created_at,updated_at) SELECT '5060','Interest Rebate / Loan Concession Expense','EXPENSE','DEBIT','OPERATING_EXPENSE',TRUE,TRUE,TRUE,'OPERATING_EXPENSE',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP WHERE NOT EXISTS (SELECT 1 FROM accounting_accounts WHERE account_code='5060')")
    op.execute("INSERT INTO accounting_settings (setting_key,setting_value,created_at,updated_at) SELECT 'interest_rebate_expense_account_id',CAST(id AS TEXT),CURRENT_TIMESTAMP,CURRENT_TIMESTAMP FROM accounting_accounts WHERE account_code='5060' AND NOT EXISTS (SELECT 1 FROM accounting_settings WHERE setting_key='interest_rebate_expense_account_id')")
    for key,value in [('early_settlement_enabled','true'),('early_settlement_requires_approval','false'),('maximum_automatic_interest_rebate_percent','0')]: op.execute(f"INSERT INTO accounting_settings (setting_key,setting_value,created_at,updated_at) SELECT '{key}','{value}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP WHERE NOT EXISTS (SELECT 1 FROM accounting_settings WHERE setting_key='{key}')")
def downgrade():
    op.drop_table('loan_early_settlements')
