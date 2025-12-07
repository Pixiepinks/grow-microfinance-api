import secrets

from flask import Blueprint, jsonify, request
from sqlalchemy import func

from ..extensions import db
from ..models import Customer, Lead, User


leads_bp = Blueprint("leads", __name__, url_prefix="/leads")


LEAD_STATUSES = {"NEW", "CONTACTED", "IN_PROGRESS", "CONVERTED", "LOST"}
DEFAULT_STATUS = "NEW"
CONVERTED_STATUS = "CONVERTED"


def lead_to_dict(lead: Lead) -> dict:
    return {
        "id": lead.id,
        "name": lead.name,
        "mobile": lead.mobile,
        "loan_type_interest": lead.loan_type_interest,
        "source": lead.source,
        "status": lead.status,
        "created_at": lead.created_at.isoformat() if lead.created_at else None,
        "customer_id": lead.customer_id,
    }


def customer_to_dict(customer: Customer) -> dict:
    return {
        "id": customer.id,
        "customer_code": customer.customer_code,
        "full_name": customer.full_name,
        "mobile": customer.mobile,
        "lead_status": customer.lead_status,
        "kyc_status": customer.kyc_status,
        "eligibility_status": customer.eligibility_status,
    }


def _generate_customer_code() -> str:
    count = db.session.query(func.count(Customer.id)).scalar() or 0
    return f"CUST-{count + 1:05d}"


def _generate_user_email(lead: Lead) -> str:
    local_part = lead.mobile.replace(" ", "") if lead.mobile else "lead"
    unique_suffix = secrets.token_hex(4)
    return f"{local_part}-{unique_suffix}@leads.local"


@leads_bp.route("", methods=["POST"])
def create_lead():
    data = request.get_json() or {}
    mobile = data.get("mobile")

    if not mobile:
        return jsonify({"message": "Mobile is required"}), 400

    lead = Lead(
        name=data.get("name"),
        mobile=mobile,
        loan_type_interest=data.get("loan_type_interest"),
        source=data.get("source"),
        status=DEFAULT_STATUS,
    )

    db.session.add(lead)
    db.session.commit()

    return jsonify(lead_to_dict(lead)), 201


@leads_bp.route("", methods=["GET"])
def list_leads():
    status = request.args.get("status")
    query = Lead.query

    if status:
        if status not in LEAD_STATUSES:
            return jsonify({"message": "Invalid status value"}), 400
        query = query.filter_by(status=status)

    leads = query.order_by(Lead.created_at.desc()).all()
    return jsonify([lead_to_dict(lead) for lead in leads])


@leads_bp.route("/<int:lead_id>/convert-to-customer", methods=["POST"])
def convert_to_customer(lead_id: int):
    lead = Lead.query.get(lead_id)

    if not lead:
        return jsonify({"message": "Lead not found"}), 404

    if lead.status == CONVERTED_STATUS and lead.customer_id:
        customer = Customer.query.get(lead.customer_id)
        response = {"message": "Lead already converted", "lead": lead_to_dict(lead)}
        if customer:
            response["customer"] = customer_to_dict(customer)
        return jsonify(response)

    customer_name = lead.name or lead.mobile or ""
    user_email = _generate_user_email(lead)

    user = User(email=user_email, name=customer_name, role="customer")
    user.set_password(secrets.token_urlsafe(12))

    customer = Customer(
        user=user,
        customer_code=_generate_customer_code(),
        full_name=customer_name,
        mobile=lead.mobile,
        lead_status=CONVERTED_STATUS,
        kyc_status="PENDING",
        eligibility_status="UNKNOWN",
    )

    db.session.add(user)
    db.session.add(customer)

    lead.status = CONVERTED_STATUS
    db.session.flush()
    lead.customer_id = customer.id

    db.session.commit()

    return jsonify({"customer": customer_to_dict(customer), "lead": lead_to_dict(lead)})
