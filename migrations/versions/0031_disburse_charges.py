"""loan disbursement charges and net proceeds

Revision ID: 0031_disburse_charges
Revises: 0030_payment_acct
Create Date: 2026-07-15
"""
from alembic import op
import sqlalchemy as sa

revision = "0031_disburse_charges"
down_revision = "0030_payment_acct"
branch_labels = None
depends_on = None


def _has_table(bind, name):
    return sa.inspect(bind).has_table(name)

def _has_column(bind, table, column):
    if not _has_table(bind, table):
        return False
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}

def _add_col(bind, table, col):
    if _has_table(bind, table) and not _has_column(bind, table, col.name):
        op.add_column(table, col)

def _seed_setting(bind, key, value):
    if _has_table(bind, "accounting_settings"):
        bind.execute(sa.text("insert into accounting_settings (setting_key, setting_value, created_at, updated_at) values (:k,:v, now(), now()) on conflict (setting_key) do nothing"), {"k": key, "v": value})

def upgrade():
    bind = op.get_bind()
    _add_col(bind, "loans", sa.Column("gross_principal_amount", sa.Numeric(18, 2), nullable=True))
    _add_col(bind, "loans", sa.Column("total_disbursement_deductions", sa.Numeric(18, 2), nullable=False, server_default="0"))
    _add_col(bind, "loans", sa.Column("net_disbursed_amount", sa.Numeric(18, 2), nullable=True))
    _add_col(bind, "loans", sa.Column("disbursement_charge_count", sa.Integer(), nullable=False, server_default="0"))
    _add_col(bind, "loans", sa.Column("disbursement_deductions_posted", sa.Boolean(), nullable=False, server_default=sa.false()))
    if _has_table(bind, "loans"):
        bind.execute(sa.text("update loans set gross_principal_amount = coalesce(gross_principal_amount, principal_amount), total_disbursement_deductions = coalesce(total_disbursement_deductions, 0), net_disbursed_amount = coalesce(net_disbursed_amount, principal_amount)"))
    _add_col(bind, "loan_applications", sa.Column("proposed_disbursement_deductions", sa.JSON(), nullable=True))
    _add_col(bind, "loan_applications", sa.Column("estimated_total_deductions", sa.Numeric(18, 2), nullable=False, server_default="0"))
    _add_col(bind, "loan_applications", sa.Column("estimated_net_disbursement", sa.Numeric(18, 2), nullable=True))
    if not _has_table(bind, "disbursement_charge_types"):
        op.create_table("disbursement_charge_types",
            sa.Column("id", sa.Integer(), primary_key=True), sa.Column("code", sa.String(50), nullable=False), sa.Column("name", sa.String(150), nullable=False), sa.Column("description", sa.Text()), sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("default_amount", sa.Numeric(18, 2)), sa.Column("default_rate", sa.Numeric(9, 4)), sa.Column("calculation_method", sa.String(40), nullable=False), sa.Column("accounting_treatment", sa.String(40), nullable=False), sa.Column("income_account_id", sa.Integer(), sa.ForeignKey("accounting_accounts.id")), sa.Column("payable_account_id", sa.Integer(), sa.ForeignKey("accounting_accounts.id")), sa.Column("expense_account_id", sa.Integer(), sa.ForeignKey("accounting_accounts.id")), sa.Column("tax_payable_account_id", sa.Integer(), sa.ForeignKey("accounting_accounts.id")), sa.Column("tax_rate", sa.Numeric(9, 4)), sa.Column("tax_method", sa.String(30), nullable=False, server_default="NO_TAX"), sa.Column("included_in_principal", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("deducted_from_disbursement", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("refundable", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"), sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()), sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()), sa.CheckConstraint("calculation_method in ('FIXED_AMOUNT','PERCENTAGE_OF_PRINCIPAL','MANUAL_AMOUNT')", name="ck_disb_charge_calc_method"), sa.CheckConstraint("accounting_treatment in ('INCOME','PAYABLE','EXPENSE_RECOVERY','TAX','OTHER')", name="ck_disb_charge_acct_treatment"), sa.CheckConstraint("tax_method in ('NO_TAX','TAX_EXCLUSIVE','TAX_INCLUSIVE')", name="ck_disb_charge_tax_method"))
        op.create_index("ix_disbursement_charge_types_code", "disbursement_charge_types", ["code"], unique=True)
    if not _has_table(bind, "loan_disbursement_deductions"):
        op.create_table("loan_disbursement_deductions",
            sa.Column("id", sa.Integer(), primary_key=True), sa.Column("loan_id", sa.Integer(), sa.ForeignKey("loans.id"), nullable=False), sa.Column("loan_application_id", sa.Integer(), sa.ForeignKey("loan_applications.id")), sa.Column("charge_type_id", sa.Integer(), sa.ForeignKey("disbursement_charge_types.id"), nullable=False), sa.Column("description", sa.Text(), nullable=False), sa.Column("gross_amount", sa.Numeric(18, 2), nullable=False), sa.Column("tax_amount", sa.Numeric(18, 2), nullable=False, server_default="0"), sa.Column("net_charge_amount", sa.Numeric(18, 2), nullable=False), sa.Column("calculation_method", sa.String(40), nullable=False), sa.Column("rate", sa.Numeric(9, 4)), sa.Column("accounting_treatment", sa.String(40), nullable=False), sa.Column("destination_account_id", sa.Integer(), sa.ForeignKey("accounting_accounts.id"), nullable=False), sa.Column("tax_account_id", sa.Integer(), sa.ForeignKey("accounting_accounts.id")), sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"), sa.Column("journal_entry_id", sa.Integer(), sa.ForeignKey("accounting_journal_entries.id")), sa.Column("reversed_at", sa.DateTime()), sa.Column("reversal_journal_id", sa.Integer(), sa.ForeignKey("accounting_journal_entries.id")), sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id")), sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()), sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()), sa.CheckConstraint("status in ('DRAFT','POSTED','REVERSED','WAIVED')", name="ck_loan_disb_deduction_status"))
        op.create_index("ix_loan_disb_deduction_loan_id", "loan_disbursement_deductions", ["loan_id"])
        op.create_index("ix_loan_disb_deduction_charge_type_id", "loan_disbursement_deductions", ["charge_type_id"])
        op.create_index("ix_loan_disb_deduction_status", "loan_disbursement_deductions", ["status"])
    for k,v in {"default_documentation_fee_account":"4030","default_processing_fee_account":"4020","default_insurance_payable_account":"2200","default_stamp_duty_payable_account":"2210","default_tax_payable_account":"2220","allow_manual_disbursement_charges":"true","require_disbursement_charge_approval":"false","allow_zero_net_disbursement":"false","allow_deductions_exceeding_principal":"false","default_charge_tax_method":"NO_TAX","show_charges_on_customer_receipt":"true"}.items(): _seed_setting(bind,k,v)

def downgrade():
    bind = op.get_bind()
    if _has_table(bind, "loan_disbursement_deductions"):
        op.drop_table("loan_disbursement_deductions")
    if _has_table(bind, "disbursement_charge_types"):
        op.drop_table("disbursement_charge_types")
