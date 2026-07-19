"""merge early-settlement and delay-interest migration heads

Revision ID: 0042_merge_heads
Revises: 0040_delay_interest_waiver, 0041_cash_paid_loan_totals
"""

revision = "0042_merge_heads"
down_revision = (
    "0040_delay_interest_waiver",
    "0041_cash_paid_loan_totals",
)
branch_labels = None
depends_on = None


def upgrade():
    # This is a graph-only merge. Both parent revisions retain their schema changes.
    pass


def downgrade():
    # Splitting the version history does not require schema changes.
    pass
