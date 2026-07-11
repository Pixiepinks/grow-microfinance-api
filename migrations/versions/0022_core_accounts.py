"""Mark core accounting accounts as system accounts.

Revision ID: 0022_core_accounts
Revises: 0021
Create Date: 2026-07-11
"""
from alembic import op

revision = "0022_core_accounts"
down_revision = "0021"
branch_labels = None
depends_on = None

CORE_ACCOUNT_CODES = (
    "1000", "1010", "1100", "1110", "1120", "1990",
    "3100", "4000", "4010", "4020", "5050",
)


def upgrade():
    op.execute(
        """
        UPDATE accounting_accounts
        SET is_system_account = TRUE
        WHERE account_code IN ('1000','1010','1100','1110','1120','1990','3100','4000','4010','4020','5050')
          AND COALESCE(is_system_account, FALSE) IS DISTINCT FROM TRUE
        """
    )


def downgrade():
    op.execute(
        """
        UPDATE accounting_accounts
        SET is_system_account = FALSE
        WHERE account_code IN ('1000','1010','1100','1110','1120','1990','3100','4000','4010','4020','5050')
          AND COALESCE(is_system_account, FALSE) IS DISTINCT FROM FALSE
        """
    )
