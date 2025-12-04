from datetime import date
from decimal import Decimal
from flask import Blueprint, request, jsonify
from flask_jwt_extended import get_jwt_identity

from ..extensions import db
from ..models import Loan, Payment, Customer
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
