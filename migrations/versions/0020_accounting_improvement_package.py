"""Accounting improvement package settings and subtypes

Revision ID: 0020
Revises: 0019
Create Date: 2026-07-11 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

SUBTYPE_SQL = "'CASH','BANK','LOAN_RECEIVABLE','INTEREST_RECEIVABLE','PENALTY_RECEIVABLE','OTHER_CURRENT_ASSET','FIXED_ASSET','ACCOUNTS_PAYABLE','BORROWING','CAPITAL','RETAINED_EARNINGS','INTEREST_INCOME','PENALTY_INCOME','FEE_INCOME','OPERATING_EXPENSE','WRITE_OFF_EXPENSE','SUSPENSE','OTHER'"

def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("accounting_accounts")}
    if "account_subtype" not in cols:
        op.add_column("accounting_accounts", sa.Column("account_subtype", sa.String(40), nullable=False, server_default="OTHER"))
    pay_cols = {c["name"] for c in insp.get_columns("payments")}
    if "transaction_reference" not in pay_cols:
        op.add_column("payments", sa.Column("transaction_reference", sa.String(120)))
    if "receipt_account_id" not in pay_cols:
        op.add_column("payments", sa.Column("receipt_account_id", sa.Integer(), sa.ForeignKey("accounting_accounts.id")))
    for code, typ, subtype, system in [
        ("1000", "ASSET", "CASH", True), ("1010", "ASSET", "BANK", True),
        ("1100", "ASSET", "LOAN_RECEIVABLE", True), ("1110", "ASSET", "INTEREST_RECEIVABLE", True), ("1120", "ASSET", "PENALTY_RECEIVABLE", True),
        ("3100", "EQUITY", "RETAINED_EARNINGS", True), ("4000", "INCOME", "INTEREST_INCOME", True), ("4010", "INCOME", "PENALTY_INCOME", True), ("4020", "INCOME", "FEE_INCOME", True),
        ("5050", "EXPENSE", "WRITE_OFF_EXPENSE", True), ("1990", "ASSET", "SUSPENSE", True),
    ]:
        op.execute(sa.text("UPDATE accounting_accounts SET account_type=:typ, account_subtype=:subtype, is_system_account=:system WHERE account_code=:code").bindparams(code=code, typ=typ, subtype=subtype, system=system))
    for code, name, typ, normal, subtype in [("5050", "Loan Write-off Expense", "EXPENSE", "DEBIT", "WRITE_OFF_EXPENSE"), ("1990", "Suspense Account", "ASSET", "DEBIT", "SUSPENSE")]:
        op.execute(sa.text("""
            INSERT INTO accounting_accounts (account_code, account_name, account_type, normal_balance, cash_flow_category, account_subtype, is_system_account, is_active, allow_manual_posting, created_at, updated_at)
            VALUES (:code, :name, :typ, :normal, 'NONE', :subtype, true, true, true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT (account_code) DO NOTHING
        """).bindparams(code=code, name=name, typ=typ, normal=normal, subtype=subtype))
    for key, code in [
        ("DEFAULT_DISBURSEMENT_ACCOUNT", "1010"), ("DEFAULT_CASH_COLLECTION_ACCOUNT", "1000"), ("DEFAULT_BANK_COLLECTION_ACCOUNT", "1010"),
        ("LOAN_RECEIVABLE_ACCOUNT", "1100"), ("INTEREST_RECEIVABLE_ACCOUNT", "1110"), ("PENALTY_RECEIVABLE_ACCOUNT", "1120"),
        ("INTEREST_INCOME_ACCOUNT", "4000"), ("PENALTY_INCOME_ACCOUNT", "4010"), ("PROCESSING_FEE_INCOME_ACCOUNT", "4020"),
        ("LOAN_WRITE_OFF_EXPENSE_ACCOUNT", "5050"), ("SUSPENSE_ACCOUNT", "1990"), ("RETAINED_EARNINGS_ACCOUNT", "3100")]:
        op.execute(sa.text("""
            INSERT INTO accounting_settings (setting_key, setting_value, created_at, updated_at)
            VALUES (:key, :code, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT (setting_key) DO NOTHING
        """).bindparams(key=key, code=code))

def downgrade():
    op.drop_column("payments", "receipt_account_id")
    op.drop_column("payments", "transaction_reference")
    op.drop_column("accounting_accounts", "account_subtype")
