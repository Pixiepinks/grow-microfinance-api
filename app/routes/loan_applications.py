import os
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Optional

from flask import Blueprint, current_app, jsonify, request
from flask_cors import cross_origin
from flask_jwt_extended import get_jwt_identity, get_jwt
from sqlalchemy import func

from app.supabase_client import (
    get_storage_bucket,
    get_supabase_client,
    get_upload_prefix,
)
from ..currency import CURRENCY_CODE, format_currency
from ..extensions import db
from ..models import Customer, Loan, LoanApplication, LoanApplicationDocument
from ..loan_ledger import generate_loan_ledger, money
from ..accounting import AccountingError, post_loan_disbursement, validate_funding_account
from ..models import AccountingAccount
from .utils import role_required

loan_app_bp = Blueprint("loan_applications", __name__, url_prefix="/loan-applications")
admin_api_bp = Blueprint("admin_api", __name__, url_prefix="/api")


ALLOWED_LOAN_TYPES = {
    "GROW_ONLINE_BUSINESS",
    "GROW_BUSINESS",
    "GROW_PERSONAL",
    "GROW_TEAM",
}

STATUS_DRAFT = "DRAFT"
STATUS_SUBMITTED = "SUBMITTED"
STATUS_STAFF_APPROVED = "STAFF_APPROVED"
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"
STATUS_DISBURSED = "DISBURSED"


SUPPORTED_REPAYMENT_FREQUENCIES = {"DAILY", "WEEKLY", "MONTHLY"}
SUPPORTED_INTEREST_TYPES = {"FLAT"}
RATE_QUANT = Decimal("0.0001")


def _decimal_to_float(value):
    return float(value) if value is not None else None


def _validate_and_calculate_terms(data: dict, *, allow_legacy: bool = False):
    errors = []
    approved_amount = parse_decimal(data.get("approved_amount"))
    loan_days = parse_int(data.get("loan_days"))
    repayment_frequency = (data.get("repayment_frequency") or "").upper()
    number_of_installments = parse_int(data.get("number_of_installments"))
    installment_amount = parse_decimal(data.get("installment_amount"))
    interest_type = (data.get("interest_type") or "FLAT").upper()

    if approved_amount is None: errors.append("approved_amount is required")
    elif approved_amount <= 0: errors.append("approved_amount must be greater than zero")
    if loan_days is None: errors.append("loan_days is required")
    elif loan_days <= 0: errors.append("loan_days must be greater than zero")
    if not repayment_frequency: errors.append("repayment_frequency is required")
    elif repayment_frequency not in SUPPORTED_REPAYMENT_FREQUENCIES: errors.append("repayment_frequency is unsupported")
    if number_of_installments is None: errors.append("number_of_installments is required")
    elif number_of_installments <= 0: errors.append("number_of_installments must be greater than zero")
    if installment_amount is None: errors.append("installment_amount is required")
    elif installment_amount <= 0: errors.append("installment_amount must be greater than zero")
    if interest_type not in SUPPORTED_INTEREST_TYPES: errors.append("interest_type is unsupported")
    if errors:
        return None, errors

    approved_amount = money(approved_amount)
    installment_amount = money(installment_amount)
    total_repayment = money(installment_amount * Decimal(number_of_installments))
    total_interest = money(total_repayment - approved_amount)
    if total_repayment < approved_amount:
        return None, ["total_repayment cannot be below approved_amount"]
    interest_rate = (total_interest / approved_amount * Decimal("100")).quantize(RATE_QUANT, rounding=ROUND_HALF_UP)
    return {"approved_amount": approved_amount, "loan_days": loan_days, "repayment_frequency": repayment_frequency, "number_of_installments": number_of_installments, "installment_amount": installment_amount, "total_repayment": total_repayment, "total_interest": total_interest, "interest_rate": interest_rate, "interest_type": interest_type}, []


def _application_terms(application):
    return {k: getattr(application, k, None) for k in ["approved_amount", "loan_days", "repayment_frequency", "number_of_installments", "installment_amount", "total_repayment", "total_interest", "interest_rate", "interest_type"]}


def _terms_complete(application):
    return all(getattr(application, f, None) is not None for f in ["approved_amount", "loan_days", "repayment_frequency", "number_of_installments", "installment_amount", "total_repayment", "total_interest", "interest_rate", "interest_type"])

STATUS_TRANSITIONS = {
    "staff": {
        STATUS_SUBMITTED: {STATUS_STAFF_APPROVED, STATUS_REJECTED},
    },
    "admin": {
        STATUS_SUBMITTED: {STATUS_APPROVED, STATUS_REJECTED, STATUS_STAFF_APPROVED},
        STATUS_STAFF_APPROVED: {STATUS_APPROVED, STATUS_REJECTED},
    },
}

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
    "GROW_TEAM": TYPE_REQUIRED_FIELDS["GROW_TEAM"]
    + ["member_list_document", "group_photo"],
}

COMMON_REQUIRED_DOCUMENTS = {"NIC_FRONT", "NIC_BACK", "SELFIE_NIC"}
TYPE_DOCUMENT_REQUIREMENTS = {
    "GROW_ONLINE_BUSINESS": {"STORE_SCREENSHOT"},
    "GROW_PERSONAL": {"SALARY_SLIP"},
    "GROW_TEAM": {"MEMBER_LIST", "GROUP_PHOTO"},
}

ALLOWED_DOCUMENT_TYPES = COMMON_REQUIRED_DOCUMENTS | set().union(
    *TYPE_DOCUMENT_REQUIREMENTS.values()
)


NIC_REGEX = re.compile(r"^(?:[0-9]{9}[VvXx]|[0-9]{12})$")


def parse_decimal(value) -> Optional[Decimal]:
    if value is None:
        return None
    if isinstance(value, str):
        # Accept user-facing formats such as "1,000,000" or values with currency prefixes
        cleaned = re.sub(r"[^0-9.\-]", "", value).replace(",", "")
        value = cleaned.strip()
        if value == "":
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


def _parse_iso_date(value):
    """Accept 'YYYY-MM-DD' or full ISO datetime string and return a date object safely."""
    if not value:
        return None

    # if already a date object
    if isinstance(value, date) and not isinstance(value, datetime):
        return value

    if isinstance(value, str):
        try:
            # datetime string
            if "T" in value:
                return datetime.fromisoformat(value).date()
            # pure date string
            return date.fromisoformat(value)
        except ValueError:
            # fallback: try first 10 characters
            try:
                return date.fromisoformat(value[:10])
            except Exception:
                current_app.logger.warning(
                    f"Invalid date format for date_of_birth: {value}"
                )
                return None

    return None


def generate_application_number() -> str:
    today = date.today()
    date_str = today.strftime("%Y%m%d")
    daily_count = (
        db.session.query(func.count(LoanApplication.id))
        .filter(func.date(LoanApplication.created_at) == today)
        .scalar()
    )
    return f"GROW-APP-{date_str}-{(daily_count or 0) + 1:04d}"


def normalize_application_payload(data: dict) -> dict:
    """Flattens nested payload sections sent by the web app.

    The web client groups form fields under sections like ``applicant_details``,
    ``loan_details`` and ``type_specific``. The existing validation and model
    creation logic expect a flat structure, so we merge those nested dictionaries
    (when present) into the top-level payload while keeping the original keys
    intact.
    """

    if not isinstance(data, dict):
        return {}

    normalized = {**data}
    for section in ("applicant_details", "loan_details", "type_specific"):
        section_data = data.get(section)
        if isinstance(section_data, dict):
            normalized.update({k: v for k, v in section_data.items() if v is not None})

    return normalized


def load_customer_id_from_request() -> Optional[int]:
    claims = get_jwt()
    user_id = int(get_jwt_identity())
    if claims.get("role") == "customer":
        customer = Customer.query.filter_by(user_id=user_id).first()
        if customer:
            return customer.id
        current_app.logger.warning(
            "Customer profile missing for user_id=%s while creating application",
            user_id,
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
    # TODO: Re-enable required document validation after Supabase storage is restored.
    return []


def available_application_actions(application: LoanApplication) -> List[str]:
    if application.status == STATUS_SUBMITTED:
        return ["approve", "reject"]
    if application.status == STATUS_APPROVED:
        return ["disburse"]
    return []


def build_application_response(application: LoanApplication) -> dict:
    customer = application.customer
    return {
        "id": application.id,
        "application_number": application.application_number,
        "customer_id": application.customer_id,
        "customer_name": customer.full_name if customer else application.full_name,
        "customer_code": customer.customer_code if customer else None,
        "loan_type": application.loan_type,
        "status": application.status,
        "currency": CURRENCY_CODE,
        "applied_amount": (
            float(application.applied_amount)
            if application.applied_amount is not None
            else None
        ),
        "applied_amount_formatted": (
            format_currency(application.applied_amount)
            if application.applied_amount is not None
            else None
        ),
        "tenure_months": application.tenure_months,
        "interest_rate": (
            float(application.interest_rate)
            if application.interest_rate is not None
            else None
        ),
        "approved_amount": (
            float(application.approved_amount)
            if application.approved_amount is not None
            else None
        ),
        "approved_amount_formatted": (
            format_currency(application.approved_amount)
            if application.approved_amount is not None
            else None
        ),
        "approved_tenure": application.approved_tenure,
        "loan_days": application.loan_days,
        "repayment_frequency": application.repayment_frequency,
        "number_of_installments": application.number_of_installments,
        "installment_amount": _decimal_to_float(application.installment_amount),
        "total_repayment": _decimal_to_float(application.total_repayment),
        "total_interest": _decimal_to_float(application.total_interest),
        "interest_type": application.interest_type,
        "review_notes": application.review_notes,
        "reject_reason": application.reject_reason,
        "submitted_at": (
            application.submitted_at.isoformat() if application.submitted_at else None
        ),
        "approved_at": (
            application.approved_at.isoformat() if application.approved_at else None
        ),
        "full_name": application.full_name,
        "nic_number": application.nic_number,
        "mobile_number": application.mobile_number,
        "email": application.email,
        "address_line1": application.address_line1,
        "address_line2": application.address_line2,
        "city": application.city,
        "district": application.district,
        "province": application.province,
        "date_of_birth": (
            application.date_of_birth.isoformat() if application.date_of_birth else None
        ),
        "monthly_income": (
            float(application.monthly_income)
            if application.monthly_income is not None
            else None
        ),
        "monthly_income_formatted": (
            format_currency(application.monthly_income)
            if application.monthly_income is not None
            else None
        ),
        "monthly_expenses": (
            float(application.monthly_expenses)
            if application.monthly_expenses is not None
            else None
        ),
        "monthly_expenses_formatted": (
            format_currency(application.monthly_expenses)
            if application.monthly_expenses is not None
            else None
        ),
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
        "created_at": (
            application.created_at.isoformat() if application.created_at else None
        ),
        "updated_at": (
            application.updated_at.isoformat() if application.updated_at else None
        ),
        "assigned_officer_id": application.assigned_officer_id,
        "available_actions": available_application_actions(application),
    }


def assert_application_access(application: LoanApplication) -> Optional[tuple]:
    claims = get_jwt()
    role = claims.get("role")
    if role == "customer":
        customer = Customer.query.filter_by(user_id=int(get_jwt_identity())).first()
        if not customer or application.customer_id != customer.id:
            return jsonify({"message": "Access forbidden"}), 403
    return None


def apply_status_transition(
    application: LoanApplication, target_status: str
) -> Optional[tuple]:
    claims = get_jwt()
    role = claims.get("role")
    allowed = STATUS_TRANSITIONS.get(role, {}).get(application.status, set())

    if target_status not in allowed:
        return (
            jsonify(
                {
                    "message": "Invalid status transition",
                    "from": application.status,
                    "to": target_status,
                    "role": role,
                }
            ),
            400,
        )

    application.status = target_status
    return None


@loan_app_bp.route("", methods=["POST"])
@role_required(["customer", "admin", "staff"])
def create_application():
    claims = get_jwt()
    data = normalize_application_payload(request.get_json() or {})
    customer_id = load_customer_id_from_request()
    if not customer_id:
        return (
            jsonify(
                {
                    "message": "No customer profile found for this user",
                    "errors": ["customer_id missing"],
                }
            ),
            400,
        )

    customer = Customer.query.get(customer_id)
    if not customer:
        return jsonify({"message": "Invalid customer_id"}), 400

    if customer.kyc_status != "APPROVED":
        return jsonify({"message": "Customer KYC is not approved"}), 400

    if customer.eligibility_status != "ELIGIBLE":
        return (
            jsonify({"message": "Customer is not eligible for loan application"}),
            400,
        )

    loan_type = data.get("loan_type")
    type_data = collect_type_specific_data(loan_type, data)
    validation_errors = validate_application_payload(
        {**data, **type_data, "loan_type": loan_type}, loan_type
    )
    if validation_errors:
        current_app.logger.warning(
            "Loan application validation failed for customer_id=%s: %s | payload_keys=%s",
            customer_id,
            validation_errors,
            sorted(list(data.keys())),
        )
        return jsonify({"errors": validation_errors}), 400

    initial_status = (
        STATUS_SUBMITTED if claims.get("role") == "customer" else STATUS_DRAFT
    )
    application = LoanApplication(
        application_number=generate_application_number(),
        customer_id=customer_id,
        loan_type=loan_type,
        status=initial_status,
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
        date_of_birth=_parse_iso_date(data.get("date_of_birth")),
        monthly_income=parse_decimal(data.get("monthly_income")),
        monthly_expenses=parse_decimal(data.get("monthly_expenses")),
        has_existing_loans=bool(data.get("has_existing_loans", False)),
        existing_loan_details=data.get("existing_loan_details"),
        extra_data=type_data,
        created_by_id=int(get_jwt_identity()),
        submitted_at=datetime.utcnow() if initial_status == STATUS_SUBMITTED else None,
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
        return (
            jsonify({"message": "Only draft or submitted applications can be updated"}),
            400,
        )

    data = normalize_application_payload(request.get_json() or {})
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
        application.tenure_months = parse_int(
            data.get("tenure_months"), application.tenure_months
        )

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

    if application.status not in {STATUS_DRAFT, STATUS_SUBMITTED}:
        return (
            jsonify(
                {"message": "Application cannot be submitted in its current status"}
            ),
            400,
        )

    data = normalize_application_payload(request.get_json() or {})
    existing_extra_data = application.extra_data or {}
    merged_data = {
        **build_application_response(application),
        **existing_extra_data,
        **data,
    }
    type_data = collect_type_specific_data(application.loan_type, data)
    merged_data.update(type_data)

    validation_errors = validate_application_payload(merged_data, application.loan_type)
    validation_errors.extend(validate_required_documents(application))
    if validation_errors:
        return (
            jsonify({"message": "Validation failed", "errors": validation_errors}),
            400,
        )

    application.status = STATUS_SUBMITTED
    application.submitted_at = datetime.utcnow()
    application.extra_data = {**existing_extra_data, **type_data}

    db.session.commit()
    return jsonify(build_application_response(application))


@loan_app_bp.route("", methods=["GET", "OPTIONS"])
@cross_origin(
    origins=os.getenv(
        "CORS_ORIGINS", "https://grow-microfinance-app-production.up.railway.app"
    ),
    methods=["GET", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
@role_required(["customer", "admin", "staff"])
def list_applications():
    logger = current_app.logger
    try:
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

        requested_status = request.args.get("status")
        if requested_status:
            valid_statuses = {
                STATUS_DRAFT,
                STATUS_SUBMITTED,
                STATUS_STAFF_APPROVED,
                STATUS_APPROVED,
                STATUS_REJECTED,
                STATUS_DISBURSED,
            }
            if requested_status not in valid_statuses:
                return jsonify({"message": "Invalid status value"}), 400
            status = requested_status
        else:
            if role == "staff":
                status = STATUS_SUBMITTED
            elif role == "admin":
                status = STATUS_STAFF_APPROVED
            else:
                status = None

        if status:
            query = query.filter_by(status=status)

        loan_type = request.args.get("loan_type")
        if loan_type:
            query = query.filter_by(loan_type=loan_type)

        start_date = request.args.get("start_date")
        end_date = request.args.get("end_date")
        if start_date:
            query = query.filter(
                LoanApplication.created_at >= datetime.fromisoformat(start_date)
            )
        if end_date:
            query = query.filter(
                LoanApplication.created_at <= datetime.fromisoformat(end_date)
            )

        applications = query.order_by(LoanApplication.created_at.desc()).all()
        response = jsonify([build_application_response(app) for app in applications])
        logger.info("Handled %s %s with status %s", request.method, request.path, 200)
        return response
    except ValueError as exc:
        logger.exception(
            "Invalid request parameter while handling %s %s: %s",
            request.method,
            request.path,
            exc,
        )
        return jsonify({"message": "Invalid request parameters"}), 400
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Error handling %s %s: %s", request.method, request.path, exc)
        return jsonify({"message": "Failed to load loan applications"}), 500


@admin_api_bp.route("/loan-applications", methods=["GET", "OPTIONS"])
@cross_origin(
    origins=os.getenv(
        "CORS_ORIGINS", "https://grow-microfinance-app-production.up.railway.app"
    ),
    methods=["GET", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
@role_required(["admin"])
def admin_list_all_applications():
    """Return all loan applications for admin dashboards.

    The Admin Loan Applications list in the web app relies on this endpoint to
    show every application regardless of its current status. Optional status
    filtering is provided for table filters while keeping "ALL" as the default.
    """

    logger = current_app.logger
    try:
        status_param = (request.args.get("status") or "ALL").upper()
        status_aliases = {"UNDER_REVIEW": STATUS_STAFF_APPROVED}

        normalized_status = status_aliases.get(status_param, status_param)
        valid_statuses = {
            STATUS_DRAFT,
            STATUS_SUBMITTED,
            STATUS_STAFF_APPROVED,
            STATUS_APPROVED,
            STATUS_REJECTED,
            STATUS_DISBURSED,
        }

        query = LoanApplication.query
        if normalized_status != "ALL":
            if normalized_status not in valid_statuses:
                return jsonify({"message": "Invalid status value"}), 400
            query = query.filter_by(status=normalized_status)

        applications = query.order_by(LoanApplication.created_at.desc()).all()
        response = jsonify([build_application_response(app) for app in applications])
        logger.info("Handled %s %s with status %s", request.method, request.path, 200)
        return response
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Error handling %s %s: %s", request.method, request.path, exc)
        return jsonify({"message": "Failed to load loan applications"}), 500


def _serialize_admin_customer(customer: Customer) -> dict:
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


@admin_api_bp.route("/admin/customers", methods=["GET", "OPTIONS"])
@cross_origin(
    origins=os.getenv(
        "CORS_ORIGINS", "https://grow-microfinance-app-production.up.railway.app"
    ),
    methods=["GET", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
@role_required(["admin"])
def admin_list_customers():
    """Return all customers for the admin dashboard."""

    logger = current_app.logger
    try:
        customers = Customer.query.order_by(Customer.id.asc()).all()
        response = jsonify(
            {
                "success": True,
                "customers": [
                    _serialize_admin_customer(customer) for customer in customers
                ],
            }
        )
        logger.info("Handled %s %s with status %s", request.method, request.path, 200)
        return response
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Error handling %s %s: %s", request.method, request.path, exc)
        return jsonify({"message": "Failed to load customers"}), 500


@admin_api_bp.route(
    "/admin/customers/<int:customer_id>", methods=["GET", "PUT", "OPTIONS"]
)
@cross_origin(
    origins=os.getenv(
        "CORS_ORIGINS", "https://grow-microfinance-app-production.up.railway.app"
    ),
    methods=["GET", "PUT", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
@role_required(["admin"])
def admin_customer_detail(customer_id: int):
    """Return or update a single customer for the admin dashboard."""

    logger = current_app.logger
    try:
        customer = Customer.query.get(customer_id)
        if not customer:
            return jsonify({"message": "Customer not found"}), 404

        if request.method == "PUT":
            payload = request.get_json(silent=True) or {}
            updatable_fields = {
                "full_name",
                "nic_number",
                "mobile",
                "address",
                "business_type",
                "status",
                "lead_status",
                "kyc_status",
                "eligibility_status",
            }

            updated = False
            for field in updatable_fields:
                if field in payload:
                    setattr(customer, field, payload[field])
                    updated = True

            if not updated:
                return jsonify({"message": "No supported fields provided"}), 400

            db.session.commit()
            response = jsonify(
                {"success": True, "customer": _serialize_admin_customer(customer)}
            )
            logger.info(
                "Handled %s %s with status %s", request.method, request.path, 200
            )
            return response

        response = jsonify(
            {"success": True, "customer": _serialize_admin_customer(customer)}
        )
        logger.info("Handled %s %s with status %s", request.method, request.path, 200)
        return response
    except Exception as exc:  # pragma: no cover - defensive logging
        db.session.rollback()
        logger.exception("Error handling %s %s: %s", request.method, request.path, exc)
        return jsonify({"message": "Failed to process customer"}), 500


@loan_app_bp.route("/awaiting-review", methods=["GET", "OPTIONS"])
@cross_origin()
@role_required(["admin", "staff"])
def list_awaiting_review_applications():
    """List applications pending staff review (SUBMITTED status).

    This dedicated endpoint mirrors the behaviour of the general listing
    route but fixes the status filter to ``SUBMITTED`` so staff dashboards
    can reliably fetch awaiting-review items without needing a query
    parameter.
    """

    logger = current_app.logger
    try:
        applications = (
            LoanApplication.query.filter_by(status=STATUS_SUBMITTED)
            .order_by(LoanApplication.created_at.desc())
            .all()
        )
        response = jsonify([build_application_response(app) for app in applications])
        logger.info("Handled %s %s with status %s", request.method, request.path, 200)
        return response
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Error handling %s %s: %s", request.method, request.path, exc)
        return jsonify({"message": "Failed to load applications"}), 500


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
    claims = get_jwt()
    role = claims.get("role")
    target_status = STATUS_APPROVED if role == "admin" else STATUS_STAFF_APPROVED

    transition_error = apply_status_transition(application, target_status)
    if transition_error:
        return transition_error

    if target_status == STATUS_APPROVED:
        legacy_payload = not any(k in data for k in ["loan_days", "repayment_frequency", "number_of_installments", "installment_amount", "interest_type"])
        if legacy_payload:
            approved_amount = parse_decimal(data.get("approved_amount", application.approved_amount or application.applied_amount))
            approved_tenure = parse_int(data.get("approved_tenure", application.approved_tenure or application.tenure_months))
            if approved_amount is None or approved_tenure is None:
                return jsonify({"message": "approved_amount and approved_tenure must be valid"}), 400
            application.approved_amount = money(approved_amount)
            application.approved_tenure = approved_tenure
        else:
            terms, errors = _validate_and_calculate_terms(data)
            if errors:
                return jsonify({"message": "Validation failed", "errors": errors}), 400
            for field, value in terms.items():
                setattr(application, field if field != "interest_rate" else "interest_rate", value)
            application.approved_tenure = parse_int(data.get("approved_tenure"), application.approved_tenure or application.tenure_months)
    else:
        approved_amount = parse_decimal(data.get("approved_amount", application.approved_amount or application.applied_amount))
        approved_tenure = parse_int(data.get("approved_tenure", application.approved_tenure or application.tenure_months))
        if approved_amount is None or approved_tenure is None:
            return jsonify({"message": "approved_amount and approved_tenure must be valid"}), 400
        application.approved_amount = money(approved_amount)
        application.approved_tenure = approved_tenure

    application.review_notes = data.get("review_notes")
    if target_status == STATUS_APPROVED:
        application.approved_at = datetime.utcnow()
    else:
        application.assigned_officer_id = int(get_jwt_identity())
    db.session.commit()
    return jsonify(build_application_response(application))

def generate_loan_number() -> str:
    today = date.today()
    date_str = today.strftime("%Y%m%d")
    daily_count = (
        db.session.query(func.count(Loan.id))
        .filter(func.date(Loan.created_at) == today)
        .scalar()
    )
    return f"GROW-LOAN-{date_str}-{(daily_count or 0) + 1:04d}"


@loan_app_bp.route("/<int:application_id>/disburse", methods=["POST"])
@role_required(["admin"])
def disburse_application(application_id):
    application = LoanApplication.query.get_or_404(application_id)
    if application.status != STATUS_APPROVED:
        return jsonify({"message": "Only APPROVED applications can be disbursed", "status": application.status}), 400

    data = request.get_json(silent=True) or {}
    funding_account = None
    if data.get("funding_account_id") is not None:
        try:
            funding_account = validate_funding_account(AccountingAccount.query.get(int(data["funding_account_id"])))
        except (TypeError, ValueError):
            return jsonify({"message": "funding_account_id must be a valid account id"}), 400
        except AccountingError as exc:
            return jsonify({"message": str(exc)}), 400

    disbursement_date = date.fromisoformat(data.get("disbursement_date")) if data.get("disbursement_date") else date.today()
    use_flexible_terms = _terms_complete(application)
    if use_flexible_terms:
        terms, errors = _validate_and_calculate_terms(_application_terms(application))
        if errors:
            return jsonify({"message": "Approved commercial terms are incomplete or invalid", "errors": errors}), 400
        principal = terms["approved_amount"]
        loan_days = terms["loan_days"]
        start_date = disbursement_date
        maturity_date = start_date + timedelta(days=loan_days)
        end_date = maturity_date
        total_payable = terms["total_repayment"]
        daily_installment = money(total_payable / Decimal(loan_days))
        loan_kwargs = {
            "loan_days": loan_days, "repayment_frequency": terms["repayment_frequency"],
            "number_of_installments": terms["number_of_installments"], "installment_amount": terms["installment_amount"],
            "total_repayment": terms["total_repayment"], "total_interest": terms["total_interest"],
            "interest_type": terms["interest_type"], "maturity_date": maturity_date,
        }
        interest_rate = terms["interest_rate"]
        payment_interval_days = {"DAILY": 1, "WEEKLY": 7, "MONTHLY": 30}[terms["repayment_frequency"]]
        total_days = loan_days
    else:
        principal = application.approved_amount or application.applied_amount
        tenure_months = application.approved_tenure or application.tenure_months
        interest_rate = application.interest_rate or Decimal("0")
        total_days = max(int(tenure_months or 0) * 30, 1)
        payment_interval_days = int(data.get("payment_interval_days", 7) or 7)
        start_date = disbursement_date
        end_date = start_date + timedelta(days=total_days - 1)
        maturity_date = end_date
        total_payable = money(Decimal(principal) + (Decimal(principal) * (Decimal(interest_rate) / Decimal("100"))))
        daily_installment = money(total_payable / Decimal(total_days))
        loan_kwargs = {"maturity_date": maturity_date}

    loan = Loan(loan_number=generate_loan_number(), customer_id=application.customer_id, principal_amount=principal, interest_rate=interest_rate, total_days=total_days, payment_interval_days=payment_interval_days, daily_installment=daily_installment, total_payable=total_payable, start_date=start_date, end_date=end_date, status="ACTIVE", created_by_id=int(get_jwt_identity()), **loan_kwargs)

    try:
        db.session.add(loan); db.session.flush(); generate_loan_ledger(loan)
        application.status = STATUS_DISBURSED
        post_loan_disbursement(loan, int(get_jwt_identity()), funding_account=funding_account, disbursement_date=disbursement_date)
        db.session.commit()
        return jsonify({"message": "Loan disbursed", "loan_id": loan.id, "loan_number": loan.loan_number, "loan": {"principal_amount": float(loan.principal_amount), "loan_days": loan.loan_days, "repayment_frequency": loan.repayment_frequency, "number_of_installments": loan.number_of_installments, "installment_amount": _decimal_to_float(loan.installment_amount), "total_repayment": _decimal_to_float(loan.total_repayment), "total_interest": _decimal_to_float(loan.total_interest), "interest_rate": float(loan.interest_rate), "interest_type": loan.interest_type, "start_date": loan.start_date.isoformat(), "maturity_date": loan.maturity_date.isoformat() if loan.maturity_date else None, "final_installment_due_date": loan.final_installment_due_date.isoformat() if loan.final_installment_due_date else None}, "application": build_application_response(application)}), 201
    except AccountingError as exc:
        db.session.rollback(); return jsonify({"message": "Accounting posting failed", "error": str(exc)}), 400
    except Exception as exc:
        db.session.rollback(); current_app.logger.exception("Failed to disburse application %s: %s", application.id, exc); return jsonify({"message": "Failed to disburse loan"}), 500


@loan_app_bp.route("/<int:application_id>/reject", methods=["POST"])
@role_required(["admin", "staff"])
def reject_application(application_id):
    application = LoanApplication.query.get_or_404(application_id)
    data = request.get_json() or {}

    reason = data.get("reject_reason")
    if not reason:
        return jsonify({"message": "reject_reason is required"}), 400

    transition_error = apply_status_transition(application, STATUS_REJECTED)
    if transition_error:
        return transition_error

    application.reject_reason = reason

    db.session.commit()
    return jsonify(build_application_response(application))


def upload_document_to_supabase(
    loan_application_id: int, document_type: str, file_storage
) -> str:
    supabase = get_supabase_client()
    bucket = get_storage_bucket()
    prefix = get_upload_prefix()

    original_name = file_storage.filename or "file"
    ext = os.path.splitext(original_name)[1]
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    safe_type = (document_type or "DOC").upper()

    object_path = f"{prefix}/{loan_application_id}/{safe_type}_{timestamp}{ext}"

    file_bytes = file_storage.read()

    supabase.storage.from_(bucket).upload(
        path=object_path,
        file=file_bytes,
        file_options={
            "content-type": file_storage.mimetype or "application/octet-stream"
        },
    )

    return object_path


def _file_size(file_storage) -> int | None:
    stream = getattr(file_storage, "stream", None)
    if not stream or not hasattr(stream, "tell") or not hasattr(stream, "seek"):
        return None

    try:
        current_position = stream.tell()
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(current_position)
        return size
    except Exception:  # pragma: no cover - best-effort logging context only
        return None


def _storage_configuration_status() -> dict:
    return {
        "supabase_url_configured": bool(os.environ.get("SUPABASE_URL")),
        "supabase_service_role_key_configured": bool(
            os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        ),
        "storage_bucket_configured": bool(
            os.environ.get("SUPABASE_BUCKET_KYC") or os.environ.get("SUPABASE_BUCKET")
        ),
        "upload_prefix_configured": bool(os.environ.get("SUPABASE_UPLOAD_FOLDER")),
    }


def _safe_exception_message(exc: Exception) -> str:
    message = str(exc) or exc.__class__.__name__
    if isinstance(exc, KeyError):
        message = f"Missing required storage configuration: {exc.args[0]}"
    return message


@loan_app_bp.route("/<int:application_id>/documents", methods=["POST"])
@role_required(["customer", "admin", "staff"])
def upload_document(application_id):
    document_type = None
    file = None
    try:
        application = LoanApplication.query.get_or_404(application_id)
        access_error = assert_application_access(application)
        if access_error:
            return access_error

        document_type = request.form.get("document_type")
        file = request.files.get("file")
        if not document_type:
            return jsonify({"message": "document_type is required"}), 400
        if not file or not file.filename:
            return jsonify({"message": "file is required"}), 400

        document_type = document_type.strip().upper()
        if document_type not in ALLOWED_DOCUMENT_TYPES:
            return jsonify({"message": "Invalid document_type"}), 400

        object_path = upload_document_to_supabase(application_id, document_type, file)

        document = LoanApplicationDocument(
            loan_application_id=application_id,
            document_type=document_type,
            file_path=object_path,
        )
        db.session.add(document)
        db.session.commit()

        return (
            jsonify(
                {
                    "message": "Document uploaded",
                    "document_id": document.id,
                    "file_path": document.file_path,
                }
            ),
            201,
        )
    except Exception as exc:
        db.session.rollback()
        current_app.logger.info(
            "Document upload request context: %s",
            {
                "application_id": application_id,
                "document_type": document_type,
                "uploaded_filename": getattr(file, "filename", None),
                "content_type": getattr(file, "mimetype", None),
                "file_size": _file_size(file) if file else None,
                "storage_configuration": _storage_configuration_status(),
            },
        )
        current_app.logger.exception(
            "Document upload failed for loan application %s",
            application_id,
        )
        return (
            jsonify(
                {
                    "success": False,
                    "message": "Document upload failed",
                    "error": _safe_exception_message(exc),
                }
            ),
            500,
        )
