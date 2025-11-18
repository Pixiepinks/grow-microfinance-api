from decimal import Decimal
from flask import Blueprint, jsonify
from flask_jwt_extended import get_jwt_identity

from ..models import Customer, Loan
from .utils import role_required

customer_bp = Blueprint("customer", __name__, url_prefix="/customer")


@customer_bp.route("/me", methods=["GET"])
@role_required(["customer"])
def me():
    user_id = get_jwt_identity()
    customer = Customer.query.filter_by(user_id=user_id).first()
    if not customer:
        return jsonify({"message": "Profile not found"}), 404

    return jsonify(
        {
            "id": customer.id,
            "customer_code": customer.customer_code,
            "full_name": customer.full_name,
            "mobile": customer.mobile,
            "status": customer.status,
        }
    )


@customer_bp.route("/loans", methods=["GET"])
@role_required(["customer"])
def my_loans():
    user_id = get_jwt_identity()
    customer = Customer.query.filter_by(user_id=user_id).first()
    loans = Loan.query.filter_by(customer_id=customer.id).all()

    loan_list = []
    total_outstanding = Decimal("0")
    total_arrears = Decimal("0")
    for loan in loans:
        arrears = loan.arrears()
        total_outstanding += loan.outstanding
        total_arrears += arrears
        loan_list.append(
            {
                "id": loan.id,
                "loan_number": loan.loan_number,
                "principal_amount": float(loan.principal_amount),
                "total_payable": float(loan.total_payable),
                "total_paid": float(loan.total_paid),
                "outstanding": float(loan.outstanding),
                "expected_to_date": float(loan.expected_to_date()),
                "arrears": float(arrears),
                "start_date": loan.start_date.isoformat(),
                "end_date": loan.end_date.isoformat(),
                "status": loan.status,
            }
        )

    summary = {
        "total_active_loans": len(loans),
        "total_outstanding": float(total_outstanding),
        "total_arrears": float(total_arrears),
    }
    return jsonify({"summary": summary, "loans": loan_list})


@customer_bp.route("/loans/<int:loan_id>/payments", methods=["GET"])
@role_required(["customer"])
def loan_payments(loan_id):
    user_id = get_jwt_identity()
    customer = Customer.query.filter_by(user_id=user_id).first()
    loan = Loan.query.filter_by(id=loan_id, customer_id=customer.id).first()
    if not loan:
        return jsonify({"message": "Loan not found"}), 404

    payments = [
        {
            "id": p.id,
            "collection_date": p.collection_date.isoformat(),
            "amount_collected": float(p.amount_collected),
            "payment_method": p.payment_method,
            "remarks": p.remarks,
        }
        for p in loan.payments
    ]

    loan_info = {
        "loan_number": loan.loan_number,
        "principal_amount": float(loan.principal_amount),
        "total_payable": float(loan.total_payable),
        "total_paid": float(loan.total_paid),
        "outstanding": float(loan.outstanding),
        "arrears": float(loan.arrears()),
        "start_date": loan.start_date.isoformat(),
        "end_date": loan.end_date.isoformat(),
    }

    return jsonify({"loan": loan_info, "payments": payments})
