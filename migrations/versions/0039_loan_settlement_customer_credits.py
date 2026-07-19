"""add automatic loan settlement and customer overpayment credits

Revision ID: 0039_loan_settlement_customer_credits
Revises: 0038_inv_accr_cal_days
"""
from alembic import op
import sqlalchemy as sa

revision = "0039_loan_settlement_customer_credits"
down_revision = "0038_inv_accr_cal_days"
branch_labels = None
depends_on = None


def _columns(table):
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    columns = _columns("loans")
    additions = [
        ("settled_at", sa.DateTime()), ("settled_date", sa.Date()),
        ("settled_by_id", sa.Integer()), ("settlement_payment_id", sa.Integer()),
        ("settlement_journal_id", sa.Integer()), ("settlement_reason", sa.String(50)),
        ("customer_credit_balance", sa.Numeric(18, 2),),
    ]
    for item in additions:
        if item[0] not in columns:
            op.add_column("loans", sa.Column(item[0], *item[1:], nullable=True, server_default="0.00" if item[0] == "customer_credit_balance" else None))
    op.create_table("customer_credit_balances",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("loan_id", sa.Integer()), sa.Column("payment_id", sa.Integer()), sa.Column("credit_number", sa.String(40), nullable=False),
        sa.Column("credit_date", sa.Date(), nullable=False), sa.Column("source_type", sa.String(50), nullable=False), sa.Column("source_id", sa.String(64), nullable=False),
        sa.Column("original_amount", sa.Numeric(18,2), nullable=False), sa.Column("available_amount", sa.Numeric(18,2), nullable=False),
        sa.Column("applied_amount", sa.Numeric(18,2), nullable=False, server_default="0.00"), sa.Column("refunded_amount", sa.Numeric(18,2), nullable=False, server_default="0.00"),
        sa.Column("status", sa.String(30), nullable=False, server_default="AVAILABLE"), sa.Column("reference", sa.String(120)), sa.Column("remarks", sa.Text()),
        sa.Column("journal_entry_id", sa.Integer()), sa.Column("created_by_id", sa.Integer()), sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["customer_id"],["customers.id"]), sa.ForeignKeyConstraint(["loan_id"],["loans.id"]), sa.ForeignKeyConstraint(["payment_id"],["payments.id"]),
        sa.UniqueConstraint("source_type", "source_id", name="uq_customer_credit_source"), sa.UniqueConstraint("payment_id"), sa.UniqueConstraint("credit_number"),
    )
    op.create_index("ix_customer_credit_balances_customer_id", "customer_credit_balances", ["customer_id"])
    # Deployments may use a different existing liability code; setting is deliberately explicit.
    op.execute("INSERT INTO accounting_accounts (account_code, account_name, account_type, normal_balance, account_subtype, is_active, allow_manual_posting, is_system_account, cash_flow_category, created_at, updated_at) SELECT '2250', 'Customer Advances / Credit Balances', 'LIABILITY', 'CREDIT', 'CUSTOMER_ADVANCE', 1, 1, 1, 'NONE', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP WHERE NOT EXISTS (SELECT 1 FROM accounting_accounts WHERE account_code = '2250')")
    op.execute("INSERT INTO accounting_settings (setting_key, setting_value, created_at, updated_at) SELECT 'customer_advance_liability_account_id', CAST(id AS TEXT), CURRENT_TIMESTAMP, CURRENT_TIMESTAMP FROM accounting_accounts WHERE account_code = '2250' AND NOT EXISTS (SELECT 1 FROM accounting_settings WHERE setting_key = 'customer_advance_liability_account_id')")


def downgrade():
    op.drop_table("customer_credit_balances")
