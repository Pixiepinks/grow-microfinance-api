from flask import Blueprint, jsonify, request, current_app

from ..extensions import db
from ..models import Customer
from .utils import role_required

customers_bp = Blueprint("customers", __name__, url_prefix="/customers")


def _serialize_customer(customer: Customer) -> dict:
    return {
        "id": customer.id,
        "user_id": customer.user_id,
        "customer_code": customer.customer_code,
        "full_name": customer.full_name,
        "nic_number": customer.nic_number,
        "mobile": customer.mobile,
        "address": customer.address,
        "business_type": customer.business_type,
        "status": customer.status,
        "lead_status": customer.lead_status,
        "kyc_status": customer.kyc_status,
        "eligibility_status": customer.eligibility_status,
        "created_at": customer.created_at.isoformat() if customer.created_at else None,
    }


def _get_customer_or_404(customer_id: int):
    customer = Customer.query.get(customer_id)
    if not customer:
        return None, (jsonify({"message": "Customer not found"}), 404)
    return customer, None


@customers_bp.route("", methods=["GET"])
@role_required(["admin", "staff"])
def list_customers():
    logger = current_app.logger
    try:
        kyc_status = (request.args.get("kyc_status") or "").upper() or None
        eligibility_status = (request.args.get("eligibility_status") or "").upper() or None

        query = Customer.query
        if kyc_status:
            query = query.filter_by(kyc_status=kyc_status)
        if eligibility_status:
            query = query.filter_by(eligibility_status=eligibility_status)

        customers = query.order_by(Customer.id.asc()).all()
        response = jsonify([_serialize_customer(customer) for customer in customers])
        logger.info("Handled %s %s with status %s", request.method, request.path, 200)
        return response
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Error handling %s %s: %s", request.method, request.path, exc)
        return jsonify({"message": "Failed to load customers"}), 500


def _update_kyc_status(customer_id: int, status: str):
    logger = current_app.logger
    try:
        customer, error_response = _get_customer_or_404(customer_id)
        if error_response:
            return error_response

        customer.kyc_status = status
        db.session.commit()

        response = jsonify(_serialize_customer(customer))
        logger.info("Handled %s %s with status %s", request.method, request.path, 200)
        return response
    except Exception as exc:  # pragma: no cover - defensive logging
        db.session.rollback()
        logger.exception("Error handling %s %s: %s", request.method, request.path, exc)
        return jsonify({"message": "Failed to update customer"}), 500


@customers_bp.route("/<int:customer_id>/kyc-uploaded", methods=["POST"])
@role_required(["admin", "staff"])
def mark_kyc_uploaded(customer_id: int):
    return _update_kyc_status(customer_id, "UPLOADED")


@customers_bp.route("/<int:customer_id>/kyc-under-review", methods=["POST"])
@role_required(["admin", "staff"])
def mark_kyc_under_review(customer_id: int):
    return _update_kyc_status(customer_id, "UNDER_REVIEW")


@customers_bp.route("/<int:customer_id>/kyc-approve", methods=["POST"])
@role_required(["admin", "staff"])
def approve_kyc(customer_id: int):
    return _update_kyc_status(customer_id, "APPROVED")


@customers_bp.route("/<int:customer_id>/kyc-reject", methods=["POST"])
@role_required(["admin", "staff"])
def reject_kyc(customer_id: int):
    return _update_kyc_status(customer_id, "REJECTED")


def _update_eligibility_status(customer_id: int, status: str):
    logger = current_app.logger
    try:
        customer, error_response = _get_customer_or_404(customer_id)
        if error_response:
            return error_response

        if status == "ELIGIBLE" and (customer.kyc_status or "").upper() != "APPROVED":
            return jsonify({"message": "Customer KYC must be approved before eligibility"}), 400

        customer.eligibility_status = status
        db.session.commit()

        response = jsonify(_serialize_customer(customer))
        logger.info("Handled %s %s with status %s", request.method, request.path, 200)
        return response
    except Exception as exc:  # pragma: no cover - defensive logging
        db.session.rollback()
        logger.exception("Error handling %s %s: %s", request.method, request.path, exc)
        return jsonify({"message": "Failed to update customer"}), 500


@customers_bp.route("/<int:customer_id>/mark-eligible", methods=["POST"])
@role_required(["admin", "staff"])
def mark_eligible(customer_id: int):
    return _update_eligibility_status(customer_id, "ELIGIBLE")


@customers_bp.route("/<int:customer_id>/mark-not-eligible", methods=["POST"])
@role_required(["admin", "staff"])
def mark_not_eligible(customer_id: int):
    return _update_eligibility_status(customer_id, "NOT_ELIGIBLE")
