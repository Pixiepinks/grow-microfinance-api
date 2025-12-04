"""Placeholder guardrails migration after removing staff approval columns."""

from alembic import op
import sqlalchemy as sa


revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade():
    # Schema already reflects desired columns; keeping revision for history.
    pass


def downgrade():
    # No-op downgrade to match placeholder upgrade.
    pass
