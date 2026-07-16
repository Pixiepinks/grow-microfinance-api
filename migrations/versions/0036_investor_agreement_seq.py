"""add investor agreement number sequence

Revision ID: 0036_investor_agreement_seq
Revises: 0035_investor_seq
Create Date: 2026-07-16
"""
from alembic import op

revision = "0036_investor_agreement_seq"
down_revision = "0035_investor_seq"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("""
        DO $$
        DECLARE
            highest_suffix bigint;
        BEGIN
            CREATE SEQUENCE IF NOT EXISTS investor_agreement_number_seq
                START WITH 1
                INCREMENT BY 1;

            SELECT max(substring(agreement_number from '^GROW-IFA-[0-9]{8}-([0-9]+)$')::bigint)
            INTO highest_suffix
            FROM investor_funding_agreements
            WHERE agreement_number ~ '^GROW-IFA-[0-9]{8}-[0-9]+$';

            IF highest_suffix IS NULL OR highest_suffix < 1 THEN
                PERFORM setval('investor_agreement_number_seq', 1, false);
            ELSE
                PERFORM setval('investor_agreement_number_seq', highest_suffix, true);
            END IF;
        END $$;
    """)


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("DROP SEQUENCE IF EXISTS investor_agreement_number_seq")
