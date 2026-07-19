from datetime import date, datetime, timedelta
from decimal import Decimal
from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import get_jwt_identity
from sqlalchemy import func

from ..currency import CURRENCY_CODE, format_currency
from ..extensions import db
from ..models import Loan, LoanApplication, Payment, Customer, AccountingAccount, CustomerCreditBalance
from ..loan_ledger import generate_loan_ledger
from ..accounting import AccountingError, allocate_payment, money, post_loan_payment, validate_collection_account
from .loan_applications import (
    STATUS_STAFF_APPROVED,
    STATUS_SUBMITTED,
    apply_status_transition,
    build_application_response,
)
from .utils import role_required

staff_bp = Blueprint("staff", __name__, url_prefix="/staff")


@staff_bp.route("/customers", methods=["GET"])
@role_required(["admin", "staff"])
def list_customers():
    customers = Customer.query.all()
    results = [
        {
            "id": c.id,
            "customer_code": c.customer_code,
            "full_name": c.full_name,
            "mobile": c.mobile,
            "status": c.status,
            "lead_status": c.lead_status,
            "kyc_status": c.kyc_status,
            "eligibility_status": c.eligibility_status,
        }
        for c in customers
    ]
    return jsonify(results)


@staff_bp.route("/payments", methods=["POST"])
@role_required(["admin", "staff"])
def record_payment():
    data = request.get_json(silent=True) or {}
    loan_id = data.get("loan_id")
    current_app.logger.info(
        "Record payment request path=%s method=%s loan_id=%s payload_keys=%s",
        request.path, request.method, loan_id, sorted(data.keys())
    )
    payment_method = (data.get("collection_method") or data.get("payment_method") or "CASH_OFFICE").upper()
    if payment_method == "CASH": payment_method = "CASH_OFFICE"
    if payment_method == "BANK": payment_method = "BANK_TRANSFER"
    remarks = data.get("remarks")
    transaction_reference = data.get("transaction_reference")
    receipt_account = None
    collector_id = data.get("collector_id")
    account_id = data.get("collection_account_id") or data.get("receipt_account_id")
    if payment_method == "CASH_COLLECTOR" and (not collector_id or not account_id):
        return jsonify({"error": "Collector setup incomplete", "message": "The selected collector has no active posting collection account."}), 422
    if account_id is not None:
        try:
            receipt_account = validate_collection_account(AccountingAccount.query.get(int(account_id)), payment_method, collector_id)
        except AccountingError as exc:
            status = 422 if payment_method == "CASH_COLLECTOR" else 400
            return jsonify({"error": "Collector setup incomplete", "message": str(exc)}), status
        except (TypeError, ValueError):
            return jsonify({"message": "collection_account_id must be a valid account id"}), 400

    if not loan_id:
        return jsonify({"message": "loan_id is required"}), 400

    try:
        amount = money(Decimal(str(data.get("amount_collected", "0"))))
    except Exception:
        return jsonify({"message": "amount_collected must be a valid number"}), 400

    if amount <= 0:
        return jsonify({"message": "amount_collected must be greater than zero"}), 400

    loan = Loan.query.get(loan_id)
    if not loan:
        return jsonify({"message": "Loan not found"}), 404

    if str(loan.status or "").strip().upper() not in {"ACTIVE", "OVERDUE"}:
        return (
            jsonify({"message": "Payments can only be recorded for active loans"}),
            400,
        )

    collection_date = data.get("collection_date")
    try:
        collection_date_value = (
            date.fromisoformat(collection_date) if collection_date else date.today()
        )
    except Exception:
        return (
            jsonify({"message": "collection_date must be ISO formatted (YYYY-MM-DD)"}),
            400,
        )

    try:
        if not loan.ledger_entries:
            generate_loan_ledger(loan)
            db.session.flush()
        principal_paid, interest_paid, penalty_paid, other_fee_paid = allocate_payment(loan, amount, collection_date_value)
        payment = Payment(
            loan_id=loan_id,
            amount_collected=amount,
            principal_paid=principal_paid,
            interest_paid=interest_paid,
            penalty_paid=penalty_paid,
            other_fee_paid=other_fee_paid,
            collection_date=collection_date_value,
            payment_date=collection_date_value,
            accounting_date=collection_date_value,
            collected_by_id=int(get_jwt_identity()),
            collector_id=int(collector_id) if collector_id else None,
            payment_method=payment_method,
            collection_method=payment_method,
            remarks=remarks,
            transaction_reference=transaction_reference,
            receipt_account_id=receipt_account.id if receipt_account else None,
            collection_account_id=receipt_account.id if receipt_account else None,
            bank_reference=transaction_reference,
        )
        db.session.add(payment)
        db.session.flush()
        journal = post_loan_payment(payment, int(get_jwt_identity()), receipt_account=receipt_account)
        if not payment.journal_id:
            raise AccountingError("Payment journal was not created")
        db.session.commit()
    except AccountingError as exc:
        db.session.rollback()
        current_app.logger.exception("Loan payment posting failed")
        status = 422 if payment_method == "CASH_COLLECTOR" else 400
        return jsonify({"error": "Collector setup incomplete", "message": str(exc)}), status

    acct = payment.collection_account
    allocation = {"delay_interest": f"{money(payment.penalty_paid):.2f}", "interest": f"{money(payment.interest_paid):.2f}", "principal": f"{money(payment.principal_paid):.2f}", "unapplied": f"{money(payment.other_fee_paid):.2f}"}
    credit = CustomerCreditBalance.query.filter_by(payment_id=payment.id).first()
    return jsonify({"message": "Payment recorded", "payment_id": payment.id, "receipt_number": payment.receipt_number, "journal_entry_id": payment.journal_id, "journal_number": journal.journal_no, "loan_status": loan.status, "settled_date": loan.settled_date.isoformat() if loan.settled_date else None, "total_applied_to_loan": float(money(amount - payment.other_fee_paid)), "overpayment": float(money(payment.other_fee_paid)), "outstanding_amount": float(loan.outstanding), "customer_credit": {"id": credit.id, "credit_number": credit.credit_number, "available_amount": float(credit.available_amount), "status": credit.status} if credit else None, "collection_account": {"id": acct.id, "code": acct.account_code, "name": acct.account_name} if acct else None, "allocation": allocation, "deposit_status": payment.deposit_status})


@staff_bp.route("/today-collections", methods=["GET"])
@role_required(["admin", "staff"])
def today_collections():
    today = date.today()
    payments = Payment.query.filter(Payment.collection_date == today).all()
    results = [
        {
            "loan_id": p.loan_id,
            "currency": CURRENCY_CODE,
            "amount_collected": float(p.amount_collected),
            "amount_collected_formatted": format_currency(p.amount_collected),
            "collected_by": p.collected_by_id,
            "payment_method": p.payment_method,
            "remarks": p.remarks,
            "collection_date": p.collection_date.isoformat(),
        }
        for p in payments
    ]
    return jsonify(results)


@staff_bp.route("/active-loans", methods=["GET", "OPTIONS"])
@role_required(["admin", "staff"])
def active_loans():
    logger = current_app.logger
    try:
        loans = Loan.query.filter(func.upper(func.trim(Loan.status)).in_(["ACTIVE", "OVERDUE"])).all()
        results = []

        for loan in loans:
            payments_made = len(loan.payments)
            next_due_date = loan.start_date + timedelta(days=payments_made)
            if next_due_date > loan.end_date:
                next_due_date = loan.end_date

            results.append(
                {
                    "loan_id": loan.id,
                    "customer_name": loan.customer.full_name if loan.customer else None,
                    "loan_type": getattr(loan, "loan_type", None) or "Standard",
                    "currency": CURRENCY_CODE,
                    "approved_amount": float(loan.principal_amount),
                    "approved_amount_formatted": format_currency(loan.principal_amount),
                    "outstanding_balance": float(loan.outstanding),
                    "outstanding_balance_formatted": format_currency(loan.outstanding),
                    "next_due_date": next_due_date.isoformat(),
                }
            )

        response = jsonify(results)
        logger.info("Handled %s %s with status %s", request.method, request.path, 200)
        return response
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Error handling %s %s: %s", request.method, request.path, exc)
        return jsonify({"message": "Failed to load active loans"}), 500


@staff_bp.route("/loan-applications", methods=["GET", "OPTIONS"])
@role_required(["admin", "staff"])
def staff_loan_applications():
    logger = current_app.logger
    try:
        status = request.args.get("status") or STATUS_SUBMITTED
        applications = (
            LoanApplication.query.filter_by(status=status)
            .order_by(LoanApplication.created_at.desc())
            .all()
        )

        results = []
        for app in applications:
            serialized = build_application_response(app)
            results.append(
                {
                    **serialized,
                    "customer_name": app.full_name,
                    "currency": CURRENCY_CODE,
                    "applied_amount_formatted": format_currency(app.applied_amount),
                }
            )

        response = jsonify(results)
        logger.info("Handled %s %s with status %s", request.method, request.path, 200)
        return response
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Error handling %s %s: %s", request.method, request.path, exc)
        return jsonify({"message": "Failed to load loan applications"}), 500


@staff_bp.route(
    "/loan-applications/<int:application_id>/approve", methods=["POST", "OPTIONS"]
)
@role_required(["admin", "staff"])
def staff_approve_application(application_id):
    logger = current_app.logger
    try:
        application = LoanApplication.query.get(application_id)
        if not application:
            return jsonify({"message": "Application not found"}), 404

        if application.status != STATUS_SUBMITTED:
            return (
                jsonify(
                    {
                        "message": "Only SUBMITTED applications can be staff-approved",
                        "status": application.status,
                    }
                ),
                400,
            )

        transition_error = apply_status_transition(application, STATUS_STAFF_APPROVED)
        if transition_error:
            return transition_error

        user_id = int(get_jwt_identity())
        application.assigned_officer_id = user_id

        db.session.commit()

        logger.info(
            "Application %s approved by staff user_id=%s", application.id, user_id
        )
        return jsonify(build_application_response(application))
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Error handling %s %s: %s", request.method, request.path, exc)
        db.session.rollback()
        return jsonify({"message": "Failed to approve application"}), 500


@staff_bp.route("/loans/arrears", methods=["GET"])
@role_required(["admin", "staff"])
def loans_in_arrears():
    loans = Loan.query.all()
    arrears_list = []
    for loan in loans:
        arrears_amount = loan.arrears()
        if arrears_amount > 0:
            arrears_list.append(
                {
                    "loan_id": loan.id,
                    "loan_number": loan.loan_number,
                    "customer_id": loan.customer_id,
                    "currency": CURRENCY_CODE,
                    "arrears": float(arrears_amount),
                    "arrears_formatted": format_currency(arrears_amount),
                    "outstanding": float(loan.outstanding),
                    "outstanding_formatted": format_currency(loan.outstanding),
                }
            )
    return jsonify(arrears_list)
