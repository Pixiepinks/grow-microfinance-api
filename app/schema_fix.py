"""Database schema helpers for runtime safety."""

from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from .extensions import db


def ensure_customers_lead_status_column():
    """
    Make sure the customers table has the columns required by the
    admin /customers API. On databases where the columns already
    exist, this is a no-op.
    """
    ddl_statements = [
        text(
            """
            ALTER TABLE customers
            ADD COLUMN IF NOT EXISTS lead_status VARCHAR(50) DEFAULT 'NEW'
            """
        ),
        text(
            """
            ALTER TABLE customers
            ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW()
            """
        ),
    ]

    engine = db.engine
    with engine.begin() as conn:
        for ddl in ddl_statements:
            try:
                conn.execute(ddl)
            except ProgrammingError:
                # Column already exists or other harmless issue – ignore
                pass
