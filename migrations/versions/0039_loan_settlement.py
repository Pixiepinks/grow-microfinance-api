"""add automatic loan settlement and customer overpayment credits

Revision ID: 0039_loan_settlement
Revises: 0038_inv_accr_cal_days
"""
from alembic import op
import sqlalchemy as sa

revision = "0039_loan_settlement"
down_revision = "0038_inv_accr_cal_days"
branch_labels = None
depends_on = None

ACCOUNT_SUBTYPE_CHECK = "account_subtype in ('CASH','BANK','COLLECTION_CLEARING','COLLECTION_CLEARING_CONTROL','LOAN_RECEIVABLE','INTEREST_RECEIVABLE','PENALTY_RECEIVABLE','OTHER_CURRENT_ASSET','FIXED_ASSET','ACCOUNTS_PAYABLE','BORROWING','CUSTOMER_ADVANCE','CAPITAL','RETAINED_EARNINGS','INTEREST_INCOME','PENALTY_INCOME','FEE_INCOME','OPERATING_EXPENSE','WRITE_OFF_EXPENSE','SUSPENSE','OTHER')"


def _columns(table):
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _table_exists(table):
    return table in sa.inspect(op.get_bind()).get_table_names()


def _index_names(table):
    return {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes(table)
        if index.get("name")
    }


def _foreign_key_names(table):
    return {
        foreign_key["name"]
        for foreign_key in sa.inspect(op.get_bind()).get_foreign_keys(table)
        if foreign_key.get("name")
    }


def _check_names(table):
    return {
        check["name"]
        for check in sa.inspect(op.get_bind()).get_check_constraints(table)
        if check.get("name")
    }


def _allow_customer_advance_subtype():
    if op.get_bind().dialect.name != "postgresql":
        return
    if "ck_accounting_accounts_subtype" in _check_names("accounting_accounts"):
        op.drop_constraint(
            "ck_accounting_accounts_subtype",
            "accounting_accounts",
            type_="check",
        )
    op.create_check_constraint(
        "ck_accounting_accounts_subtype",
        "accounting_accounts",
        ACCOUNT_SUBTYPE_CHECK,
    )


def _validate_existing_customer_advance_account():
    account = op.get_bind().execute(
        sa.text(
            """
            SELECT account_name, account_type, normal_balance, account_subtype
            FROM accounting_accounts
            WHERE account_code = '2250'
            """
        )
    ).mappings().one_or_none()
    if account and dict(account) != {
        "account_name": "Customer Advances / Credit Balances",
        "account_type": "LIABILITY",
        "normal_balance": "CREDIT",
        "account_subtype": "CUSTOMER_ADVANCE",
    }:
        raise RuntimeError(
            "Cannot create the Customer Advances account: accounting account code "
            "2250 is already assigned to a different account."
        )


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
    foreign_keys = _foreign_key_names("loans")
    with op.batch_alter_table("loans") as batch:
        if "fk_loans_settled_by_id" not in foreign_keys:
            batch.create_foreign_key("fk_loans_settled_by_id", "users", ["settled_by_id"], ["id"])
        if "fk_loans_settlement_payment_id" not in foreign_keys:
            batch.create_foreign_key("fk_loans_settlement_payment_id", "payments", ["settlement_payment_id"], ["id"])
        if "fk_loans_settlement_journal_id" not in foreign_keys:
            batch.create_foreign_key("fk_loans_settlement_journal_id", "accounting_journal_entries", ["settlement_journal_id"], ["id"])

    if not _table_exists("customer_credit_balances"):
        op.create_table("customer_credit_balances",
            sa.Column("id", sa.Integer(), primary_key=True), sa.Column("customer_id", sa.Integer(), nullable=False),
            sa.Column("loan_id", sa.Integer()), sa.Column("payment_id", sa.Integer()), sa.Column("credit_number", sa.String(40), nullable=False),
            sa.Column("credit_date", sa.Date(), nullable=False), sa.Column("source_type", sa.String(50), nullable=False), sa.Column("source_id", sa.String(64), nullable=False),
            sa.Column("original_amount", sa.Numeric(18,2), nullable=False), sa.Column("available_amount", sa.Numeric(18,2), nullable=False),
            sa.Column("applied_amount", sa.Numeric(18,2), nullable=False, server_default="0.00"), sa.Column("refunded_amount", sa.Numeric(18,2), nullable=False, server_default="0.00"),
            sa.Column("status", sa.String(30), nullable=False, server_default="AVAILABLE"), sa.Column("reference", sa.String(120)), sa.Column("remarks", sa.Text()),
            sa.Column("journal_entry_id", sa.Integer(), sa.ForeignKey("accounting_journal_entries.id")), sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id")), sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["customer_id"],["customers.id"]), sa.ForeignKeyConstraint(["loan_id"],["loans.id"]), sa.ForeignKeyConstraint(["payment_id"],["payments.id"]),
            sa.UniqueConstraint("source_type", "source_id", name="uq_customer_credit_source"), sa.UniqueConstraint("payment_id"), sa.UniqueConstraint("credit_number"),
        )
    elif not {"customer_id", "loan_id", "payment_id", "credit_number", "source_type", "source_id"} <= _columns("customer_credit_balances"):
        raise RuntimeError("customer_credit_balances exists but is not the schema expected by migration 0039")
    if "ix_customer_credit_balances_customer_id" not in _index_names("customer_credit_balances"):
        op.create_index("ix_customer_credit_balances_customer_id", "customer_credit_balances", ["customer_id"])

    _allow_customer_advance_subtype()
    _validate_existing_customer_advance_account()
    op.execute(
        """
        INSERT INTO accounting_accounts (
            account_code,
            account_name,
            account_type,
            normal_balance,
            account_subtype,
            is_active,
            allow_manual_posting,
            is_system_account,
            cash_flow_category,
            created_at,
            updated_at
        )
        SELECT
            '2250',
            'Customer Advances / Credit Balances',
            'LIABILITY',
            'CREDIT',
            'CUSTOMER_ADVANCE',
            TRUE,
            TRUE,
            TRUE,
            'NONE',
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP
        WHERE NOT EXISTS (
            SELECT 1
            FROM accounting_accounts
            WHERE account_code = '2250'
        )
        """
    )
    op.execute("INSERT INTO accounting_settings (setting_key, setting_value, created_at, updated_at) SELECT 'customer_advance_liability_account_id', CAST(id AS TEXT), CURRENT_TIMESTAMP, CURRENT_TIMESTAMP FROM accounting_accounts WHERE account_code = '2250' AND NOT EXISTS (SELECT 1 FROM accounting_settings WHERE setting_key = 'customer_advance_liability_account_id')")


def downgrade():
    if _table_exists("customer_credit_balances"):
        op.drop_table("customer_credit_balances")
    foreign_keys = _foreign_key_names("loans")
    with op.batch_alter_table("loans") as batch:
        for name in (
            "fk_loans_settlement_journal_id",
            "fk_loans_settlement_payment_id",
            "fk_loans_settled_by_id",
        ):
            if name in foreign_keys:
                batch.drop_constraint(name, type_="foreignkey")
        for name in (
            "customer_credit_balance",
            "settlement_reason",
            "settlement_journal_id",
            "settlement_payment_id",
            "settled_by_id",
            "settled_date",
            "settled_at",
        ):
            if name in _columns("loans"):
                batch.drop_column(name)
