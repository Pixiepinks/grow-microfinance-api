import os
import time
import click
from decimal import Decimal
from datetime import date
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
from .routes.investors import investors_bp
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
        request._started_at = time.monotonic()
        app.logger.info("START request method=%s path=%s", request.method, request.path)
        if request.method == "OPTIONS":
            return "", 204

    @app.after_request
    def log_request_completion(response):
        started = getattr(request, "_started_at", None)
        elapsed_ms = round((time.monotonic() - started) * 1000, 2) if started else None
        app.logger.info("END request method=%s path=%s status=%s elapsed_ms=%s", request.method, request.path, response.status_code, elapsed_ms)
        return response

    @app.teardown_request
    def cleanup_session(exception=None):
        if exception is not None:
            db.session.rollback()
        if not app.config.get("TESTING"):
            db.session.remove()

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
    app.register_blueprint(investors_bp)

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



    def _customer_backfill_command(customer_id, all_customers, confirm, apply):
        from .models import Customer
        from .customer_master import apply_backfill, backfill_customer
        if not customer_id and not all_customers:
            raise click.ClickException("Specify --customer-id or --all")
        if all_customers and not confirm:
            raise click.ClickException("--all requires --confirm")
        customers = [db.session.get(Customer, customer_id)] if customer_id else Customer.query.order_by(Customer.id).all()
        if customer_id and not customers[0]: raise click.ClickException("Customer not found")
        reports = []
        for customer in customers:
            reports.append(apply_backfill(customer) if apply else backfill_customer(customer))
        if apply: db.session.commit()
        click.echo({"mode": "apply" if apply else "preview", "customers": reports})

    @app.cli.command("preview-customer-profile-backfill")
    @click.option("--customer-id", type=int)
    @click.option("--all", "all_customers", is_flag=True)
    @click.option("--confirm", is_flag=True, help="Required with --all.")
    def preview_customer_profile_backfill(customer_id, all_customers, confirm):
        """Preview missing-only customer master backfill; makes no changes."""
        _customer_backfill_command(customer_id, all_customers, confirm, False)

    @app.cli.command("apply-customer-profile-backfill")
    @click.option("--customer-id", type=int)
    @click.option("--all", "all_customers", is_flag=True)
    @click.option("--confirm", is_flag=True, help="Required with --all.")
    def apply_customer_profile_backfill(customer_id, all_customers, confirm):
        """Apply missing-only customer master backfill."""
        _customer_backfill_command(customer_id, all_customers, confirm, True)

    @app.cli.command("reconcile-loan-settlements")
    @click.option("--preview", "preview_mode", is_flag=True, default=False, help="Report only; makes no database changes.")
    @click.option("--post", "post_mode", is_flag=True, default=False, help="Apply eligible reconciliations.")
    @click.option("--confirm", is_flag=True, default=False, help="Required with --post.")
    @click.option("--loan-id", type=int, default=None)
    def reconcile_loan_settlements(preview_mode, post_mode, confirm, loan_id):
        """Safely backfill legacy fully-paid loans, one transaction at a time."""
        from .models import Loan
        from .settlement_reconciliation import candidates, preview, post
        if post_mode == preview_mode:
            raise click.ClickException("Specify exactly one of --preview or --post")
        if post_mode and not confirm:
            raise click.ClickException("--post requires --confirm")
        results = [preview(Loan.query.get(loan_id))] if loan_id and Loan.query.get(loan_id) else candidates()
        if post_mode:
            results = []
            loans = [Loan.query.get(loan_id)] if loan_id else Loan.query.all()
            for loan in filter(None, loans):
                if (loan.status or "").strip().upper() not in {"ACTIVE", "OVERDUE", "DISBURSED", "SETTLED"}: continue
                try:
                    with db.session.begin_nested(): results.append(post(loan))
                    db.session.commit()
                except Exception as exc:
                    db.session.rollback(); results.append({"loan_id": loan.id, "processed": False, "warnings": [str(exc)]})
        summary = {"total_candidates": len(results), "exact_paid_loans": sum(1 for r in results if r.get("overpayment", 0) <= 0.01),
                   "overpaid_loans": sum(1 for r in results if r.get("overpayment", 0) > 0.01),
                   "skipped_loans": sum(1 for r in results if not r.get("processed", r.get("can_post", False))),
                   "total_proposed_customer_credits": str(sum((r.get("overpayment", 0) for r in results), 0))}
        click.echo({"loans": [{**r, **{k: str(v) for k,v in r.items() if hasattr(v, "as_tuple")}} for r in results], "summary": summary})
        if preview_mode: db.session.rollback()

    @app.cli.command("repair-reconciliation")
    @click.option("--loan-number", required=True, help="Loan number to inspect or repair.")
    @click.option("--preview", "preview_mode", is_flag=True, default=False)
    @click.option("--apply", "apply_mode", is_flag=True, default=False)
    def repair_reconciliation(loan_number, preview_mode, apply_mode):
        """Repair one legacy reconciliation without reposting its cash receipt."""
        if preview_mode == apply_mode:
            raise click.ClickException("Specify exactly one of --preview or --apply")
        from .models import Loan, CustomerCreditBalance, AccountingJournalEntry, LoanChargeWaiver
        from .settlement_reconciliation import preview, finalize_loan_reconciliation
        loan = Loan.query.filter_by(loan_number=loan_number).first()
        if not loan:
            raise click.ClickException("Loan not found")
        before = preview(loan)
        key = f"LOAN-RECONCILIATION:RECLASSIFICATION:{loan.id}"
        report = {
            "loan_id": loan.id, "loan_number": loan.loan_number,
            "existing_reconciliation": loan.settlement_reason,
            "existing_customer_credit": CustomerCreditBalance.query.filter_by(loan_id=loan.id).count(),
            "existing_reclassification_journal": bool(AccountingJournalEntry.query.filter_by(idempotency_key=key).first()),
            "existing_waiver": bool(LoanChargeWaiver.query.filter_by(loan_id=loan.id, waiver_type="DELAY_INTEREST", status="POSTED").first()),
            "expected_customer_credit": str(before["proposed_customer_credit"]),
            "proposed_reclassification": "Dr Delay Interest Receivable / Cr Customer Advances",
            "proposed_status": before["proposed_status"],
            "duplicate_risk": bool(before["customer_credit_exists"] or AccountingJournalEntry.query.filter_by(idempotency_key=key).first()),
        }
        if apply_mode:
            loan, outcome = finalize_loan_reconciliation(loan.id)
            report["applied"] = bool(outcome.get("processed"))
            report["status"] = loan.status
            report["reclassification_journal_id"] = outcome.get("reclassification_journal_id")
            report["customer_credit_balance"] = str(outcome.get("customer_credit_balance", 0))
        click.echo(report)
        if preview_mode:
            db.session.rollback()

    @app.cli.command("reconcile-loan-paid-totals")
    @click.option("--preview", "preview_mode", is_flag=True, default=False, help="Report proposed cash-paid cache corrections.")
    @click.option("--post", "post_mode", is_flag=True, default=False, help="Update only the loan cash-paid summary cache.")
    def reconcile_loan_paid_totals(preview_mode, post_mode):
        """Reconcile loan summary caches from valid posted payment receipts.

        This deliberately never touches receipts, ledger allocations, or journals.
        """
        from .models import Loan
        from .loan_totals import loan_totals, money
        if preview_mode == post_mode:
            raise click.ClickException("Specify exactly one of --preview or --post")
        rows = []
        for loan in Loan.query.order_by(Loan.id).all():
            totals = loan_totals(loan)
            current = money(loan.cash_paid_cache)
            cash_paid = totals["cash_paid"]
            difference = money(cash_paid - current)
            row = {"loan_id": loan.id, "loan_number": loan.loan_number,
                   "current_total_paid": str(current), "recalculated_cash_paid": str(cash_paid),
                   "waiver_total": str(totals["settlement_adjustments"]), "difference": str(difference),
                   "proposed_correction": "UPDATE_CASH_PAID_CACHE" if difference else "NO_CHANGE"}
            rows.append(row)
            if post_mode and difference:
                loan.cash_paid_cache = cash_paid
        if post_mode:
            db.session.commit()
        click.echo({"mode": "post" if post_mode else "preview", "loans": rows,
                    "changed": sum(1 for row in rows if row["proposed_correction"] != "NO_CHANGE")})

    @app.cli.command("accrue-investor-interest")
    @click.option("--as-of-date", default=None, help="YYYY-MM-DD cutoff date.")
    @click.option("--agreement-id", type=int, default=None)
    @click.option("--month", default=None, help="YYYY-MM month to process.")
    @click.option("--preview/--no-preview", default=False)
    @click.option("--post/--no-post", default=False)
    def accrue_investor_interest(as_of_date, agreement_id, month, preview, post):
        from datetime import date as date_cls
        from .models import InvestorFundingAgreement
        from .investor_funding import catch_up_investor_interest, month_bounds
        as_of = date_cls.fromisoformat(as_of_date) if as_of_date else date_cls.today()
        q = InvestorFundingAgreement.query.filter_by(auto_accrual_enabled=True)
        if agreement_id:
            q = q.filter_by(id=agreement_id)
        else:
            q = q.filter(InvestorFundingAgreement.status.in_(["ACTIVE", "MATURED"]))
        rows = []
        for agr in q.all():
            if month:
                _ps, pe = month_bounds(month)
                result = catch_up_investor_interest(agr.id, pe, post=post and not preview, include_partial=True)
            else:
                result = catch_up_investor_interest(agr.id, as_of, post=post and not preview)
            rows.append(result)
        if post and not preview:
            db.session.commit()
        else:
            db.session.rollback()
        print({"results": rows})


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

    @app.cli.command("accrue-delay-interest")
    @click.option("--through-date", required=True, type=click.DateTime(formats=["%Y-%m-%d"]))
    @click.option("--preview", "preview_mode", is_flag=True)
    @click.option("--post", "post_mode", is_flag=True)
    def accrue_delay_interest_cli(through_date, preview_mode, post_mode):
        """Accrue overdue delay interest; suitable for a scheduled Railway job."""
        if preview_mode == post_mode:
            raise click.ClickException("Specify exactly one of --preview or --post")
        from .accounting import accrue_delay_interest
        summary = accrue_delay_interest(through_date.date(), preview=preview_mode)
        if post_mode: db.session.commit()
        else: db.session.rollback()
        click.echo({**summary, "total_delay_interest_accrued": str(summary["total_delay_interest_accrued"])})
        if summary.get("errors"):
            raise click.ClickException("Some accruals failed")

    @app.cli.command("repair-loan-ledger")
    @click.option("--loan-id", type=int, default=None)
    @click.option("--all", "all_loans", is_flag=True, default=False)
    @click.option("--preview", "preview_mode", is_flag=True, default=False)
    @click.option("--apply", "apply_changes", is_flag=True, default=False)
    def repair_loan_ledger_cli(loan_id, all_loans, preview_mode, apply_changes):
        """Preview or explicitly apply contractual-ledger date/allocation repairs.

        Applying changes only changes derived ledger state.  The report flags
        cases whose immutable receipt/journal allocation requires a separately
        approved accounting correction.
        """
        if preview_mode == apply_changes or (loan_id is None) == (not all_loans):
            raise click.ClickException("Specify exactly one of --loan-id/--all and --preview/--apply")
        from .models import Loan
        from .loan_ledger import recalculate_loan_ledger
        loans = [Loan.query.get(loan_id)] if loan_id else Loan.query.order_by(Loan.id).all()
        reports = []
        for loan in filter(None, loans):
            report = recalculate_loan_ledger(loan.id)
            report["accounting_impact"] = "CONTROLLED_ADJUSTMENT_REQUIRED" if any(
                money(p.penalty_paid) > 0 for p in loan.payments if (p.status or "").upper() == "POSTED"
            ) else "NONE"
            report["journal_adjustment_required"] = report["accounting_impact"] != "NONE"
            reports.append(report)
        if apply_changes:
            db.session.commit()
        else:
            db.session.rollback()
        click.echo({"mode": "apply" if apply_changes else "preview", "loans": reports})

    @app.cli.command("repair-loan-statuses")
    @click.option("--loan-id", type=int, default=None)
    @click.option("--all", "all_loans", is_flag=True, default=False)
    @click.option("--preview", "preview_mode", is_flag=True, default=False)
    @click.option("--apply", "apply_changes", is_flag=True, default=False)
    def repair_loan_statuses_cli(loan_id, all_loans, preview_mode, apply_changes):
        """Preview or explicitly repair loan statuses from contractual balances."""
        if preview_mode == apply_changes or (loan_id is None) == (not all_loans):
            raise click.ClickException("Specify exactly one of --loan-id/--all and --preview/--apply")
        from .models import Loan
        from .loan_status import contractual_balances, update_loan_settlement_status
        loans = [Loan.query.get(loan_id)] if loan_id else Loan.query.order_by(Loan.id).all()
        reports = []
        for loan in filter(None, loans):
            current = loan.status
            balances = contractual_balances(loan)
            if (current or "").strip().upper() in {"WRITTEN_OFF", "CANCELLED"}:
                proposed = current
                reason = "status is protected from automatic settlement repair"
            else:
                proposed = "SETTLED" if (balances["principal_outstanding"] <= Decimal("0.01") and balances["contractual_interest_outstanding"] <= Decimal("0.01")) else "ACTIVE"
                reason = "contractual principal and interest are within 0.01 tolerance" if proposed == "SETTLED" else "contractual balance remains"
            reports.append({"loan_id": loan.id, "current_status": current, "proposed_status": proposed,
                            **{key: str(value.quantize(Decimal("0.01"))) for key, value in balances.items()},
                            "settlement_date_source": "existing settled_date" if loan.settled_date else "not changed by status repair",
                            "reason": reason})
            if apply_changes:
                update_loan_settlement_status(loan.id, loan.settled_date, loan.settled_by_id, loan=loan)
        if apply_changes:
            db.session.commit()
        else:
            db.session.rollback()
        click.echo({"mode": "apply" if apply_changes else "preview", "loans": reports})

    @app.cli.command("repair-loan-status")
    @click.option("--loan-number", required=True)
    @click.option("--preview", "preview_mode", is_flag=True, default=False)
    @click.option("--apply", "apply_changes", is_flag=True, default=False)
    def repair_loan_status_cli(loan_number, preview_mode, apply_changes):
        """Repair one persisted Loan.status without changing receipts or ledgers."""
        if preview_mode == apply_changes:
            raise click.ClickException("Specify exactly one of --preview or --apply")
        from .models import Loan
        from .loan_status import (AUTHORITATIVE_STATUS_FIELD, contractual_balances,
                                  serialize_loan_status, update_loan_settlement_status)
        loan = Loan.query.filter_by(loan_number=loan_number).first()
        if loan is None:
            raise click.ClickException("Loan not found")
        balances = contractual_balances(loan)
        proposed = ("SETTLED" if balances["principal_outstanding"] <= Decimal("0.01")
                    and balances["contractual_interest_outstanding"] <= Decimal("0.01")
                    else "ACTIVE")
        report = {"loan_number": loan.loan_number, "authoritative_status_column": AUTHORITATIVE_STATUS_FIELD,
                  "current_database_status": serialize_loan_status(loan), "proposed_database_status": proposed,
                  **{key: f"{value:.2f}" for key, value in balances.items()},
                  "settled_at": loan.settled_at.isoformat() if loan.settled_at else None,
                  "reason": "contractual principal and interest are within 0.01 tolerance" if proposed == "SETTLED" else "contractual balance remains"}
        if apply_changes:
            update_loan_settlement_status(loan.id, loan.settled_date or date.today(), loan.settled_by_id, loan=loan)
            db.session.commit()
            db.session.expire_all()
            report["persisted_database_status"] = serialize_loan_status(db.session.get(Loan, loan.id))
        else:
            db.session.rollback()
        click.echo(report)

    @app.cli.command("inspect-loan-status")
    @click.option("--loan-number", required=True)
    def inspect_loan_status_cli(loan_number):
        """Show raw and serialized authoritative status values for one loan."""
        from .models import Loan
        from .loan_status import AUTHORITATIVE_STATUS_FIELD, contractual_balances, serialize_loan_status
        loan = Loan.query.filter_by(loan_number=loan_number).first()
        if loan is None:
            raise click.ClickException("Loan not found")
        balances = contractual_balances(loan)
        status = serialize_loan_status(loan)
        click.echo({"loan_table": Loan.__tablename__, "loan_primary_key": loan.id,
                    "authoritative_status_field": AUTHORITATIVE_STATUS_FIELD,
                    "raw_stored_status": loan.status, "serialized_status": status,
                    "list_endpoint_status": status, "detail_endpoint_status": status,
                    **{key: f"{value:.2f}" for key, value in balances.items()},
                    "settled_at": loan.settled_at.isoformat() if loan.settled_at else None})

    return app


def _warn_on_weak_jwt_secret(app):
    secret = app.config.get("JWT_SECRET_KEY") or ""
    if len(secret.encode("utf-8")) < 32:
        app.logger.warning(
            "JWT_SECRET_KEY is shorter than 32 bytes; set a strong random secret for production."
        )
