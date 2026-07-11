"""Startup schema validation for production safety."""

import logging

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError

REQUIRED_COLUMNS = {
    "loan_applications": {
        "term_type",
        "term_value",
        "repayment_frequency",
        "number_of_installments",
        "installment_count",
        "installment_amount",
        "total_repayment",
        "total_interest",
        "interest_type",
        "interest_rate_basis",
    },
    "loans": {
        "tenure_months",
        "term_type",
        "term_value",
        "repayment_frequency",
        "number_of_installments",
        "installment_count",
        "installment_amount",
        "total_repayment",
        "total_interest",
        "interest_type",
        "interest_rate_basis",
        "maturity_date",
        "final_installment_due_date",
        "loan_days",
        "start_date",
        "end_date",
    },
}


def missing_required_columns(engine):
    inspector = sa.inspect(engine)
    missing = {}
    for table_name, required_columns in REQUIRED_COLUMNS.items():
        existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
        missing_columns = sorted(required_columns - existing_columns)
        if missing_columns:
            missing[table_name] = missing_columns
    return missing


def validate_required_schema(engine, logger=None):
    logger = logger or logging.getLogger(__name__)
    try:
        missing = missing_required_columns(engine)
    except SQLAlchemyError:
        logger.exception("Unable to inspect database schema during startup validation.")
        raise SystemExit(1)
    if missing:
        details = "; ".join(
            f"{table}: {', '.join(columns)}" for table, columns in missing.items()
        )
        logger.error(
            "Database schema is behind application models; missing required columns: %s. "
            "Run `flask db upgrade` before starting Gunicorn.",
            details,
        )
        raise SystemExit(1)
    logger.info("Database schema validation passed for loan term columns.")
