import logging
import os
from datetime import datetime

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
