"""Finalize removal of staff approval columns from schema history."""

from alembic import op
import sqlalchemy as sa


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    # No schema changes; columns are intentionally absent from the model and DB.
    pass


def downgrade():
    # Matching no-op downgrade.
    pass
