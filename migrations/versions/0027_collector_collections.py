"""Collector clearing collections and deposits.

Revision ID: 0027_collector_collect
Revises: 0026_loan_accrual
Create Date: 2026-07-12
"""
from alembic import op
import sqlalchemy as sa

revision = "0027_collector_collect"
down_revision = "0026_loan_accrual"
branch_labels = None
depends_on = None

ACCOUNT_SUBTYPE_CHECK = "account_subtype in ('CASH','BANK','COLLECTION_CLEARING','LOAN_RECEIVABLE','INTEREST_RECEIVABLE','PENALTY_RECEIVABLE','OTHER_CURRENT_ASSET','FIXED_ASSET','ACCOUNTS_PAYABLE','BORROWING','CAPITAL','RETAINED_EARNINGS','INTEREST_INCOME','PENALTY_INCOME','FEE_INCOME','OPERATING_EXPENSE','WRITE_OFF_EXPENSE','SUSPENSE','OTHER')"


def _table_names(bind):
    return set(sa.inspect(bind).get_table_names())


def _column_names(bind, table):
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def _fk_names(bind, table):
    return {
        fk.get("name")
        for fk in sa.inspect(bind).get_foreign_keys(table)
        if fk.get("name")
    }


def _index_names(bind, table):
    return {
        index.get("name")
        for index in sa.inspect(bind).get_indexes(table)
        if index.get("name")
    }


def _check_names(bind, table):
    return {
        constraint.get("name")
        for constraint in sa.inspect(bind).get_check_constraints(table)
        if constraint.get("name")
    }


def _primary_key_columns(bind, table):
    return set(sa.inspect(bind).get_pk_constraint(table).get("constrained_columns") or [])


def _unique_columns(bind, table):
    unique_columns = set(_primary_key_columns(bind, table))
    for constraint in sa.inspect(bind).get_unique_constraints(table):
        columns = constraint.get("column_names") or []
        if len(columns) == 1:
            unique_columns.add(columns[0])
    return unique_columns


def _create_fk_if_possible(
    bind,
    name,
    source_table,
    target_table,
    source_columns,
    target_columns,
    ondelete=None,
):
    tables = _table_names(bind)
    if source_table not in tables or target_table not in tables:
        print(f"0027: skipping {name}; missing table")
        return

    source_existing = _column_names(bind, source_table)
    target_existing = _column_names(bind, target_table)
    if not set(source_columns) <= source_existing or not set(target_columns) <= target_existing:
        print(f"0027: skipping {name}; missing column")
        return

    if len(target_columns) == 1 and target_columns[0] not in _unique_columns(bind, target_table):
        print(f"0027: skipping {name}; target column is not unique")
        return

    if name in _fk_names(bind, source_table):
        print(f"0027: skipping {name}; already exists")
        return

    print(f"0027: creating {name}")
    op.create_foreign_key(
        name,
        source_table,
        target_table,
        source_columns,
        target_columns,
        ondelete=ondelete,
    )


def _create_index_if_missing(bind, name, table, columns, unique=False):
    if table not in _table_names(bind):
        print(f"0027: skipping {name}; missing table")
        return
    if not set(columns) <= _column_names(bind, table):
        print(f"0027: skipping {name}; missing column")
        return
    if name in _index_names(bind, table):
        print(f"0027: skipping {name}; already exists")
        return

    print(f"0027: creating {name}")
    op.create_index(name, table, columns, unique=unique)


def _sync_account_subtype_check(bind):
    if bind.dialect.name != "postgresql":
        print("0027: skipping account subtype check sync outside PostgreSQL")
        return

    checks = _check_names(bind, "accounting_accounts")
    if "ck_accounting_accounts_subtype" in checks:
        print("0027: replacing accounting account subtype check")
        op.drop_constraint(
            "ck_accounting_accounts_subtype",
            "accounting_accounts",
            type_="check",
        )

    print("0027: creating accounting account subtype check")
    op.create_check_constraint(
        "ck_accounting_accounts_subtype",
        "accounting_accounts",
        ACCOUNT_SUBTYPE_CHECK,
    )


def upgrade():
    bind = op.get_bind()
    tables = _table_names(bind)

    print("0027: validating existing schema")
    required = {"accounting_accounts", "loans", "payments"}
    missing_required = required - tables
    if missing_required:
        raise RuntimeError(
            "Missing required tables: " + ", ".join(sorted(missing_required))
        )

    _sync_account_subtype_check(bind)

    print("0027: adding account metadata")
    account_columns = _column_names(bind, "accounting_accounts")
    if "collector_id" not in account_columns:
        print("0027: adding accounting_accounts.collector_id")
        op.add_column("accounting_accounts", sa.Column("collector_id", sa.Integer(), nullable=True))
    if "is_collection_account" not in account_columns:
        print("0027: adding accounting_accounts.is_collection_account")
        op.add_column(
            "accounting_accounts",
            sa.Column(
                "is_collection_account",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
    if "account_role" not in account_columns:
        print("0027: adding accounting_accounts.account_role")
        op.add_column("accounting_accounts", sa.Column("account_role", sa.String(50), nullable=True))
    if "parent_account_id" not in account_columns:
        print("0027: adding accounting_accounts.parent_account_id")
        op.add_column("accounting_accounts", sa.Column("parent_account_id", sa.Integer(), nullable=True))

    print("0027: adding payment metadata")
    payment_columns = _column_names(bind, "payments")
    payment_additions = [
        ("payment_date", sa.Column("payment_date", sa.Date(), nullable=True)),
        ("accounting_date", sa.Column("accounting_date", sa.Date(), nullable=True)),
        ("collection_method", sa.Column("collection_method", sa.String(50), nullable=True)),
        ("collection_account_id", sa.Column("collection_account_id", sa.Integer(), nullable=True)),
        ("collector_id", sa.Column("collector_id", sa.Integer(), nullable=True)),
        ("bank_reference", sa.Column("bank_reference", sa.String(120), nullable=True)),
        ("receipt_number", sa.Column("receipt_number", sa.String(40), nullable=True)),
        ("status", sa.Column("status", sa.String(20), nullable=False, server_default="POSTED")),
        ("reversed_by", sa.Column("reversed_by", sa.Integer(), nullable=True)),
        ("deposited_amount", sa.Column("deposited_amount", sa.Numeric(18, 2), nullable=False, server_default="0")),
        ("deposit_status", sa.Column("deposit_status", sa.String(30), nullable=False, server_default="NOT_APPLICABLE")),
    ]
    for column_name, column in payment_additions:
        if column_name not in payment_columns:
            print(f"0027: adding payments.{column_name}")
            op.add_column("payments", column)

    print("0027: creating deposit batch table")
    if "collection_deposit_batches" not in tables:
        print("0027: creating collection_deposit_batches")
        op.create_table(
            "collection_deposit_batches",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("deposit_number", sa.String(40), nullable=False),
            sa.Column("collector_id", sa.Integer(), nullable=False),
            sa.Column("collector_account_id", sa.Integer(), nullable=False),
            sa.Column("bank_account_id", sa.Integer(), nullable=False),
            sa.Column("deposit_date", sa.Date(), nullable=False),
            sa.Column("accounting_date", sa.Date(), nullable=False),
            sa.Column("total_amount", sa.Numeric(18, 2), nullable=False),
            sa.Column("bank_reference", sa.String(120)),
            sa.Column("deposit_slip_reference", sa.String(120)),
            sa.Column("remarks", sa.Text()),
            sa.Column("journal_entry_id", sa.Integer()),
            sa.Column("reversal_journal_id", sa.Integer()),
            sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
            sa.Column("created_by", sa.Integer()),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("reversed_at", sa.DateTime()),
            sa.Column("reversal_reason", sa.Text()),
            sa.UniqueConstraint("deposit_number", name="uq_dep_batch_no"),
        )

    print("0027: creating allocation table")
    if "collection_deposit_allocations" not in tables:
        print("0027: creating collection_deposit_allocations")
        op.create_table(
            "collection_deposit_allocations",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("deposit_batch_id", sa.Integer(), nullable=False),
            sa.Column("payment_id", sa.Integer(), nullable=False),
            sa.Column("allocated_amount", sa.Numeric(18, 2), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("payment_id", name="uq_dep_alloc_payment"),
        )

    print("0027: creating foreign keys")
    _create_fk_if_possible(bind, "fk_acct_collector", "accounting_accounts", "users", ["collector_id"], ["id"], ondelete="SET NULL")
    _create_fk_if_possible(bind, "fk_acct_parent", "accounting_accounts", "accounting_accounts", ["parent_account_id"], ["id"], ondelete="SET NULL")
    _create_fk_if_possible(bind, "fk_pay_coll_acct", "payments", "accounting_accounts", ["collection_account_id"], ["id"], ondelete="SET NULL")
    _create_fk_if_possible(bind, "fk_pay_collector", "payments", "users", ["collector_id"], ["id"], ondelete="SET NULL")
    _create_fk_if_possible(bind, "fk_pay_reversed_by", "payments", "users", ["reversed_by"], ["id"], ondelete="SET NULL")
    _create_fk_if_possible(bind, "fk_dep_collector", "collection_deposit_batches", "users", ["collector_id"], ["id"])
    _create_fk_if_possible(bind, "fk_dep_src_acct", "collection_deposit_batches", "accounting_accounts", ["collector_account_id"], ["id"])
    _create_fk_if_possible(bind, "fk_dep_bank_acct", "collection_deposit_batches", "accounting_accounts", ["bank_account_id"], ["id"])
    _create_fk_if_possible(bind, "fk_dep_journal", "collection_deposit_batches", "accounting_journal_entries", ["journal_entry_id"], ["id"], ondelete="SET NULL")
    _create_fk_if_possible(bind, "fk_dep_rev_journal", "collection_deposit_batches", "accounting_journal_entries", ["reversal_journal_id"], ["id"], ondelete="SET NULL")
    _create_fk_if_possible(bind, "fk_dep_created_by", "collection_deposit_batches", "users", ["created_by"], ["id"], ondelete="SET NULL")
    _create_fk_if_possible(bind, "fk_alloc_batch", "collection_deposit_allocations", "collection_deposit_batches", ["deposit_batch_id"], ["id"])
    _create_fk_if_possible(bind, "fk_alloc_payment", "collection_deposit_allocations", "payments", ["payment_id"], ["id"])

    print("0027: creating indexes")
    _create_index_if_missing(bind, "ix_payments_receipt_number", "payments", ["receipt_number"], unique=True)
    _create_index_if_missing(bind, "ix_pay_deposit_status", "payments", ["deposit_status"])
    _create_index_if_missing(bind, "ix_dep_batch_no", "collection_deposit_batches", ["deposit_number"], unique=True)
    _create_index_if_missing(bind, "ix_cda_batch", "collection_deposit_allocations", ["deposit_batch_id"])
    _create_index_if_missing(bind, "ix_cda_payment", "collection_deposit_allocations", ["payment_id"])

    print("0027: backfilling payment metadata")
    latest_payment_columns = _column_names(bind, "payments")
    if {"payment_date", "collection_date"} <= latest_payment_columns:
        op.execute("UPDATE payments SET payment_date = collection_date WHERE payment_date IS NULL")
    if {"accounting_date", "collection_date"} <= latest_payment_columns:
        op.execute("UPDATE payments SET accounting_date = collection_date WHERE accounting_date IS NULL")
    if {"collection_method", "payment_method"} <= latest_payment_columns:
        op.execute("UPDATE payments SET collection_method = CASE WHEN lower(coalesce(payment_method,'')) LIKE '%bank%' THEN 'BANK_TRANSFER' ELSE 'CASH_OFFICE' END WHERE collection_method IS NULL")

    print("0027: migration complete")


def downgrade():
    pass
