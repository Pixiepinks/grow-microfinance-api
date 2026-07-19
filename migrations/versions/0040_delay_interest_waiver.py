"""Add delay-interest waiver reconciliation fields safely.

This revision was partially applied to one production database before its
Alembic version row was written.  The guards below deliberately make the
schema changes safe to replay: existing data is retained and only missing
objects (or NULL values in the new required amount column) are changed.

Revision ID: 0040_delay_interest_waiver
Revises: 0039_loan_settlement
"""
import re

from alembic import op
import sqlalchemy as sa


revision = "0040_delay_interest_waiver"
down_revision = "0039_loan_settlement"
branch_labels = None
depends_on = None


# Keep this list aligned with AccountingAccount's model check constraint.
ACCOUNT_SUBTYPES = {
    "CASH", "BANK", "COLLECTION_CLEARING", "COLLECTION_CLEARING_CONTROL",
    "LOAN_RECEIVABLE", "INTEREST_RECEIVABLE", "PENALTY_RECEIVABLE",
    "OTHER_CURRENT_ASSET", "FIXED_ASSET", "ACCOUNTS_PAYABLE", "BORROWING",
    "CUSTOMER_ADVANCE", "CAPITAL", "RETAINED_EARNINGS", "INTEREST_INCOME",
    "PENALTY_INCOME", "FEE_INCOME", "OPERATING_EXPENSE", "WRITE_OFF_EXPENSE",
    "DELAY_INTEREST_WAIVER", "SUSPENSE", "OTHER",
}


def _inspector():
    """Return a fresh inspector; inspectors cache metadata after DDL."""
    return sa.inspect(op.get_bind())


def table_exists(table_name):
    return table_name in _inspector().get_table_names()


def column_exists(table_name, column_name):
    return any(column["name"] == column_name for column in _inspector().get_columns(table_name))


def index_exists(table_name, index_name):
    return any(index.get("name") == index_name for index in _inspector().get_indexes(table_name))


def _column(table_name, column_name):
    return next(column for column in _inspector().get_columns(table_name) if column["name"] == column_name)


def _foreign_key_exists(table_name, name, constrained_columns, referred_table):
    for foreign_key in _inspector().get_foreign_keys(table_name):
        if foreign_key.get("name") == name:
            return True
        if (
            foreign_key.get("constrained_columns") == constrained_columns
            and foreign_key.get("referred_table") == referred_table
        ):
            return True
    return False


def _numeric_18_2(column):
    column_type = column["type"]
    return isinstance(column_type, sa.Numeric) and column_type.precision == 18 and column_type.scale == 2


def _ensure_delay_interest_waived():
    if not column_exists("loan_ledger", "delay_interest_waived"):
        op.add_column(
            "loan_ledger",
            sa.Column("delay_interest_waived", sa.Numeric(18, 2), nullable=False, server_default="0.00"),
        )
        return

    column = _column("loan_ledger", "delay_interest_waived")
    if not _numeric_18_2(column):
        raise RuntimeError("loan_ledger.delay_interest_waived must be NUMERIC(18, 2); refusing to replace production data")
    if column["nullable"]:
        op.execute(sa.text("UPDATE loan_ledger SET delay_interest_waived = 0.00 WHERE delay_interest_waived IS NULL"))
        op.alter_column(
            "loan_ledger",
            "delay_interest_waived",
            existing_type=column["type"],
            nullable=False,
            server_default="0.00",
        )
    elif column.get("default") is None:
        # Preserve existing values while restoring the migration's default for
        # rows created after this repair.
        op.alter_column(
            "loan_ledger",
            "delay_interest_waived",
            existing_type=column["type"],
            server_default="0.00",
        )


def _ensure_column(table_name, column):
    if not column_exists(table_name, column.name):
        op.add_column(table_name, column)


def _ensure_credit_foreign_keys():
    if not _foreign_key_exists("customer_credit_balances", "fk_credit_applied_loan", ["applied_to_loan_id"], "loans"):
        op.create_foreign_key("fk_credit_applied_loan", "customer_credit_balances", "loans", ["applied_to_loan_id"], ["id"])
    if not _foreign_key_exists("customer_credit_balances", "fk_credit_correcting_journal", ["correcting_journal_id"], "accounting_journal_entries"):
        op.create_foreign_key("fk_credit_correcting_journal", "customer_credit_balances", "accounting_journal_entries", ["correcting_journal_id"], ["id"])


def _ensure_account_subtype_constraint():
    """Extend, rather than replace, the live PostgreSQL subtype vocabulary."""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    definition = bind.execute(sa.text("""
        SELECT pg_get_constraintdef(c.oid)
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE c.conname = 'ck_accounting_accounts_subtype'
          AND t.relname = 'accounting_accounts'
          AND n.nspname = current_schema()
    """)).scalar_one_or_none()
    existing_subtypes = set(re.findall(r"'([^']+)'", definition or ""))
    allowed_subtypes = existing_subtypes | ACCOUNT_SUBTYPES
    if definition and "DELAY_INTEREST_WAIVER" in existing_subtypes:
        return
    if definition:
        op.drop_constraint("ck_accounting_accounts_subtype", "accounting_accounts", type_="check")
    expression = "account_subtype IN ({})".format(
        ", ".join("'{}'".format(value.replace("'", "''")) for value in sorted(allowed_subtypes))
    )
    op.create_check_constraint("ck_accounting_accounts_subtype", "accounting_accounts", expression)


def _waiver_table_columns():
    return [
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("waiver_number", sa.String(40), nullable=False, unique=True),
        sa.Column("loan_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("ledger_entry_id", sa.Integer()),
        sa.Column("waiver_type", sa.String(30), nullable=False),
        sa.Column("waiver_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("receivable_account_id", sa.Integer(), nullable=False),
        sa.Column("expense_account_id", sa.Integer(), nullable=False),
        sa.Column("journal_entry_id", sa.Integer()),
        sa.Column("approval_reference", sa.String(120)), sa.Column("reason", sa.Text()),
        sa.Column("status", sa.String(20), nullable=False), sa.Column("approved_by", sa.Integer()),
        sa.Column("approved_at", sa.DateTime()), sa.Column("reversed_by", sa.Integer()),
        sa.Column("reversed_at", sa.DateTime()), sa.Column("reversal_journal_id", sa.Integer()),
        sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("updated_at", sa.DateTime(), nullable=False),
    ]


def _ensure_waiver_table():
    columns = _waiver_table_columns()
    if not table_exists("loan_charge_waivers"):
        op.create_table("loan_charge_waivers", *columns)
        return
    for column in columns:
        # A partially-created table can be reconciled without replacing it.
        if not column_exists("loan_charge_waivers", column.name):
            if column.primary_key:
                raise RuntimeError("loan_charge_waivers is missing its primary key; refusing to replace production data")
            op.add_column("loan_charge_waivers", column)
    # PostgreSQL may report the unnamed UNIQUE from create_table without a name;
    # compare its business key rather than attempting to create a duplicate index.
    unique_columns = [item.get("column_names") for item in _inspector().get_unique_constraints("loan_charge_waivers")]
    unique_indexes = [item.get("column_names") for item in _inspector().get_indexes("loan_charge_waivers") if item.get("unique")]
    if ["waiver_number"] not in unique_columns and ["waiver_number"] not in unique_indexes:
        op.create_unique_constraint("uq_loan_charge_waivers_waiver_number", "loan_charge_waivers", ["waiver_number"])


def upgrade():
    _ensure_delay_interest_waived()
    _ensure_column("customer_credit_balances", sa.Column("applied_to_loan_id", sa.Integer(), nullable=True))
    _ensure_column("customer_credit_balances", sa.Column("correcting_journal_id", sa.Integer(), nullable=True))
    _ensure_credit_foreign_keys()
    _ensure_waiver_table()
    # Do this before startup seeding changes account 5060 to this subtype.
    _ensure_account_subtype_constraint()


def downgrade():
    # Downgrades are intentionally conservative for this production repair.
    pass
