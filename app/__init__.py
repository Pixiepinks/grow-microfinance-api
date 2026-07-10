import os
import click
from flask import Flask, jsonify, request
from flask_cors import CORS

from flask_migrate import stamp, upgrade
from sqlalchemy import inspect, text

from .extensions import db, migrate, jwt, init_jwt_handlers
from .routes.auth import auth_bp
from .routes.admin import admin_bp
from .routes.staff import staff_bp
from .routes.customer import customer_bp
from .routes.customers import admin_api_customers_bp, api_customers_bp, customers_bp, public_bp
from .routes.loan_applications import admin_api_bp, loan_app_bp
from .routes.leads import leads_bp
from .routes.accounting import accounting_bp
from .schema_fix import ensure_customers_lead_status_column

def create_app():
    app = Flask(__name__)

    config_name = os.getenv("FLASK_ENV", "development").capitalize()
    config_module = f"config.{config_name}Config"
    app.config.from_object(config_module)

    app.config.setdefault("JWT_TOKEN_LOCATION", ["headers", "cookies", "query_string"])
    app.config.setdefault("JWT_QUERY_STRING_NAME", "access_token")

    db.init_app(app)
    migrate.init_app(app, db)
    jwt.init_app(app)
    init_jwt_handlers(app)

    configured_origins = app.config.get("CORS_ORIGINS", [])
    if isinstance(configured_origins, str):
        configured_origins = [origin.strip() for origin in configured_origins.split(",") if origin.strip()]

    CORS(
        app,
        resources={r"/*": {"origins": configured_origins}},
        supports_credentials=True,
        allow_headers=["Authorization", "Content-Type"],
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    )

    @app.before_request
    def handle_preflight_requests():
        if request.method == "OPTIONS":
            return "", 204

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(admin_api_bp)
    app.register_blueprint(staff_bp)
    app.register_blueprint(customer_bp)
    app.register_blueprint(customers_bp)
    app.register_blueprint(api_customers_bp)
    app.register_blueprint(admin_api_customers_bp)
    app.register_blueprint(public_bp)
    app.register_blueprint(loan_app_bp)
    app.register_blueprint(leads_bp)
    app.register_blueprint(accounting_bp)

    with app.app_context():
        ensure_customers_lead_status_column()
        try:
            db.session.execute(
                text(
                    """
                    ALTER TABLE customer_kyc_profiles
                    ADD CONSTRAINT customer_kyc_profiles_customer_id_key UNIQUE (customer_id);
                    """
                )
            )
            db.session.commit()
        except Exception:
            db.session.rollback()


    @app.cli.group("accounting")
    def accounting_cli():
        """Accounting utilities."""
        pass

    @accounting_cli.command("seed")
    def accounting_seed():
        from .accounting import seed_default_accounts
        seed_default_accounts()
        db.session.commit()
        print("Seeded accounting chart of accounts")

    @accounting_cli.command("backfill")
    @click.option("--dry-run/--commit", default=True, help="Preview without committing by default.")
    @click.option("--date-from", default=None, help="Only include records on/after YYYY-MM-DD.")
    @click.option("--date-to", default=None, help="Only include records on/before YYYY-MM-DD.")
    @click.option("--loan-id", type=int, default=None, help="Backfill one loan disbursement.")
    @click.option("--payment-id", type=int, default=None, help="Backfill one payment journal.")
    def accounting_backfill(dry_run, date_from, date_to, loan_id, payment_id):
        from datetime import date as date_cls
        from .models import Loan, Payment
        from .accounting import post_loan_disbursement, post_loan_payment, AccountingError

        start = date_cls.fromisoformat(date_from) if date_from else None
        end = date_cls.fromisoformat(date_to) if date_to else None
        summary = {"created": 0, "skipped": 0, "failed": 0, "mismatches": 0}

        loans = Loan.query
        if loan_id:
            loans = loans.filter_by(id=loan_id)
        if start:
            loans = loans.filter(Loan.start_date >= start)
        if end:
            loans = loans.filter(Loan.start_date <= end)

        payments = Payment.query
        if payment_id:
            payments = payments.filter_by(id=payment_id)
        if start:
            payments = payments.filter(Payment.collection_date >= start)
        if end:
            payments = payments.filter(Payment.collection_date <= end)

        for loan in loans.all():
            try:
                post_loan_disbursement(loan)
                summary["created"] += 1
            except AccountingError:
                summary["skipped"] += 1
        for payment in payments.all():
            try:
                post_loan_payment(payment)
                summary["created"] += 1
            except AccountingError:
                summary["mismatches"] += 1
            except Exception:
                summary["failed"] += 1
        if dry_run:
            db.session.rollback()
        else:
            db.session.commit()
        print(summary)


    @accounting_cli.command("backfill-disbursements")
    @click.option("--dry-run/--commit", default=True, help="Preview without committing by default.")
    @click.option("--loan-id", type=int, default=None, help="Backfill one loan disbursement.")
    @click.option("--date-from", default=None, help="Only include loans starting on/after YYYY-MM-DD.")
    @click.option("--date-to", default=None, help="Only include loans starting on/before YYYY-MM-DD.")
    def accounting_backfill_disbursements(dry_run, loan_id, date_from, date_to):
        from datetime import date as date_cls
        from .models import Loan, AccountingJournalEntry
        from .accounting import post_loan_disbursement, AccountingError, money

        start = date_cls.fromisoformat(date_from) if date_from else None
        end = date_cls.fromisoformat(date_to) if date_to else None
        summary = {"created": 0, "skipped": 0, "failed": 0, "mismatched": 0}
        query = Loan.query.filter(Loan.status.in_(["Active", "ACTIVE"]))
        if loan_id:
            query = query.filter_by(id=loan_id)
        if start:
            query = query.filter(Loan.start_date >= start)
        if end:
            query = query.filter(Loan.start_date <= end)
        for loan in query.all():
            existing = AccountingJournalEntry.query.filter_by(idempotency_key=f"LOAN_DISBURSEMENT:{loan.id}").first()
            if existing:
                if money(existing.total_debit) == money(loan.principal_amount) and money(existing.total_credit) == money(loan.principal_amount):
                    summary["skipped"] += 1
                else:
                    summary["mismatched"] += 1
                continue
            try:
                post_loan_disbursement(loan)
                summary["created"] += 1
            except AccountingError as exc:
                current_app.logger.exception("Failed to backfill disbursement for loan %s", loan.id)
                summary["failed"] += 1
                click.echo({"loan_id": loan.id, "error": str(exc)})
        if dry_run:
            db.session.rollback()
        else:
            db.session.commit()
        click.echo(summary)

    @app.route("/health", methods=["GET"])
    def health_check():
        return jsonify({"status": "ok"})

    # Ensure database schema is present even when the deployment start command
    # skips the entrypoint migration step (e.g., running `gunicorn wsgi:app`).
    # If migrations have already run, `upgrade()` is a no-op. Allow skipping
    # this auto-run via environment variable so CLI migration commands do not
    # execute twice.
    if os.getenv("SKIP_AUTO_MIGRATIONS") != "1":
        with app.app_context():
            try:
                upgrade()
            except Exception as exc:  # pragma: no cover - defensive logging
                app.logger.warning("Skipping automatic migrations: %s", exc)
                try:
                    inspector = inspect(db.engine)
                    if not inspector.has_table("alembic_version"):
                        stamp()
                        app.logger.info(
                            "Database stamped to current migration head after failed upgrade"
                        )
                except Exception as stamp_exc:  # pragma: no cover - defensive logging
                    app.logger.error("Failed to stamp database after migration error: %s", stamp_exc)

    return app
