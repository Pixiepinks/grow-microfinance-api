"""Placeholder migration; staff approval metadata removed from schema."""

from alembic import op
import sqlalchemy as sa


revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade():
    # Schema already matches the desired state; no-op migration retained for
    # compatibility with existing revision history.
    pass


def downgrade():
    # No-op downgrade; columns were never added in this branch.
    pass
