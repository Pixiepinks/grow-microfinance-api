import os
import re
from datetime import datetime, date
from decimal import Decimal
from typing import Dict, List, Optional

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import get_jwt_identity, get_jwt
from werkzeug.utils import secure_filename
from sqlalchemy import func

from ..extensions import db
from ..models import Customer, LoanApplication, LoanApplicationDocument
from .utils import role_required


loan_app_bp = Blueprint("loan_applications", __name__, url_prefix="/loan-applications")


ALLOWED_LOAN_TYPES = {
    "GROW_ONLINE_BUSINESS",
    "GROW_BUSINESS",
    "GROW_PERSONAL",
    "GROW_TEAM",
}

STATUS_DRAFT = "DRAFT"
STATUS_SUBMITTED = "SUBMITTED"
STATUS_UNDER_REVIEW = "UNDER_REVIEW"
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"
STATUS_DISBURSED = "DISBURSED"

COMMON_REQUIRED_FIELDS = [
    "full_name",
    "nic_number",
    "mobile_number",
    "loan_type",
    "applied_amount",
    "tenure_months",
    "monthly_income",
    "monthly_expenses",
]

TYPE_REQUIRED_FIELDS: Dict[str, List[str]] = {
    "GROW_ONLINE_BUSINESS": [
        "online_store_name",
        "platform",
        "online_store_link",
        "average_monthly_revenue_last_3_months",
        "main_product_category",
    ],
    "GROW_BUSINESS": [
        "business_name",
        "business_address",
        "monthly_sales",
        "business_type",
    ],
    "GROW_PERSONAL": [
        "employment_type",
        "net_monthly_salary",
    ],
    "GROW_TEAM": [
        "group_name",
        "number_of_members",
        "team_leader_name",
        "team_leader_nic",
        "team_leader_mobile",
        "group_savings_amount",
        "group_business_activity",
    ],
}

TYPE_ALL_FIELDS: Dict[str, List[str]] = {
    "GROW_ONLINE_BUSINESS": TYPE_REQUIRED_FIELDS["GROW_ONLINE_BUSINESS"]
    + ["proof_screenshot_urls"],
    "GROW_BUSINESS": TYPE_REQUIRED_FIELDS["GROW_BUSINESS"]
    + [
        "business_registration_status",
        "business_reg_number",
        "business_type",
        "stock_value",
        "years_in_business",
    ],
    "GROW_PERSONAL": TYPE_REQUIRED_FIELDS["GROW_PERSONAL"]
    + [
        "employer_name",
        "job_title",
        "guarantor_name",
        "guarantor_nic",
        "guarantor_mobile",
        "guarantor_relationship",
    ],
    "GROW_TEAM": TYPE_REQUIRED_FIELDS["GROW_TEAM"] + ["member_list_document", "group_photo"],
}

COMMON_REQUIRED_DOCUMENTS = {"NIC_FRONT", "NIC_BACK", "SELFIE_NIC"}
TYPE_DOCUMENT_REQUIREMENTS = {
    "GROW_ONLINE_BUSINESS": {"STORE_SCREENSHOT"},
    "GROW_PERSONAL": {"SALARY_SLIP"},
    "GROW_TEAM": {"MEMBER_LIST", "GROUP_PHOTO"},
}


NIC_REGEX = re.compile(r"^(?:[0-9]{9}[VvXx]|[0-9]{12})$")


def parse_decimal(value) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def parse_int(value, default: Optional[int] = None) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def generate_application_number() -> str:
    today = date.today()
    date_str = today.strftime("%Y%m%d")
    daily_count = (
        db.session.query(func.count(LoanApplication.id))
        .filter(func.date(LoanApplication.created_at) == today)
        .scalar()
    )
    return f"GROW-APP-{date_str}-{(daily_count or 0) + 1:04d}"


def load_customer_id_from_request() -> Optional[int]:
    claims = get_jwt()
    user_id = int(get_jwt_identity())
    if claims.get("role") == "customer":
        customer = Customer.query.filter_by(user_id=user_id).first()
        if customer:
            return customer.id
        current_app.logger.warning(
            "Customer profile missing for user_id=%s while creating application", user_id
        )
        return None
    payload = request.get_json(silent=True) or {}
    return payload.get("customer_id")


def collect_type_specific_data(loan_type: str, data: dict) -> dict:
    fields = TYPE_ALL_FIELDS.get(loan_type, [])
    return {field: data.get(field) for field in fields if field in data}


def validate_application_payload(data: dict, loan_type: str) -> List[str]:
    errors: List[str] = []
    for field in COMMON_REQUIRED_FIELDS:
        if data.get(field) in (None, ""):
            errors.append(f"{field} is required")

    nic = data.get("nic_number")
    if nic and not NIC_REGEX.match(nic):
        errors.append("nic_number is invalid")

    if loan_type not in ALLOWED_LOAN_TYPES:
        errors.append("Invalid loan_type")

    if parse_decimal(data.get("applied_amount")) is None:
        errors.append("applied_amount must be a number")

    if parse_decimal(data.get("monthly_income")) is None:
        errors.append("monthly_income must be a number")

    if parse_decimal(data.get("monthly_expenses")) is None:
        errors.append("monthly_expenses must be a number")

    if parse_int(data.get("tenure_months")) is None:
        errors.append("tenure_months must be a number")

    for field in TYPE_REQUIRED_FIELDS.get(loan_type, []):
        if data.get(field) in (None, ""):
            errors.append(f"{field} is required for {loan_type}")

    if loan_type == "GROW_PERSONAL" and data.get("employment_type") == "salaried":
        if not data.get("employer_name"):
            errors.append("employer_name is required for salaried employment")

    if loan_type == "GROW_TEAM":
        try:
            members = int(data.get("number_of_members", 0))
            if members <= 0:
                errors.append("number_of_members must be greater than zero")
        except Exception:
            errors.append("number_of_members must be a number")

    return errors


def validate_required_documents(application: LoanApplication) -> List[str]:
    errors: List[str] = []
    existing = {doc.document_type for doc in application.documents}
    missing_common = COMMON_REQUIRED_DOCUMENTS - existing
    if missing_common:
        errors.append(f"Missing documents: {', '.join(sorted(missing_common))}")

    type_missing = TYPE_DOCUMENT_REQUIREMENTS.get(application.loan_type, set()) - existing
    if type_missing:
        errors.append(f"Missing documents for {application.loan_type}: {', '.join(sorted(type_missing))}")

    if application.loan_type == "GROW_ONLINE_BUSINESS" and "STORE_SCREENSHOT" not in existing:
        errors.append("At least one store screenshot is required")

    return errors


def build_application_response(application: LoanApplication) -> dict:
    return {
        "id": application.id,
        "application_number": application.application_number,
        "customer_id": application.customer_id,
        "loan_type": application.loan_type,
        "status": application.status,
        "applied_amount": float(application.applied_amount) if application.applied_amount is not None else None,
        "tenure_months": application.tenure_months,
        "interest_rate": float(application.interest_rate) if application.interest_rate is not None else None,
        "approved_amount": float(application.approved_amount) if application.approved_amount is not None else None,
        "approved_tenure": application.approved_tenure,
        "review_notes": application.review_notes,
        "reject_reason": application.reject_reason,
        "submitted_at": application.submitted_at.isoformat() if application.submitted_at else None,
        "approved_at": application.approved_at.isoformat() if application.approved_at else None,
        "full_name": application.full_name,
        "nic_number": application.nic_number,
        "mobile_number": application.mobile_number,
        "email": application.email,
        "address_line1": application.address_line1,
        "address_line2": application.address_line2,
        "city": application.city,
        "district": application.district,
        "province": application.province,
        "date_of_birth": application.date_of_birth.isoformat() if application.date_of_birth else None,
        "monthly_income": float(application.monthly_income) if application.monthly_income is not None else None,
        "monthly_expenses": float(application.monthly_expenses) if application.monthly_expenses is not None else None,
        "has_existing_loans": application.has_existing_loans,
        "existing_loan_details": application.existing_loan_details,
        "extra_data": application.extra_data or {},
        "documents": [
            {
                "id": d.id,
                "document_type": d.document_type,
                "file_path": d.file_path,
                "uploaded_at": d.uploaded_at.isoformat() if d.uploaded_at else None,
            }
            for d in application.documents
        ],
        "created_at": application.created_at.isoformat() if application.created_at else None,
        "updated_at": application.updated_at.isoformat() if application.updated_at else None,
    }


def assert_application_access(application: LoanApplication) -> Optional[tuple]:
    claims = get_jwt()
    role = claims.get("role")
    if role == "customer":
        customer = Customer.query.filter_by(user_id=int(get_jwt_identity())).first()
        if not customer or application.customer_id != customer.id:
            return jsonify({"message": "Access forbidden"}), 403
    return None


@loan_app_bp.route("", methods=["POST"])
@role_required(["customer", "admin", "staff"])
def create_application():
    data = request.get_json() or {}
    customer_id = load_customer_id_from_request()
    if not customer_id:
        return (
            jsonify({"message": "No customer profile found for this user", "errors": ["customer_id missing"]}),
            400,
        )

    loan_type = data.get("loan_type")
    type_data = collect_type_specific_data(loan_type, data)
    validation_errors = validate_application_payload({**data, **type_data, "loan_type": loan_type}, loan_type)
    if validation_errors:
        current_app.logger.warning(
            "Loan application validation failed for customer_id=%s: %s | payload_keys=%s",
            customer_id,
            validation_errors,
            sorted(list(data.keys())),
        )
        return jsonify({"errors": validation_errors}), 400

    application = LoanApplication(
        application_number=generate_application_number(),
        customer_id=customer_id,
        loan_type=loan_type,
        status=STATUS_DRAFT,
        applied_amount=parse_decimal(data.get("applied_amount")) or Decimal("0"),
        tenure_months=parse_int(data.get("tenure_months"), 0) or 0,
        interest_rate=parse_decimal(data.get("interest_rate")),
        full_name=data.get("full_name"),
        nic_number=data.get("nic_number"),
        mobile_number=data.get("mobile_number"),
        email=data.get("email"),
        address_line1=data.get("address_line1"),
        address_line2=data.get("address_line2"),
        city=data.get("city"),
        district=data.get("district"),
        province=data.get("province"),
        date_of_birth=date.fromisoformat(data["date_of_birth"]) if data.get("date_of_birth") else None,
        monthly_income=parse_decimal(data.get("monthly_income")),
        monthly_expenses=parse_decimal(data.get("monthly_expenses")),
        has_existing_loans=bool(data.get("has_existing_loans", False)),
        existing_loan_details=data.get("existing_loan_details"),
        extra_data=type_data,
        created_by_id=int(get_jwt_identity()),
    )

    db.session.add(application)
    db.session.commit()

    return jsonify(build_application_response(application)), 201


@loan_app_bp.route("/<int:application_id>", methods=["PUT"])
@role_required(["customer", "admin", "staff"])
def update_application(application_id):
    application = LoanApplication.query.get_or_404(application_id)
    access_error = assert_application_access(application)
    if access_error:
        return access_error

    if application.status not in {STATUS_DRAFT, STATUS_SUBMITTED}:
        return jsonify({"message": "Only draft or submitted applications can be updated"}), 400

    data = request.get_json() or {}
    loan_type = data.get("loan_type", application.loan_type)
    type_data = collect_type_specific_data(loan_type, data)

    current_payload = build_application_response(application)
    validation_source = {**current_payload, **data, **type_data, "loan_type": loan_type}
    validation_errors = validate_application_payload(validation_source, loan_type)
    if validation_errors:
        return jsonify({"errors": validation_errors}), 400

    for field in [
        "loan_type",
        "full_name",
        "nic_number",
        "mobile_number",
        "email",
        "address_line1",
        "address_line2",
        "city",
        "district",
        "province",
        "existing_loan_details",
    ]:
        if field in data:
            setattr(application, field, data.get(field))

    if "date_of_birth" in data and data.get("date_of_birth"):
        application.date_of_birth = date.fromisoformat(data.get("date_of_birth"))

    if "has_existing_loans" in data:
        application.has_existing_loans = bool(data.get("has_existing_loans"))

    if "applied_amount" in data:
        application.applied_amount = parse_decimal(data.get("applied_amount"))

    if "interest_rate" in data:
        application.interest_rate = parse_decimal(data.get("interest_rate"))

    if "monthly_income" in data:
        application.monthly_income = parse_decimal(data.get("monthly_income"))

    if "monthly_expenses" in data:
        application.monthly_expenses = parse_decimal(data.get("monthly_expenses"))

    if "tenure_months" in data:
        application.tenure_months = parse_int(data.get("tenure_months"), application.tenure_months)

    application.extra_data = {**(application.extra_data or {}), **type_data}

    db.session.commit()

    return jsonify(build_application_response(application))


@loan_app_bp.route("/<int:application_id>/submit", methods=["POST"])
@role_required(["customer", "admin", "staff"])
def submit_application(application_id):
    application = LoanApplication.query.get_or_404(application_id)
    access_error = assert_application_access(application)
    if access_error:
        return access_error

    if application.status not in {STATUS_DRAFT, STATUS_SUBMITTED, STATUS_UNDER_REVIEW}:
        return jsonify({"message": "Application cannot be submitted in its current status"}), 400

    data = request.get_json() or {}
    merged_data = {**build_application_response(application), **application.extra_data, **data}
    type_data = collect_type_specific_data(application.loan_type, data)
    merged_data.update(type_data)

    validation_errors = validate_application_payload(merged_data, application.loan_type)
    validation_errors.extend(validate_required_documents(application))
    if validation_errors:
        return jsonify({"errors": validation_errors}), 400

    application.status = STATUS_SUBMITTED
    application.submitted_at = datetime.utcnow()
    application.extra_data = {**(application.extra_data or {}), **type_data}

    db.session.commit()
    return jsonify(build_application_response(application))


@loan_app_bp.route("", methods=["GET"])
@role_required(["customer", "admin", "staff"])
def list_applications():
    claims = get_jwt()
    role = claims.get("role")
    query = LoanApplication.query

    if role == "customer":
        customer = Customer.query.filter_by(user_id=int(get_jwt_identity())).first()
        if not customer:
            return jsonify([])
        query = query.filter_by(customer_id=customer.id)
    else:
        customer_id = request.args.get("customer_id")
        if customer_id:
            query = query.filter_by(customer_id=customer_id)

    status = request.args.get("status")
    if status:
        query = query.filter_by(status=status)

    loan_type = request.args.get("loan_type")
    if loan_type:
        query = query.filter_by(loan_type=loan_type)

    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    if start_date:
        query = query.filter(LoanApplication.created_at >= datetime.fromisoformat(start_date))
    if end_date:
        query = query.filter(LoanApplication.created_at <= datetime.fromisoformat(end_date))

    applications = query.order_by(LoanApplication.created_at.desc()).all()
    return jsonify([build_application_response(app) for app in applications])


@loan_app_bp.route("/<int:application_id>", methods=["GET"])
@role_required(["customer", "admin", "staff"])
def get_application(application_id):
    application = LoanApplication.query.get_or_404(application_id)
    access_error = assert_application_access(application)
    if access_error:
        return access_error

    return jsonify(build_application_response(application))


@loan_app_bp.route("/<int:application_id>/approve", methods=["POST"])
@role_required(["admin", "staff"])
def approve_application(application_id):
    application = LoanApplication.query.get_or_404(application_id)
    data = request.get_json() or {}

    if application.status not in {STATUS_SUBMITTED, STATUS_UNDER_REVIEW}:
        return jsonify({"message": "Only submitted applications can be approved"}), 400

    approved_amount = parse_decimal(data.get("approved_amount"))
    approved_tenure = data.get("approved_tenure")
    if approved_amount is None or approved_tenure is None:
        return jsonify({"message": "approved_amount and approved_tenure are required"}), 400

    application.approved_amount = approved_amount
    application.approved_tenure = int(approved_tenure)
    application.review_notes = data.get("review_notes")
    application.status = STATUS_APPROVED
    application.approved_at = datetime.utcnow()

    db.session.commit()
    return jsonify(build_application_response(application))


@loan_app_bp.route("/<int:application_id>/reject", methods=["POST"])
@role_required(["admin", "staff"])
def reject_application(application_id):
    application = LoanApplication.query.get_or_404(application_id)
    data = request.get_json() or {}

    if application.status not in {STATUS_SUBMITTED, STATUS_UNDER_REVIEW}:
        return jsonify({"message": "Only submitted applications can be rejected"}), 400

    reason = data.get("reject_reason")
    if not reason:
        return jsonify({"message": "reject_reason is required"}), 400

    application.reject_reason = reason
    application.status = STATUS_REJECTED

    db.session.commit()
    return jsonify(build_application_response(application))


@loan_app_bp.route("/<int:application_id>/documents", methods=["POST"])
@role_required(["customer", "admin", "staff"])
def upload_document(application_id):
    application = LoanApplication.query.get_or_404(application_id)
    access_error = assert_application_access(application)
    if access_error:
        return access_error

    document_type = request.form.get("document_type")
    file = request.files.get("file")
    if not document_type or not file:
        return jsonify({"message": "document_type and file are required"}), 400

    upload_root = current_app.config.get("UPLOAD_FOLDER", "uploads")
    target_folder = os.path.join(upload_root, "loan_documents", str(application_id))
    os.makedirs(target_folder, exist_ok=True)

    filename = secure_filename(file.filename)
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    stored_name = f"{document_type}_{timestamp}_{filename}"
    file_path = os.path.join(target_folder, stored_name)
    file.save(file_path)

    document = LoanApplicationDocument(
        loan_application_id=application_id,
        document_type=document_type,
        file_path=file_path,
    )
    db.session.add(document)
    db.session.commit()

    return jsonify({"message": "Document uploaded", "document_id": document.id, "file_path": document.file_path}), 201
