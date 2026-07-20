"""Customer-master consolidation.

Field map: ``customers`` owns the current full name, NIC, mobile, email, DOB,
civil status, structured current/permanent addresses, occupation/employer/business,
income/expenses, household/dependents and guarantor.  ``customer_kyc_profiles``
owns KYC verification/review, consents and documents and is a verified fallback
for those profile values. ``loan_applications`` owns immutable application-time
snapshots (including loan terms) and is used only by the explicit backfill tool.
``users.email`` is the fallback for customer email.  Loans contain loan lifecycle
facts, not customer profile data.  Application snapshots are never updated.

Address fallback is current structured address, then permanent structured address,
then legacy ``customers.address``. Legacy text is copied only to line1 and marked
for review; city/district/province are never guessed.  Latest approved KYC means
``review_status`` in APPROVED/VERIFIED, ordered by reviewed_at descending then id
descending.  A legacy profile is eligible when the customer KYC status is approved.
"""
from __future__ import annotations
from datetime import datetime
from decimal import Decimal
from sqlalchemy import or_
from .extensions import db
from .models import Customer, CustomerKYCProfile, LoanApplication

PROFILE_FIELDS = ("full_name", "nic_number", "mobile", "email", "date_of_birth", "civil_status",
 "current_address_line1", "current_address_line2", "current_city", "current_district", "current_province", "current_postal_code",
 "permanent_address_line1", "permanent_address_line2", "permanent_city", "permanent_district", "permanent_province", "permanent_postal_code",
 "occupation", "employer_name", "employer_address", "business_name", "business_address", "business_type", "monthly_income", "monthly_expenses", "household_size", "dependents_count", "guarantor_name", "guarantor_relationship", "guarantor_mobile")
KYC_FALLBACK_FIELDS = set(PROFILE_FIELDS) - {"business_type"}
IDENTITY_FIELDS = {"nic_number", "date_of_birth", "mobile"}

def present(value): return value is not None and (not isinstance(value, str) or bool(value.strip()))
def latest_approved_kyc(customer):
    statuses = ("APPROVED", "VERIFIED")
    query = CustomerKYCProfile.query.filter_by(customer_id=customer.id).filter(
        or_(CustomerKYCProfile.review_status.in_(statuses),
            (CustomerKYCProfile.review_status.is_(None) if (customer.kyc_status or "").upper() in statuses else False)))
    return query.order_by(CustomerKYCProfile.reviewed_at.desc(), CustomerKYCProfile.id.desc()).first()

def _value(customer, kyc, field):
    value = getattr(customer, field, None)
    if present(value): return value, "customers"
    if field == "email" and customer.user and present(customer.user.email): return customer.user.email, "users"
    if kyc and field in KYC_FALLBACK_FIELDS and present(getattr(kyc, field, None)): return getattr(kyc, field), "customer_kyc_profiles"
    return None, None

def customer_profile_conflicts(customer, kyc=None, application=None):
    kyc = kyc or latest_approved_kyc(customer)
    sources = [("customer_kyc_profiles", kyc), ("loan_applications", application)]
    conflicts = []
    for field in IDENTITY_FIELDS | {"monthly_income", "current_address_line1"}:
        base = getattr(customer, field, None)
        if not present(base): continue
        for source, record in sources:
            candidate = getattr(record, field if source != "loan_applications" or field != "mobile" else "mobile_number", None) if record else None
            if present(candidate) and candidate != base:
                conflicts.append({"field": field, "customer_value": str(base), "source": source, "source_value": str(candidate), "requires_manual_review": field in IDENTITY_FIELDS})
    return conflicts

def build_customer_master_profile(customer_id):
    customer = db.session.get(Customer, customer_id)
    if not customer: raise LookupError("Customer not found")
    kyc = latest_approved_kyc(customer)
    fields = {}
    for field in PROFILE_FIELDS:
        value, source = _value(customer, kyc, field)
        fields[field] = {"value": value, "source": source}
    # legacy address is a final fallback only, never parsed.
    if not present(fields["current_address_line1"]["value"]) and not present(fields["permanent_address_line1"]["value"]) and present(customer.address):
        fields["current_address_line1"] = {"value": customer.address, "source": "customers.address_legacy"}
    missing = [field for field, item in fields.items() if not present(item["value"])]
    conflicts = customer_profile_conflicts(customer, kyc)
    return {"customer_id": customer.id, "customer_code": customer.customer_code, "fields": fields,
            "kyc_status": customer.kyc_status, "eligibility_status": customer.eligibility_status,
            "profile_complete": not missing and not any(c["requires_manual_review"] for c in conflicts),
            "missing_fields": missing, "conflicts": conflicts,
            "review_warnings": (["legacy_address_requires_review"] if fields["current_address_line1"]["source"] == "customers.address_legacy" else []) + (["identity_conflict_requires_review"] if any(c["requires_manual_review"] for c in conflicts) else [])}

def backfill_customer(customer):
    """Fill blanks only; return a preview/apply-safe change report."""
    kyc = latest_approved_kyc(customer)
    application = LoanApplication.query.filter_by(customer_id=customer.id).filter(LoanApplication.status.in_(("SUBMITTED", "APPROVED"))).order_by(LoanApplication.submitted_at.desc(), LoanApplication.approved_at.desc(), LoanApplication.id.desc()).first()
    changes, warnings = [], []
    for field in PROFILE_FIELDS:
        if present(getattr(customer, field, None)): continue
        value = getattr(kyc, field, None) if kyc and field in KYC_FALLBACK_FIELDS else None
        source = "customer_kyc_profiles" if present(value) else None
        app_field = {"mobile": "mobile_number", "current_address_line1": "address_line1"}.get(field, field)
        if not present(value) and application and hasattr(application, app_field): value, source = getattr(application, app_field), "loan_applications:%s" % application.id
        if present(value): changes.append({"field": field, "value": value, "source": source})
    if not present(customer.current_address_line1) and not present(customer.permanent_address_line1) and present(customer.address):
        changes.append({"field": "current_address_line1", "value": customer.address, "source": "customers.address_legacy"})
        if not customer.address_backfill_review_required: changes.append({"field": "address_backfill_review_required", "value": True, "source": "legacy_address_review"})
        warnings.append("legacy_address_requires_review")
    return {"customer_id": customer.id, "changes": changes, "warnings": warnings, "conflicts": customer_profile_conflicts(customer, kyc, application)}

def apply_backfill(customer):
    report = backfill_customer(customer)
    for change in report["changes"]: setattr(customer, change["field"], change["value"])
    return report
