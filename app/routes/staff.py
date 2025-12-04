from datetime import date, datetime, timedelta
from decimal import Decimal
from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import get_jwt_identity

from ..extensions import db
from ..models import Loan, LoanApplication, Payment, Customer
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
        }
        for c in customers
    ]
    return jsonify(results)


@staff_bp.route("/payments", methods=["POST"])
@role_required(["admin", "staff"])
def record_payment():
    data = request.get_json() or {}
    loan_id = data.get("loan_id")
    payment_method = data.get("payment_method", "Cash")
    remarks = data.get("remarks")

    if not loan_id:
        return jsonify({"message": "loan_id is required"}), 400

    try:
        amount = Decimal(str(data.get("amount_collected", "0")))
    except Exception:
        return jsonify({"message": "amount_collected must be a valid number"}), 400

    if amount <= 0:
        return jsonify({"message": "amount_collected must be greater than zero"}), 400

    loan = Loan.query.get(loan_id)
    if not loan:
        return jsonify({"message": "Loan not found"}), 404

    if str(loan.status).lower() != "active":
        return jsonify({"message": "Payments can only be recorded for active loans"}), 400

    collection_date = data.get("collection_date")
    try:
        collection_date_value = (
            date.fromisoformat(collection_date) if collection_date else date.today()
        )
    except Exception:
        return jsonify({"message": "collection_date must be ISO formatted (YYYY-MM-DD)"}), 400

    payment = Payment(
        loan_id=loan_id,
        amount_collected=amount,
        collection_date=collection_date_value,
        collected_by_id=int(get_jwt_identity()),
        payment_method=payment_method,
        remarks=remarks,
    )
    db.session.add(payment)
    db.session.commit()

    return jsonify({"message": "Payment recorded", "payment_id": payment.id})


@staff_bp.route("/today-collections", methods=["GET"])
@role_required(["admin", "staff"])
def today_collections():
    today = date.today()
    payments = Payment.query.filter(Payment.collection_date == today).all()
    results = [
        {
            "loan_id": p.loan_id,
            "amount_collected": float(p.amount_collected),
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
        loans = Loan.query.filter_by(status="Active").all()
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
                    "approved_amount": float(loan.principal_amount),
                    "outstanding_balance": float(loan.outstanding),
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

        results = [
            {
                "id": app.id,
                "application_number": app.application_number,
                "customer_name": app.full_name,
                "loan_type": app.loan_type,
                "applied_amount": float(app.applied_amount),
                "tenure_months": app.tenure_months,
                "status": app.status,
                "created_at": app.created_at.isoformat() if app.created_at else None,
            }
            for app in applications
        ]

        response = jsonify(results)
        logger.info("Handled %s %s with status %s", request.method, request.path, 200)
        return response
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception(
            "Error handling %s %s: %s", request.method, request.path, exc
        )
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

        transition_error = apply_status_transition(
            application, STATUS_STAFF_APPROVED
        )
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
        logger.exception(
            "Error handling %s %s: %s", request.method, request.path, exc
        )
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
                    "arrears": float(arrears_amount),
                    "outstanding": float(loan.outstanding),
                }
            )
    return jsonify(arrears_list)
