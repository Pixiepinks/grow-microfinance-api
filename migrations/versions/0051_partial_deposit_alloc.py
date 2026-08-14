"""allow a payment allocation in more than one deposit batch

Revision ID: 0051_partial_dep
Revises: 0050_fix_empty_bank_recon
"""
from alembic import op
import sqlalchemy as sa


revision = "0051_partial_dep"
down_revision = "0050_fix_empty_bank_recon"
branch_labels = None
depends_on = None

TABLE = "collection_deposit_allocations"
OLD = "uq_dep_alloc_payment"
NEW = "uq_dep_alloc_batch_payment"


def _unique_names(bind):
    return {item["name"] for item in sa.inspect(bind).get_unique_constraints(TABLE)}


def _assert_no_duplicate_pairs(bind):
    duplicate = bind.execute(sa.text("""
        SELECT deposit_batch_id, payment_id, COUNT(*) AS row_count
          FROM collection_deposit_allocations
         GROUP BY deposit_batch_id, payment_id
        HAVING COUNT(*) > 1
         ORDER BY deposit_batch_id, payment_id
         LIMIT 1
    """)).mappings().first()
    if duplicate:
        raise RuntimeError(
            "Cannot add uq_dep_alloc_batch_payment: duplicate allocation pair "
            f"deposit_batch_id={duplicate['deposit_batch_id']}, "
            f"payment_id={duplicate['payment_id']}, rows={duplicate['row_count']}"
        )


def upgrade():
    bind = op.get_bind()
    _assert_no_duplicate_pairs(bind)
    names = _unique_names(bind)
    with op.batch_alter_table(TABLE) as batch:
        if OLD in names:
            batch.drop_constraint(OLD, type_="unique")
        if NEW not in names:
            batch.create_unique_constraint(NEW, ["deposit_batch_id", "payment_id"])


def downgrade():
    bind = op.get_bind()
    duplicate_payment = bind.execute(sa.text("""
        SELECT payment_id, COUNT(*) AS row_count
          FROM collection_deposit_allocations
         GROUP BY payment_id
        HAVING COUNT(*) > 1
         ORDER BY payment_id
         LIMIT 1
    """)).mappings().first()
    if duplicate_payment:
        raise RuntimeError(
            "Cannot restore uq_dep_alloc_payment without losing valid multi-batch "
            f"history: payment_id={duplicate_payment['payment_id']}, "
            f"rows={duplicate_payment['row_count']}"
        )
    names = _unique_names(bind)
    with op.batch_alter_table(TABLE) as batch:
        if NEW in names:
            batch.drop_constraint(NEW, type_="unique")
        if OLD not in names:
            batch.create_unique_constraint(OLD, ["payment_id"])
