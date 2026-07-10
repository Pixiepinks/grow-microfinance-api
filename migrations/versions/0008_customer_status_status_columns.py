"""Add customer status fields with defaults

Revision ID: 0008
Revises: 0007
Create Date: 2025-01-01 00:00:00.000000
"""


revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    # Columns were already added in revision 0006; keep this revision as a no-op
    # for deployments that have this historical migration in their chain.
    pass

def downgrade():
    # No-op because upgrade does not mutate schema.
    pass
