"""Database schema helpers for runtime safety."""

from sqlalchemy import inspect, text

from .extensions import db


def ensure_customer_status_columns() -> None:
    """Ensure customer status columns exist in the customers table.

    This helper is intended for environments where migrations cannot be run
    reliably. It is idempotent and safe to call during application startup.
    """

    engine = db.engine
    inspector = inspect(engine)
    columns = {col["name"] for col in inspector.get_columns("customers")}

    statements = []
    if "lead_status" not in columns:
        statements.append("ADD COLUMN lead_status VARCHAR(32) NOT NULL DEFAULT 'NEW'")
    if "kyc_status" not in columns:
        statements.append("ADD COLUMN kyc_status VARCHAR(32) NOT NULL DEFAULT 'PENDING'")
    if "eligibility_status" not in columns:
        statements.append("ADD COLUMN eligibility_status VARCHAR(32) NOT NULL DEFAULT 'UNKNOWN'")

    if statements:
        ddl = "ALTER TABLE customers " + ", ".join(statements) + ";"
        with engine.connect() as conn:
            conn.execute(text(ddl))
            conn.commit()
