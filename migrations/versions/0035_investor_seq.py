"""add investor number sequence

Revision ID: 0035_investor_seq
Revises: 0034_investor_funding
Create Date: 2026-07-16
"""
from alembic import op

revision = "0035_investor_seq"
down_revision = "0034_investor_funding"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute("""
        DO $$
        DECLARE
            duplicate_count integer;
            malformed_count integer;
            highest_suffix bigint;
        BEGIN
            SELECT count(*) INTO duplicate_count
            FROM (
                SELECT investor_number
                FROM investors
                WHERE investor_number IS NOT NULL
                GROUP BY investor_number
                HAVING count(*) > 1
            ) duplicates;

            IF duplicate_count > 0 THEN
                RAISE EXCEPTION 'Cannot add investor_number uniqueness protection: % duplicate investor_number value(s) exist', duplicate_count;
            END IF;

            SELECT count(*) INTO malformed_count
            FROM investors
            WHERE investor_number IS NOT NULL
              AND investor_number !~ '^GROW-INV-[0-9]+$';

            IF malformed_count > 0 THEN
                RAISE NOTICE 'Found % malformed investor_number value(s); preserving them and seeding the sequence from valid GROW-INV numbers only', malformed_count;
            END IF;

            CREATE SEQUENCE IF NOT EXISTS investor_number_seq
                START WITH 1
                INCREMENT BY 1;

            SELECT max(substring(investor_number from '^GROW-INV-([0-9]+)$')::bigint)
            INTO highest_suffix
            FROM investors
            WHERE investor_number ~ '^GROW-INV-[0-9]+$';

            IF highest_suffix IS NULL OR highest_suffix < 1 THEN
                PERFORM setval('investor_number_seq', 1, false);
            ELSE
                PERFORM setval('investor_number_seq', highest_suffix, true);
            END IF;

            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'uq_investors_investor_number'
                  AND conrelid = 'investors'::regclass
            ) THEN
                ALTER TABLE investors
                    ADD CONSTRAINT uq_investors_investor_number
                    UNIQUE (investor_number);
            END IF;
        END $$;
    """)


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("DROP SEQUENCE IF EXISTS investor_number_seq")
