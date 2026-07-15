"""add indexes for disbursement read endpoints

Revision ID: 0032_disburse_read_indexes
Revises: 0031_disburse_charges
Create Date: 2026-07-15
"""
from alembic import op
import sqlalchemy as sa

revision = "0032_disburse_read_indexes"
down_revision = "0031_disburse_charges"
branch_labels = None
depends_on = None


def _has_index(bind, table_name, index_name):
    inspector = sa.inspect(bind)
    return any(idx["name"] == index_name for idx in inspector.get_indexes(table_name))


def _create_index_once(bind, name, table, columns):
    if not _has_index(bind, table, name):
        op.create_index(name, table, columns)


def upgrade():
    bind = op.get_bind()
    _create_index_once(bind, "ix_accounting_accounts_is_active", "accounting_accounts", ["is_active"])
    _create_index_once(bind, "ix_accounting_accounts_account_type", "accounting_accounts", ["account_type"])
    _create_index_once(bind, "ix_accounting_accounts_account_subtype", "accounting_accounts", ["account_subtype"])
    _create_index_once(bind, "ix_accounting_accounts_account_role", "accounting_accounts", ["account_role"])
    _create_index_once(bind, "ix_disbursement_charge_types_active", "disbursement_charge_types", ["active"])
    _create_index_once(bind, "ix_disbursement_charge_types_display_order", "disbursement_charge_types", ["display_order"])
    _create_index_once(bind, "ix_disbursement_charge_types_income_account_id", "disbursement_charge_types", ["income_account_id"])
    _create_index_once(bind, "ix_disbursement_charge_types_payable_account_id", "disbursement_charge_types", ["payable_account_id"])
    _create_index_once(bind, "ix_disbursement_charge_types_expense_account_id", "disbursement_charge_types", ["expense_account_id"])
    _create_index_once(bind, "ix_disbursement_charge_types_tax_payable_account_id", "disbursement_charge_types", ["tax_payable_account_id"])


def downgrade():
    for name, table in [
        ("ix_disbursement_charge_types_tax_payable_account_id", "disbursement_charge_types"),
        ("ix_disbursement_charge_types_expense_account_id", "disbursement_charge_types"),
        ("ix_disbursement_charge_types_payable_account_id", "disbursement_charge_types"),
        ("ix_disbursement_charge_types_income_account_id", "disbursement_charge_types"),
        ("ix_disbursement_charge_types_display_order", "disbursement_charge_types"),
        ("ix_disbursement_charge_types_active", "disbursement_charge_types"),
        ("ix_accounting_accounts_account_role", "accounting_accounts"),
        ("ix_accounting_accounts_account_subtype", "accounting_accounts"),
        ("ix_accounting_accounts_account_type", "accounting_accounts"),
        ("ix_accounting_accounts_is_active", "accounting_accounts"),
    ]:
        op.drop_index(name, table_name=table)
