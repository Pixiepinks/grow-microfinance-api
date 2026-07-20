"""consolidate nullable customer master profile fields

Revision ID: 0044_customer_master_profile
Revises: 0043_ledger_last_payment_date
"""
from alembic import op
import sqlalchemy as sa
revision = "0044_customer_master_profile"
down_revision = "0043_ledger_last_payment_date"
branch_labels = None
depends_on = None
def upgrade():
    for name, col in (("email", sa.String(120)), ("monthly_expenses", sa.Numeric(12, 2)), ("address_backfill_review_required", sa.Boolean())):
        op.add_column("customers", sa.Column(name, col, nullable=True))
    op.execute("UPDATE customers SET address_backfill_review_required = false WHERE address_backfill_review_required IS NULL")
    op.alter_column("customers", "address_backfill_review_required", nullable=False, server_default=sa.text("false"))
    for name, typ in (("full_name", sa.String(150)), ("nic_number", sa.String(50)), ("mobile", sa.String(20)), ("email", sa.String(120)), ("monthly_expenses", sa.Numeric(12, 2))):
        op.add_column("customer_kyc_profiles", sa.Column(name, typ, nullable=True))
    op.add_column("customer_kyc_profiles", sa.Column("review_status", sa.String(32), nullable=True))
    op.add_column("customer_kyc_profiles", sa.Column("reviewed_at", sa.DateTime(), nullable=True))
    op.drop_constraint("customer_kyc_profiles_customer_id_key", "customer_kyc_profiles", type_="unique")
def downgrade():
    op.create_unique_constraint("customer_kyc_profiles_customer_id_key", "customer_kyc_profiles", ["customer_id"])
    op.drop_column("customer_kyc_profiles", "reviewed_at"); op.drop_column("customer_kyc_profiles", "review_status")
    for name in ("monthly_expenses", "email", "mobile", "nic_number", "full_name"): op.drop_column("customer_kyc_profiles", name)
    op.drop_column("customers", "address_backfill_review_required"); op.drop_column("customers", "monthly_expenses"); op.drop_column("customers", "email")
