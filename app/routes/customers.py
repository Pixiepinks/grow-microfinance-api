import logging
import os
from datetime import date, datetime
from decimal import Decimal

from flask import Blueprint, current_app, jsonify, request

from ..extensions import db
from ..models import Customer, CustomerDocument
from ..supabase_client import build_public_url, get_storage_bucket, get_supabase_client
from .utils import role_required

logger = logging.getLogger(__name__)

customers_bp = Blueprint("customers", __name__, url_prefix="/customers")
public_bp = Blueprint("public", __name__, url_prefix="/public")


def _serialize_customer(customer: Customer) -> dict:
    return {
        **customer.to_dict(),
        "user_id": customer.user_id,
        "status": customer.status,
        "created_at": customer.created_at.isoformat() if customer.created_at else None,
    }


def _get_customer_or_404(customer_id: int):
    customer = Customer.query.get(customer_id)
    if not customer:
        return None, (jsonify({"message": "Customer not found"}), 404)
    return customer, None


def _get_customer_by_code(customer_code: str) -> Customer | None:
    if not customer_code:
        return None
    return Customer.query.filter_by(customer_code=customer_code).first()


def save_customer_document_file(customer_id: int, uploaded_file, document_type: str) -> str:
    supabase = get_supabase_client()
    bucket = get_storage_bucket()

    original_name = uploaded_file.filename or f"{document_type}.bin"
    ext = os.path.splitext(original_name)[1] or ".bin"
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    safe_type = (document_type or "DOC").upper()

    storage_path = f"customer_documents/{customer_id}/{safe_type}_{timestamp}{ext}"
    file_bytes = uploaded_file.read()

    supabase.storage.from_(bucket).upload(
        path=storage_path,
        file=file_bytes,
        file_options={"content-type": uploaded_file.mimetype or "application/octet-stream"},
    )

    return build_public_url(storage_path)


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


@customers_bp.route("/<int:customer_id>/documents", methods=["GET"])
@role_required(["admin", "staff"])
def list_customer_documents(customer_id: int):
    customer = Customer.query.get(customer_id)
    if not customer:
        return jsonify({"message": "Customer not found"}), 404

    docs = (
        CustomerDocument.query.filter_by(customer_id=customer_id)
        .order_by(CustomerDocument.uploaded_at.desc())
        .all()
    )
    return jsonify(
        [
            {
                "id": d.id,
                "document_type": d.document_type,
                "file_path": d.file_path,
                "uploaded_at": d.uploaded_at.isoformat() if d.uploaded_at else None,
            }
            for d in docs
        ]
    )


@customers_bp.route("/<int:customer_id>/documents", methods=["POST"])
@role_required(["admin", "staff"])
def upload_customer_document(customer_id: int):
    customer = Customer.query.get(customer_id)
    if not customer:
        return jsonify({"message": "Customer not found"}), 404

    if "file" not in request.files:
        return jsonify({"message": "No file uploaded"}), 400
    uploaded_file = request.files["file"]
    document_type = request.form.get("document_type")
    if not document_type:
        return jsonify({"message": "document_type is required"}), 400

    try:
        file_path = save_customer_document_file(customer_id, uploaded_file, document_type)

        doc = CustomerDocument(
            customer_id=customer_id,
            document_type=document_type,
            file_path=file_path,
            uploaded_at=datetime.utcnow(),
        )
        db.session.add(doc)
        if customer.kyc_status == "PENDING":
            customer.kyc_status = "UPLOADED"
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.exception("Failed to upload customer document")
        return (
            jsonify(
                {
                    "error": "Failed to upload customer document",
                    "details": str(e),
                }
            ),
            500,
        )

    return (
        jsonify(
            {
                "id": doc.id,
                "document_type": doc.document_type,
                "file_path": doc.file_path,
                "uploaded_at": doc.uploaded_at.isoformat() if doc.uploaded_at else None,
                "kyc_status": customer.kyc_status,
            }
        ),
        201,
    )


@customers_bp.route("/<int:customer_id>", methods=["GET"])
@role_required(["admin", "staff"])
def get_customer(customer_id: int):
    logger = current_app.logger
    try:
        customer, error_response = _get_customer_or_404(customer_id)
        if error_response:
            return error_response

        response = jsonify(_serialize_customer(customer))
        logger.info("Handled %s %s with status %s", request.method, request.path, 200)
        return response
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("Error handling %s %s: %s", request.method, request.path, exc)
        return jsonify({"message": "Failed to load customer"}), 500


@customers_bp.route("/by-code", methods=["GET"])
@role_required(["admin", "staff"])
def get_customer_by_code_admin():
    customer_code = request.args.get("customer_code", type=str)
    customer = _get_customer_by_code(customer_code)
    if not customer:
        return jsonify({"message": "Customer not found"}), 404
    return jsonify(_serialize_customer(customer))


@customers_bp.route("/<int:customer_id>/kyc-profile", methods=["PATCH", "POST"])
@role_required(["admin", "staff"])
def update_customer_kyc_profile(customer_id: int):
    customer = Customer.query.get(customer_id)
    if not customer:
        return jsonify({"message": "Customer not found"}), 404

    data = request.get_json() or {}

    def set_attr(field, cast=str):
        if field in data and data[field] is not None:
            setattr(customer, field, cast(data[field]))

    set_attr("civil_status")
    set_attr("permanent_address_line1")
    set_attr("permanent_address_line2")
    set_attr("permanent_city")
    set_attr("permanent_district")
    set_attr("permanent_province")
    set_attr("permanent_postal_code")
    set_attr("current_address_line1")
    set_attr("current_address_line2")
    set_attr("current_city")
    set_attr("current_district")
    set_attr("current_province")
    set_attr("current_postal_code")
    set_attr("current_address_since")
    set_attr("household_size", int)
    set_attr("dependents_count", int)
    set_attr("customer_type")
    set_attr("employer_name")
    set_attr("employer_address")
    set_attr("occupation")
    set_attr("business_name")
    set_attr("business_address")
    set_attr("guarantor_name")
    set_attr("guarantor_relationship")
    set_attr("guarantor_mobile")

    if "monthly_income" in data and data["monthly_income"] is not None:
        try:
            customer.monthly_income = Decimal(str(data["monthly_income"]))
        except Exception:
            pass

    if "date_of_birth" in data and data["date_of_birth"]:
        try:
            customer.date_of_birth = date.fromisoformat(data["date_of_birth"])
        except ValueError:
            pass

    if "consent_data_processing" in data:
        customer.consent_data_processing = bool(data["consent_data_processing"])
    if "consent_credit_checks" in data:
        customer.consent_credit_checks = bool(data["consent_credit_checks"])

    db.session.commit()
    return jsonify(_serialize_customer(customer))


@customers_bp.route("/<int:customer_id>/kyc-uploaded", methods=["POST"])
@role_required(["admin", "staff"])
def mark_kyc_uploaded(customer_id: int):
    logger = current_app.logger
    try:
        customer, error_response = _get_customer_or_404(customer_id)
        if error_response:
            return error_response

        customer.kyc_status = "UPLOADED"
        db.session.commit()

        response = jsonify(_serialize_customer(customer))
        logger.info("Handled %s %s with status %s", request.method, request.path, 200)
        return response
    except Exception as exc:  # pragma: no cover - defensive logging
        db.session.rollback()
        logger.exception("Error handling %s %s: %s", request.method, request.path, exc)
        return jsonify({"message": "Failed to update customer"}), 500


@customers_bp.route("/<int:customer_id>/kyc-under-review", methods=["POST"])
@role_required(["admin", "staff"])
def mark_kyc_under_review(customer_id: int):
    logger = current_app.logger
    try:
        customer, error_response = _get_customer_or_404(customer_id)
        if error_response:
            return error_response

        customer.kyc_status = "UNDER_REVIEW"
        db.session.commit()

        response = jsonify(_serialize_customer(customer))
        logger.info("Handled %s %s with status %s", request.method, request.path, 200)
        return response
    except Exception as exc:  # pragma: no cover - defensive logging
        db.session.rollback()
        logger.exception("Error handling %s %s: %s", request.method, request.path, exc)
        return jsonify({"message": "Failed to update customer"}), 500


@customers_bp.route("/<int:customer_id>/kyc-approve", methods=["POST"])
@role_required(["admin", "staff"])
def approve_kyc(customer_id: int):
    logger = current_app.logger
    try:
        customer, error_response = _get_customer_or_404(customer_id)
        if error_response:
            return error_response

        customer.kyc_status = "APPROVED"
        db.session.commit()

        response = jsonify(_serialize_customer(customer))
        logger.info("Handled %s %s with status %s", request.method, request.path, 200)
        return response
    except Exception as exc:  # pragma: no cover - defensive logging
        db.session.rollback()
        logger.exception("Error handling %s %s: %s", request.method, request.path, exc)
        return jsonify({"message": "Failed to update customer"}), 500


@customers_bp.route("/<int:customer_id>/kyc-reject", methods=["POST"])
@role_required(["admin", "staff"])
def reject_kyc(customer_id: int):
    logger = current_app.logger
    try:
        customer, error_response = _get_customer_or_404(customer_id)
        if error_response:
            return error_response

        customer.kyc_status = "REJECTED"
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
    logger = current_app.logger
    try:
        customer, error_response = _get_customer_or_404(customer_id)
        if error_response:
            return error_response

        if (customer.kyc_status or "").upper() != "APPROVED":
            return jsonify({"message": "Cannot mark eligible: KYC not approved"}), 400

        customer.eligibility_status = "ELIGIBLE"
        db.session.commit()

        response = jsonify(_serialize_customer(customer))
        logger.info("Handled %s %s with status %s", request.method, request.path, 200)
        return response
    except Exception as exc:  # pragma: no cover - defensive logging
        db.session.rollback()
        logger.exception("Error handling %s %s: %s", request.method, request.path, exc)
        return jsonify({"message": "Failed to update customer"}), 500


@customers_bp.route("/<int:customer_id>/mark-not-eligible", methods=["POST"])
@role_required(["admin", "staff"])
def mark_not_eligible(customer_id: int):
    logger = current_app.logger
    try:
        customer, error_response = _get_customer_or_404(customer_id)
        if error_response:
            return error_response

        customer.eligibility_status = "NOT_ELIGIBLE"
        db.session.commit()

        response = jsonify(_serialize_customer(customer))
        logger.info("Handled %s %s with status %s", request.method, request.path, 200)
        return response
    except Exception as exc:  # pragma: no cover - defensive logging
        db.session.rollback()
        logger.exception("Error handling %s %s: %s", request.method, request.path, exc)
        return jsonify({"message": "Failed to update customer"}), 500


@public_bp.route("/customers/by-code", methods=["GET"])
def public_get_customer_by_code():
    """
    Lightweight public lookup used by the /kyc?code=... page.
    No authentication, returns only non-sensitive fields.
    """

    customer_code = request.args.get("customer_code", type=str)
    customer = _get_customer_by_code(customer_code)
    if not customer:
        return jsonify({"message": "Customer not found"}), 404

    data = {
        "customer_code": customer.customer_code,
        "full_name": customer.full_name,
        "mobile": customer.mobile,
        "kyc_status": customer.kyc_status,
        "eligibility_status": customer.eligibility_status,
    }
    return jsonify(data)


@public_bp.route("/customers/<customer_code>/kyc-upload", methods=["POST"])
def public_kyc_upload(customer_code: str):
    customer = Customer.query.filter_by(customer_code=customer_code).first()
    if not customer:
        return jsonify({"message": "Customer not found"}), 404

    files = request.files
    saved_types: list[str] = []

    upload_failed = False

    def handle_file(field_name: str, doc_type: str):
        nonlocal upload_failed
        file_storage = files.get(field_name)
        if file_storage:
            try:
                path = save_customer_document_file(customer.id, file_storage, doc_type)
            except ValueError:
                upload_failed = True
                return
            doc = CustomerDocument(
                customer_id=customer.id,
                document_type=doc_type,
                file_path=path,
            )
            db.session.add(doc)
            saved_types.append(doc_type)

    handle_file("nic_front", "NIC_FRONT")
    handle_file("nic_back", "NIC_BACK")
    handle_file("selfie_nic", "SELFIE_NIC")
    handle_file("address_proof", "ADDRESS_PROOF")

    form = request.form

    def set_form_attr(field, cast=str):
        if field in form and form[field]:
            setattr(customer, field, cast(form[field]))

    set_form_attr("civil_status")
    set_form_attr("permanent_address_line1")
    set_form_attr("permanent_address_line2")
    set_form_attr("permanent_city")
    set_form_attr("permanent_district")
    set_form_attr("permanent_province")
    set_form_attr("permanent_postal_code")
    set_form_attr("current_address_line1")
    set_form_attr("current_address_line2")
    set_form_attr("current_city")
    set_form_attr("current_district")
    set_form_attr("current_province")
    set_form_attr("current_postal_code")
    set_form_attr("current_address_since")
    set_form_attr("customer_type")
    set_form_attr("employer_name")
    set_form_attr("employer_address")
    set_form_attr("occupation")
    set_form_attr("business_name")
    set_form_attr("business_address")
    set_form_attr("guarantor_name")
    set_form_attr("guarantor_relationship")
    set_form_attr("guarantor_mobile")

    if "household_size" in form and form["household_size"]:
        customer.household_size = int(form["household_size"])
    if "dependents_count" in form and form["dependents_count"]:
        customer.dependents_count = int(form["dependents_count"])

    if "monthly_income" in form and form["monthly_income"]:
        try:
            customer.monthly_income = Decimal(form["monthly_income"])
        except Exception:
            pass

    if "date_of_birth" in form and form["date_of_birth"]:
        try:
            customer.date_of_birth = date.fromisoformat(form["date_of_birth"])
        except ValueError:
            pass

    def form_bool(name):
        v = form.get(name)
        return True if v and str(v).lower() in ("1", "true", "on", "yes") else False

    if "consent_data_processing" in form:
        customer.consent_data_processing = form_bool("consent_data_processing")
    if "consent_credit_checks" in form:
        customer.consent_credit_checks = form_bool("consent_credit_checks")

    if upload_failed:
        return jsonify({"message": "Failed to upload document"}), 500

    if not saved_types:
        return jsonify({"message": "No files uploaded"}), 400

    if customer.kyc_status in ("PENDING", "UPLOADED"):
        customer.kyc_status = "UPLOADED"

    db.session.commit()

    return jsonify(
        {
            "message": "KYC documents uploaded",
            "saved_types": saved_types,
            "kyc_status": customer.kyc_status,
        }
    )
