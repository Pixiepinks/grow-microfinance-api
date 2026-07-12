import os
import click
from flask import Flask, jsonify, request
from flask_cors import CORS

from sqlalchemy import text
from werkzeug.exceptions import HTTPException

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
    _warn_on_weak_jwt_secret(app)

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

    @app.errorhandler(HTTPException)
    def handle_http_exception(exc):
        return (
            jsonify(
                {
                    "error": exc.name.lower().replace(" ", "_"),
                    "message": exc.description,
                }
            ),
            exc.code,
        )

    @app.errorhandler(Exception)
    def handle_unexpected_exception(exc):
        db.session.rollback()
        app.logger.exception(
            "Unhandled API error during %s %s", request.method, request.path
        )
        message = (
            "Unable to submit loan application."
            if request.path.startswith("/loan-applications")
            else "An unexpected error occurred."
        )
        return jsonify({"error": "internal_server_error", "message": message}), 500

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



    @accounting_cli.command("repair-defective-loan")
    @click.argument("loan_id", type=int)
    @click.option("--apply", "apply_changes", is_flag=True, default=False, help="Persist repair. Dry-run is the default.")
    def accounting_repair_defective_loan(loan_id, apply_changes):
        from .loan_repair import repair_unpaid_defective_loan, LoanRepairError
        try:
            summary = repair_unpaid_defective_loan(loan_id, apply_changes=apply_changes)
        except LoanRepairError as exc:
            db.session.rollback()
            raise click.ClickException(str(exc))
        click.echo(summary)

    @accounting_cli.command("backfill-disbursements")
    @click.option("--apply", "apply_changes", is_flag=True, default=False, help="Persist journals. Dry-run is the default.")
    @click.option("--loan-id", type=int, default=None, help="Backfill one loan disbursement.")
    @click.option("--date-from", default=None, help="Only include loans starting on/after YYYY-MM-DD.")
    @click.option("--date-to", default=None, help="Only include loans starting on/before YYYY-MM-DD.")
    @click.option("--funding-account-code", default=None, help="Explicit funding account code override.")
    def accounting_backfill_disbursements(apply_changes, loan_id, date_from, date_to, funding_account_code):
        from datetime import date as date_cls
        from .models import Loan, AccountingAccount, AccountingJournalEntry
        from .accounting import post_loan_disbursement, AccountingError, money, resolve_system_account

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
                funding_account = AccountingAccount.query.filter_by(account_code=funding_account_code).first() if funding_account_code else resolve_system_account("DEFAULT_DISBURSEMENT_ACCOUNT")
                click.echo({"loan_id": loan.id, "funding_account": funding_account.account_code})
                post_loan_disbursement(loan, funding_account=funding_account)
                summary["created"] += 1
            except AccountingError as exc:
                app.logger.exception("Failed to backfill disbursement for loan %s", loan.id)
                summary["failed"] += 1
                click.echo({"loan_id": loan.id, "error": str(exc)})
        if not apply_changes:
            db.session.rollback()
        else:
            db.session.commit()
        click.echo(summary)

    @accounting_cli.command("backfill-payments")
    @click.option("--apply", "apply_changes", is_flag=True, default=False, help="Persist journals. Dry-run is the default.")
    @click.option("--payment-id", type=int, default=None, help="Backfill one payment journal.")
    @click.option("--date-from", default=None, help="Only include payments on/after YYYY-MM-DD.")
    @click.option("--date-to", default=None, help="Only include payments on/before YYYY-MM-DD.")
    @click.option("--receipt-account-code", default=None, help="Explicit receipt account code override.")
    def accounting_backfill_payments(apply_changes, payment_id, date_from, date_to, receipt_account_code):
        from datetime import date as date_cls
        from .models import Payment, AccountingAccount, AccountingJournalEntry
        from .accounting import post_loan_payment, AccountingError, money

        start = date_cls.fromisoformat(date_from) if date_from else None
        end = date_cls.fromisoformat(date_to) if date_to else None
        summary = {"created": 0, "skipped": 0, "failed": 0, "mismatched": 0}
        query = Payment.query
        if payment_id:
            query = query.filter_by(id=payment_id)
        if start:
            query = query.filter(Payment.collection_date >= start)
        if end:
            query = query.filter(Payment.collection_date <= end)
        for payment in query.all():
            existing = AccountingJournalEntry.query.filter_by(idempotency_key=f"LOAN_PAYMENT:{payment.id}").first()
            if existing:
                summary["skipped"] += 1
                continue
            total = money(payment.amount_collected)
            allocated = money(money(payment.principal_paid) + money(payment.interest_paid) + money(payment.penalty_paid) + money(payment.other_fee_paid))
            if allocated != total:
                summary["mismatched"] += 1
                click.echo({"payment_id": payment.id, "error": "Stored payment allocation does not match amount collected"})
                continue
            try:
                receipt_account = AccountingAccount.query.filter_by(account_code=receipt_account_code).first() if receipt_account_code else None
                post_loan_payment(payment, receipt_account=receipt_account)
                summary["created"] += 1
            except AccountingError as exc:
                summary["failed"] += 1
                click.echo({"payment_id": payment.id, "error": str(exc)})
        if not apply_changes:
            db.session.rollback()
        else:
            db.session.commit()
        click.echo(summary)

    @app.route("/health", methods=["GET"])
    def health_check():
        return jsonify({"status": "ok"})

    @app.cli.command("accrue-loan-interest")
    @click.option("--as-of-date", default=None, help="Accrue due loan interest through YYYY-MM-DD; defaults to today.")
    @click.option("--loan-id", type=int, default=None, help="Accrue one loan only.")
    def accrue_loan_interest_cli(as_of_date, loan_id):
        from datetime import date as date_cls
        from .accounting import accrue_due_loan_interest
        as_of = date_cls.fromisoformat(as_of_date) if as_of_date else date_cls.today()
        summary = accrue_due_loan_interest(as_of, loan_id=loan_id, historical=True)
        if summary.get("errors"):
            app.logger.error("Loan interest accrual completed with errors: %s", summary)
        db.session.commit()
        click.echo({**summary, "total_interest_accrued": str(summary["total_interest_accrued"])})
        if summary.get("errors"):
            raise click.ClickException("Some accruals failed")

    return app


def _warn_on_weak_jwt_secret(app):
    secret = app.config.get("JWT_SECRET_KEY") or ""
    if len(secret.encode("utf-8")) < 32:
        app.logger.warning(
            "JWT_SECRET_KEY is shorter than 32 bytes; set a strong random secret for production."
        )
