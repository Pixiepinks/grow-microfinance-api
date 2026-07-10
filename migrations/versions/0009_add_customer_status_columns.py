"""Add customer status columns with server defaults

Revision ID: 0009
Revises: 0008
Create Date: 2025-01-01 00:00:00.000000
"""


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    # Columns were already added in revision 0006; keep this revision as a no-op
    # for deployments that have this historical migration in their chain.
    pass

def downgrade():
    # No-op because upgrade does not mutate schema.
    pass
