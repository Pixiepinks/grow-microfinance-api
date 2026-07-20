from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from flask import Blueprint, request, jsonify, current_app
from sqlalchemy.exc import IntegrityError
import logging
import secrets
from flask_jwt_extended import get_jwt_identity
from sqlalchemy import String, case, cast, false, func, or_, and_
from sqlalchemy.orm import joinedload

from app.supabase_client import build_public_url

from ..currency import CURRENCY_CODE, format_currency
from ..extensions import db
from ..models import (
    Customer,
    Loan,
    LoanApplication,
    LoanApplicationDocument,
    LoanLedger,
    Payment,
    User,
    AccountingAccount,
    AccountingJournalLine,
    CollectionDepositBatch,
    LoanDisbursementDeduction,
    DisbursementChargeType,
    AccountingSetting,
    CustomerCreditBalance,
)
from ..accounting import log_audit, post_loan_disbursement, AccountingError, accrue_due_loan_interest, reverse_payment, reverse_loan_disbursement, money as acct_money, preview_collection_deposit, create_collection_deposit, reverse_collection_deposit, collector_cash_position, account_subtype, allocate_payment, post_loan_payment, validate_collection_account, repair_unposted_payment, require_open_accounting_period, ValidationError, preview_loan_disbursement, preview_loan_application_disbursement, CALCULATION_METHODS, is_funding_account, is_active_account, is_posting_account
from ..loan_ledger import (
    daily_interest_rate,
    generate_loan_ledger,
    ledger_totals,
    loan_config_summary,
    money,
)
from .utils import role_required

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")
logger = logging.getLogger(__name__)
from ..settlement_reconciliation import preview as settlement_preview, finalize_loan_reconciliation
from ..loan_status import serialize_loan_status
from ..early_settlement import preview_early_loan_settlement, post_early_loan_settlement, reverse_early_loan_settlement, EarlySettlementError

ACTIVE_LOAN_STATUSES = {"ACTIVE", "DISBURSED"}
POSTED_PAYMENT_STATUSES = {"POSTED"}



def _parse_customer_search_limit(raw_limit) -> int:
    try:
        parsed_limit = int(raw_limit) if raw_limit is not None else 10
    except (TypeError, ValueError):
        parsed_limit = 10
    return min(max(parsed_limit, 1), 20)


def _normalized_phone_expression(column):
    normalized = func.coalesce(column, "")
    for old in (" ", "-", "+", "(", ")", "."):
        normalized = func.replace(normalized, old, "")
    return normalized


def _phone_search_variants(query: str) -> set[str]:
    digits = "".join(ch for ch in query if ch.isdigit())
    if not digits:
        return set()

    variants = {digits}
    if digits.startswith("94") and len(digits) > 2:
        variants.add("0" + digits[2:])
        variants.add(digits[2:])
    elif digits.startswith("0") and len(digits) > 1:
        variants.add("94" + digits[1:])
        variants.add(digits[1:])
    else:
        variants.add("0" + digits)
        variants.add("94" + digits)
    return {variant for variant in variants if variant}


def _serialize_customer_search_item(customer: Customer) -> dict:
    customer_number = customer.customer_code
    full_name = customer.full_name
    mobile = customer.mobile
    label_parts = [part for part in (customer_number, full_name, mobile) if part]
    return {
        "id": customer.id,
        "customer_id": customer.id,
        "customer_number": customer_number,
        "full_name": full_name,
        "nic": customer.nic_number,
        "mobile": mobile,
        "email": customer.user.email if customer.user else None,
        "address_line_1": (
            customer.permanent_address_line1
            or customer.current_address_line1
            or customer.address
        ),
        "address_line_2": customer.permanent_address_line2 or customer.current_address_line2,
        "city": customer.permanent_city or customer.current_city,
        "district": customer.permanent_district or customer.current_district,
        "province": customer.permanent_province or customer.current_province,
        "date_of_birth": customer.date_of_birth.isoformat() if customer.date_of_birth else None,
        "label": " — ".join(label_parts),
    }

def _compact_account(account):
    if not account:
        return None
    return {"id": account.id, "code": account.account_code, "name": account.account_name, "account_type": account.account_type, "account_subtype": account_subtype(account)}


def _charge_destination_account(charge_type):
    if charge_type.accounting_treatment == "INCOME":
        return charge_type.income_account
    if charge_type.accounting_treatment in {"PAYABLE", "TAX"}:
        return charge_type.payable_account or charge_type.tax_payable_account
    return charge_type.expense_account or charge_type.income_account or charge_type.payable_account

def _charge_type_is_disbursement_ready(charge_type):
    destination = _charge_destination_account(charge_type)
    return (
        bool(charge_type.active)
        and bool(charge_type.deducted_from_disbursement)
        and charge_type.calculation_method in CALCULATION_METHODS
        and destination is not None
        and is_active_account(destination)
        and is_posting_account(destination)
    )


def _charge_type_payload(charge_type):
    destination = _charge_destination_account(charge_type)
    return {
        "id": charge_type.id,
        "code": charge_type.code,
        "name": charge_type.name,
        "description": charge_type.description,
        "calculation_method": charge_type.calculation_method,
        "default_amount": float(charge_type.default_amount) if charge_type.default_amount is not None else None,
        "default_rate": float(charge_type.default_rate) if charge_type.default_rate is not None else None,
        "accounting_treatment": charge_type.accounting_treatment,
        "tax_method": charge_type.tax_method,
        "tax_rate": float(charge_type.tax_rate) if charge_type.tax_rate is not None else None,
        "included_in_principal": bool(charge_type.included_in_principal),
        "deducted_from_disbursement": bool(charge_type.deducted_from_disbursement),
        "refundable": bool(charge_type.refundable),
        "display_order": charge_type.display_order,
        "active": bool(charge_type.active),
        "selected_by_default": bool(charge_type.active and charge_type.deducted_from_disbursement and (charge_type.default_amount or 0) > 0),
        "destination_account": _compact_account(destination),
    }


def _setting_bool(settings, key, default=False):
    setting = settings.get(key)
    if setting is None:
        return default
    return str(setting.setting_value).lower() in {"1", "true", "yes", "on"}


def _term_display(application):
    if application.term_type and application.term_value:
        unit = "days" if application.term_type == "DAYS" else "months"
        return f"{application.term_value} {unit}"
    if application.loan_days:
        return f"{application.loan_days} days"
    return f"{application.tenure_months} months" if application.tenure_months else None


@admin_bp.before_request
def log_collector_requests():
    if request.path.startswith("/admin/collectors"):
        logger.info(
            "Collector request method=%s path=%s payload_keys=%s",
            request.method,
            request.path,
            sorted((request.get_json(silent=True) or {}).keys()),
        )


@admin_bp.route("/staff", methods=["GET"])
@role_required(["admin"])
def list_staff_users():
    users = (
        User.query.filter(User.role.in_(["admin", "staff"]))
        .order_by(User.role.asc(), User.name.asc())
        .all()
    )

    results = []
    for user in users:
        last_login = getattr(user, "last_login_at", None)
        if last_login is not None:
            last_login = last_login.isoformat()

        results.append(
            {
                "id": user.id,
                "email": user.email,
                "name": user.name,
                "role": user.role,
                "is_active": user.is_active,
                "created_at": user.created_at.isoformat() if user.created_at else None,
                "last_login_at": last_login,
            }
        )

    return jsonify(results)


@admin_bp.route("/users", methods=["POST"])
@role_required(["admin"])
def create_user():
    data = request.get_json() or {}
    email = data.get("email")
    password = data.get("password")
    name = data.get("name")
    role = data.get("role")

    if role not in ["admin", "staff", "customer"]:
        return jsonify({"message": "Invalid role"}), 400

    if User.query.filter_by(email=email).first():
        return jsonify({"message": "User already exists"}), 400

    user = User(email=email, name=name, role=role)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()

    return jsonify({"message": "User created", "user_id": user.id})


@admin_bp.route("/users/<int:user_id>/reset-password", methods=["POST"])
@role_required(["admin"])
def reset_user_password(user_id):
    user = User.query.get_or_404(user_id)
    temporary_password = f"Tmp-{secrets.token_urlsafe(18)}!9aA"
    user.set_password(temporary_password)
    user.must_change_password = True
    user.password_changed_at = datetime.utcnow()
    user.token_version = (user.token_version or 0) + 1
    user.failed_login_attempts = 0
    user.locked_until = None
    log_audit("PASSWORD_RESET_REQUESTED", "User", user.id, int(get_jwt_identity()))
    log_audit("PASSWORD_RESET_COMPLETED", "User", user.id, int(get_jwt_identity()))
    log_audit("SESSION_REVOKED", "User", user.id, int(get_jwt_identity()), {"reason": "password_reset"})
    db.session.commit()
    return jsonify({
        "message": "Temporary password generated. User must change it at next login.",
        "temporary_password": temporary_password,
        "must_change_password": True,
    })

@admin_bp.route("/users", methods=["GET"])
@role_required(["admin"])
def list_users():
    role = request.args.get("role")
    query = User.query
    if role:
        query = query.filter_by(role=role)
    users = query.all()
    results = [
        {
            "id": u.id,
            "email": u.email,
            "name": u.name,
            "role": u.role,
            "is_active": u.is_active,
        }
        for u in users
    ]
    return jsonify(results)


@admin_bp.route("/customers", methods=["POST"])
@role_required(["admin"])
def create_customer():
    data = request.get_json() or {}
    user_data = data.get("user") or {}
    profile_data = data.get("customer") or {}

    if not user_data.get("email") or not user_data.get("password"):
        return jsonify({"message": "User email and password are required"}), 400

    if User.query.filter_by(email=user_data["email"]).first():
        return jsonify({"message": "User already exists"}), 400

    user = User(
        email=user_data["email"], name=user_data.get("name", ""), role="customer"
    )
    user.set_password(user_data["password"])

    customer = Customer(
        user=user,
        customer_code=profile_data.get("customer_code"),
        full_name=profile_data.get("full_name", ""),
        nic_number=profile_data.get("nic_number"),
        mobile=profile_data.get("mobile"),
        address=profile_data.get("address"),
        business_type=profile_data.get("business_type"),
        status=profile_data.get("status", "Active"),
    )

    db.session.add(user)
    db.session.add(customer)
    db.session.commit()

    return jsonify({"message": "Customer created", "customer_id": customer.id})




@admin_bp.route("/customers/search", methods=["GET"], strict_slashes=False)
@role_required(["admin"])
def search_customers():
    query_text = str(request.args.get("q") or "").strip()
    limit = _parse_customer_search_limit(request.args.get("limit"))
    if not query_text:
        return jsonify({"items": [], "total": 0, "query": ""})

    try:
        search = f"%{query_text}%"
        prefix = f"{query_text}%"
        exact_lower = query_text.lower()
        numeric_id = int(query_text) if query_text.isdigit() else None
        mobile_normalized = _normalized_phone_expression(Customer.mobile)
        guarantor_mobile_normalized = _normalized_phone_expression(
            Customer.guarantor_mobile
        )
        phone_variants = _phone_search_variants(query_text)

        filters = [
            Customer.customer_code.ilike(search),
            Customer.full_name.ilike(search),
            Customer.nic_number.ilike(search),
            Customer.mobile.ilike(search),
            Customer.guarantor_mobile.ilike(search),
            User.email.ilike(search),
            cast(Customer.id, String).ilike(search),
        ]
        for variant in phone_variants:
            filters.extend(
                [
                    mobile_normalized.like(f"%{variant}%"),
                    guarantor_mobile_normalized.like(f"%{variant}%"),
                ]
            )

        rank = case(
            (Customer.id == numeric_id, 1),
            (func.lower(Customer.customer_code) == exact_lower, 2),
            (func.lower(Customer.nic_number) == exact_lower, 3),
            (
                or_(*[mobile_normalized == variant for variant in phone_variants])
                if phone_variants
                else false(),
                4,
            ),
            (
                or_(
                    Customer.nic_number.ilike(prefix),
                    *[mobile_normalized.like(f"{variant}%") for variant in phone_variants],
                ),
                5,
            ),
            (Customer.full_name.ilike(prefix), 6),
            else_=7,
        )

        customer_query = Customer.query.outerjoin(User)
        include_inactive = str(
            request.args.get("include_inactive", "false")
        ).lower() in ("1", "true", "yes", "on")
        if not include_inactive:
            customer_query = customer_query.filter(
                Customer.status.in_(["Active", "ACTIVE", "active"])
            )

        customers = (
            customer_query.filter(or_(*filters))
            .order_by(rank.asc(), Customer.customer_code.asc(), Customer.id.asc())
            .limit(limit)
            .all()
        )
        return jsonify(
            {
                "items": [
                    _serialize_customer_search_item(customer) for customer in customers
                ],
                "total": len(customers),
                "query": query_text,
            }
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        current_app.logger.exception("Customer search failed: %s", exc)
        return jsonify({"message": "Failed to search customers"}), 500


@admin_bp.route("/customers", methods=["GET"])
@role_required(["admin"])
def list_customers():
    customers = Customer.query.all()
    results = [
        {
            "id": c.id,
            "customer_code": c.customer_code,
            "full_name": c.full_name,
            "status": c.status,
            "mobile": c.mobile,
            "lead_status": c.lead_status,
            "kyc_status": c.kyc_status,
            "eligibility_status": c.eligibility_status,
        }
        for c in customers
    ]
    return jsonify(results)


@admin_bp.route("/loans/search", methods=["GET"], strict_slashes=False)
@role_required(["admin"])
def search_loans():
    query_text = str(request.args.get("q") or "").strip()
    try:
        limit = min(max(int(request.args.get("limit", 10)), 1), 20)
    except (TypeError, ValueError):
        limit = 10
    if not query_text:
        return jsonify({"items": [], "total": 0, "query": ""})
    pattern = f"%{query_text}%"
    loans = (Loan.query.join(Customer).filter(or_(Loan.loan_number.ilike(pattern), Customer.full_name.ilike(pattern), Customer.customer_code.ilike(pattern))).order_by(Loan.loan_number, Loan.id).limit(limit).all())
    return jsonify({"items": [{"id": loan.id, "loan_number": loan.loan_number, "customer_id": loan.customer_id, "customer_name": loan.customer.full_name if loan.customer else None, "customer_number": loan.customer.customer_code if loan.customer else None, "status": loan.status} for loan in loans], "total": len(loans), "query": query_text})


@admin_bp.route("/loans", methods=["POST"])
@role_required(["admin"])
def create_loan():
    data = request.get_json() or {}
    customer_id = data.get("customer_id")
    principal = Decimal(str(data.get("principal_amount", "0")))
    interest_rate = Decimal(str(data.get("interest_rate", "0")))
    total_days = int(data.get("total_days", 0))
    payment_interval_days = int(data.get("payment_interval_days", 7) or 7)
    start_date = date.fromisoformat(data.get("start_date"))
    end_date = date.fromisoformat(data.get("end_date"))

    total_payable = principal + (principal * (interest_rate / Decimal("100")))
    daily_installment = total_payable / Decimal(total_days)

    loan = Loan(
        loan_number=data.get("loan_number"),
        customer_id=customer_id,
        principal_amount=principal,
        interest_rate=interest_rate,
        total_days=total_days,
        payment_interval_days=payment_interval_days,
        start_date=start_date,
        end_date=end_date,
        total_payable=total_payable,
        daily_installment=daily_installment,
        created_by_id=int(get_jwt_identity()),
    )
    db.session.add(loan)
    db.session.flush()
    generate_loan_ledger(loan)
    try:
        post_loan_disbursement(loan, int(get_jwt_identity()))
        db.session.commit()
    except AccountingError as exc:
        db.session.rollback()
        return jsonify({"message": str(exc)}), 400

    return jsonify({"message": "Loan created", "loan_id": loan.id})


@admin_bp.route("/loans", methods=["GET"])
@role_required(["admin"])
def list_loans():
    """Return the admin loan list using database-side search and pagination."""
    def value(name):
        raw = request.args.get(name)
        return raw.strip() if isinstance(raw, str) and raw.strip() else None

    def invalid(message, error="invalid_parameter"):
        return jsonify({"error": error, "message": message}), 422

    def parse_date(name):
        raw = value(name)
        if raw is None:
            return None, None
        try:
            return date.fromisoformat(raw), None
        except ValueError:
            return None, invalid(f"{name} must be a valid ISO date (YYYY-MM-DD).")

    def parse_decimal(name):
        raw = value(name)
        if raw is None:
            return None, None
        try:
            parsed = Decimal(raw)
        except (InvalidOperation, ValueError):
            return None, invalid(f"{name} must be a valid decimal amount.")
        if not parsed.is_finite() or parsed < 0:
            return None, invalid(f"{name} must be a non-negative decimal amount.")
        return parsed, None

    def parse_positive_int(name, default, maximum=None):
        raw = value(name)
        if raw is None:
            return default, None
        try:
            parsed = int(raw)
        except ValueError:
            return None, invalid(f"{name} must be an integer.")
        if parsed < 1 or (maximum is not None and parsed > maximum):
            limit = f" between 1 and {maximum}" if maximum else " at least 1"
            return None, invalid(f"{name} must be{limit}.")
        return parsed, None

    q = value("q")
    status = value("status")
    if status:
        status = status.upper()
        if status == "ALL":
            status = None
        elif status not in {"ACTIVE", "OVERDUE", "SETTLED", "WRITTEN_OFF", "CANCELLED", "DISBURSED"}:
            return invalid("status is not supported.")
    balance_status = value("balance_status")
    if balance_status:
        balance_status = balance_status.upper()
        if balance_status == "ALL":
            balance_status = None
        elif balance_status not in {"OUTSTANDING", "FULLY_PAID", "OVERPAID", "ZERO_BALANCE"}:
            return invalid("balance_status is not supported.")
    sort_by = (value("sort_by") or "disbursement_date").lower()
    sort_direction = (value("sort_direction") or "desc").lower()
    if sort_direction not in {"asc", "desc"}:
        return invalid("sort_direction must be asc or desc.")

    date_from, error = parse_date("date_from")
    if error:
        return error
    date_to, error = parse_date("date_to")
    if error:
        return error
    if date_from and date_to and date_from > date_to:
        return invalid("Date From cannot be later than Date To.", "invalid_date_range")
    principal_min, error = parse_decimal("principal_min")
    if error:
        return error
    principal_max, error = parse_decimal("principal_max")
    if error:
        return error
    if principal_min is not None and principal_max is not None and principal_min > principal_max:
        return invalid("principal_min cannot be greater than principal_max.", "invalid_principal_range")
    page, error = parse_positive_int("page", 1)
    if error:
        return error
    page_size, error = parse_positive_int("page_size", 25, 100)
    if error:
        return error
    customer_id_raw = value("customer_id")
    customer_id = None
    if customer_id_raw:
        try:
            customer_id = int(customer_id_raw)
            if customer_id < 1:
                raise ValueError
        except ValueError:
            return invalid("customer_id must be a positive integer.")

    # These correlated aggregates preserve one row per loan while keeping balance
    # filters, sorting, and pagination entirely in the database.
    paid_amount = db.session.query(func.coalesce(func.sum(Payment.amount_collected), 0)).filter(
        Payment.loan_id == Loan.id,
        Payment.reversed_at.is_(None),
        func.upper(func.trim(Payment.status)) == "POSTED",
    ).correlate(Loan).scalar_subquery()
    raw_outstanding = (func.coalesce(Loan.total_payable, 0) - paid_amount).label("raw_outstanding")
    outstanding_amount = case((raw_outstanding < 0, 0), else_=raw_outstanding).label("outstanding_amount")
    application_id = db.session.query(func.min(LoanDisbursementDeduction.loan_application_id)).filter(
        LoanDisbursementDeduction.loan_id == Loan.id
    ).correlate(Loan).scalar_subquery()

    sort_fields = {
        "loan_number": Loan.loan_number,
        "customer_name": Customer.full_name,
        "principal_amount": Loan.principal_amount,
        "total_payable": Loan.total_payable,
        "total_paid": paid_amount,
        "outstanding_amount": outstanding_amount,
        "disbursement_date": Loan.start_date,
        "settled_date": Loan.settled_date,
        "status": func.upper(func.trim(Loan.status)),
    }
    if sort_by not in sort_fields:
        return invalid("sort_by is not supported.")

    query = db.session.query(Loan, paid_amount.label("total_paid"), raw_outstanding, outstanding_amount, application_id.label("application_id"), LoanApplication.application_number).options(joinedload(Loan.customer)).outerjoin(Customer, Loan.customer_id == Customer.id).outerjoin(LoanApplication, LoanApplication.id == application_id)
    if q:
        pattern = f"%{q}%"
        search_terms = [
            Loan.loan_number.ilike(pattern), Customer.full_name.ilike(pattern),
            Customer.customer_code.ilike(pattern), Customer.nic_number.ilike(pattern),
            Customer.mobile.ilike(pattern), LoanApplication.application_number.ilike(pattern),
        ]
        if q.isdigit():
            search_terms.append(Loan.id == int(q))
        query = query.filter(or_(*search_terms))
    if status:
        query = query.filter(func.upper(func.trim(Loan.status)) == status)
    if customer_id:
        query = query.filter(Loan.customer_id == customer_id)
    if date_from:
        query = query.filter(Loan.start_date >= date_from)
    if date_to:
        query = query.filter(Loan.start_date <= date_to)
    if principal_min is not None:
        query = query.filter(Loan.principal_amount >= principal_min)
    if principal_max is not None:
        query = query.filter(Loan.principal_amount <= principal_max)
    credit = func.coalesce(Loan.customer_credit_balance, 0)
    if balance_status == "OUTSTANDING":
        query = query.filter(outstanding_amount > Decimal("0.01"))
    elif balance_status == "FULLY_PAID":
        query = query.filter(and_(outstanding_amount <= Decimal("0.01"), credit <= Decimal("0.01"), raw_outstanding >= 0))
    elif balance_status == "OVERPAID":
        query = query.filter(or_(credit > Decimal("0.01"), raw_outstanding < 0))
    elif balance_status == "ZERO_BALANCE":
        query = query.filter(outstanding_amount <= Decimal("0.01"))

    total_items = query.order_by(None).count()
    sort_expression = sort_fields[sort_by]
    ordering = sort_expression.asc() if sort_direction == "asc" else sort_expression.desc()
    rows = query.order_by(ordering, Loan.id.asc()).offset((page - 1) * page_size).limit(page_size).all()
    total_pages = (total_items + page_size - 1) // page_size
    items = []
    for loan, total_paid, raw_balance, displayed_balance, linked_application_id, application_number in rows:
        customer = loan.customer
        raw_balance = Decimal(raw_balance or 0)
        displayed_balance = Decimal(displayed_balance or 0)
        credit_balance = Decimal(loan.customer_credit_balance or 0)
        reconciliation_required = (loan.status or "").strip().upper() != "SETTLED" and displayed_balance <= Decimal("0.01") and raw_balance >= 0
        items.append({
            "id": loan.id, "loan_id": loan.id, "loan_number": loan.loan_number,
            "application_id": linked_application_id, "application_number": application_number,
            "customer_id": loan.customer_id, "customer_number": customer.customer_code if customer else None,
            "customer_name": customer.full_name if customer else None, "nic": customer.nic_number if customer else None,
            "mobile": customer.mobile if customer else None, "customer": _loan_customer_to_dict(customer),
            "currency": CURRENCY_CODE, "principal_amount": float(loan.principal_amount or 0),
            "total_interest": float(loan.total_interest if loan.total_interest is not None else (loan.total_payable or 0) - (loan.principal_amount or 0)),
            "total_payable": float(loan.total_payable or 0), "total_paid": float(total_paid or 0),
            "outstanding_amount": float(displayed_balance), "outstanding": float(displayed_balance),
            "customer_credit_balance": float(credit_balance), "disbursement_date": loan.start_date.isoformat() if loan.start_date else None,
            "start_date": loan.start_date.isoformat() if loan.start_date else None,
            "maturity_date": loan.maturity_date.isoformat() if loan.maturity_date else (loan.end_date.isoformat() if loan.end_date else None),
            "settled_date": loan.settled_date.isoformat() if loan.settled_date else None, "status": serialize_loan_status(loan),
            "settlement_reconciliation_required": reconciliation_required,
            "available_actions": [],
            # Legacy list consumers use these keys; monetary values remain numeric.
            "principal_amount_formatted": format_currency(loan.principal_amount or 0),
            "total_payable_formatted": format_currency(loan.total_payable or 0),
            "total_paid_formatted": format_currency(total_paid or 0),
            "outstanding_formatted": format_currency(displayed_balance),
        })
    return jsonify({"items": items, "pagination": {"page": page, "page_size": page_size, "total_items": total_items, "total_pages": total_pages, "has_next": page < total_pages, "has_previous": page > 1}, "applied_filters": {"q": q, "status": status, "date_from": date_from.isoformat() if date_from else None, "date_to": date_to.isoformat() if date_to else None, "balance_status": balance_status, "principal_min": float(principal_min) if principal_min is not None else None, "principal_max": float(principal_max) if principal_max is not None else None, "customer_id": customer_id, "sort_by": sort_by, "sort_direction": sort_direction}})


def _loan_customer_to_dict(customer: Customer | None) -> dict | None:
    if customer is None:
        return None
    return {
        "id": customer.id,
        "full_name": customer.full_name,
        "mobile": customer.mobile,
        "nic": customer.nic_number,
    }


@admin_bp.route("/loans/<int:loan_id>", methods=["GET"])
@role_required(["admin"])
def get_loan(loan_id):
    loan = Loan.query.options(joinedload(Loan.customer)).get_or_404(loan_id)
    payload = _loan_to_dict(loan)
    reconciliation = settlement_preview(loan)
    payload["settlement_reconciliation_required"] = (loan.status or "").upper() != "SETTLED" and reconciliation["outstanding"] <= 0.01 and reconciliation["can_post"]
    return jsonify(payload)


@admin_bp.route("/loans/<int:loan_id>/settlement-reconciliation/preview", methods=["POST"], strict_slashes=False)
@admin_bp.route("/loans/<int:loan_id>/reconciliation/preview", methods=["POST"], strict_slashes=False)
@role_required(["admin"])
def settlement_reconciliation_preview(loan_id):
    payload = request.get_json(silent=True) or {}
    if "delay_interest_waiver_amount" in payload:
        try:
            waiver_amount = Decimal(str(payload["delay_interest_waiver_amount"])).quantize(Decimal("0.01"))
        except Exception:
            return jsonify({"error": "invalid_delay_interest_waiver", "message": "delay_interest_waiver_amount must be a currency amount."}), 422
        if waiver_amount < 0:
            return jsonify({"error": "invalid_delay_interest_waiver", "message": "delay_interest_waiver_amount cannot be negative."}), 422
        if waiver_amount > 0 and not str(payload.get("reason") or "").strip():
            return jsonify({"error": "waiver_reason_required", "message": "reason is required for a delay interest waiver."}), 422
        if waiver_amount > 0:
            payload["waive_delay_interest"] = True
    as_of_date = None
    if payload.get("as_of_date"):
        try:
            as_of_date = date.fromisoformat(payload["as_of_date"])
        except (TypeError, ValueError):
            return jsonify({"error": "invalid_as_of_date", "message": "as_of_date must be YYYY-MM-DD."}), 422
    loan = Loan.query.get(loan_id)
    if loan is None:
        return jsonify({"error": "loan_not_found", "message": "The selected loan was not found."}), 404
    result = settlement_preview(loan, as_of_date=as_of_date)
    return jsonify({k: (float(v) if isinstance(v, Decimal) else (v.isoformat() if hasattr(v, "isoformat") else v)) for k, v in result.items()})


@admin_bp.route("/loans/<int:loan_id>/settlement-reconciliation", methods=["POST"], strict_slashes=False)
@admin_bp.route("/loans/<int:loan_id>/reconciliation", methods=["POST"], strict_slashes=False)
@admin_bp.route("/loans/<int:loan_id>/reconcile", methods=["POST"], strict_slashes=False)
@admin_bp.route("/loans/<int:loan_id>/reconcile-loan", methods=["POST"], strict_slashes=False)
@role_required(["admin"])
def settlement_reconciliation_post(loan_id):
    if not (request.get_json(silent=True) or {}).get("confirm"):
        return jsonify({"error": "confirmation_required", "message": "Confirm the settlement reconciliation before posting."}), 422
    loan = Loan.query.get(loan_id)
    if loan is None:
        return jsonify({"error": "loan_not_found", "message": "The selected loan was not found."}), 404
    payload = request.get_json(silent=True) or {}
    # The UI uses this explicit name.  Retain the original spelling for older
    # clients, but never make a zero waiver require waiver documentation.
    waiver_requested = bool(payload.get("waive_remaining_delay_interest", payload.get("waive_delay_interest", False)))
    raw_waiver_amount = payload.get("delay_interest_waiver_amount", "0.00")
    try:
        waiver_amount = Decimal(str(raw_waiver_amount or "0.00")).quantize(Decimal("0.01"))
    except Exception:
        return jsonify({"error": "invalid_delay_interest_waiver", "message": "delay_interest_waiver_amount must be a currency amount."}), 422
    if waiver_amount < 0:
        return jsonify({"error": "invalid_delay_interest_waiver", "message": "delay_interest_waiver_amount cannot be negative."}), 422
    if waiver_amount > 0 and not str(payload.get("reason") or "").strip():
        return jsonify({"error": "waiver_reason_required", "message": "reason is required for a delay interest waiver."}), 422
    # Credit is calculated from posted receipts on the server; a stale client
    # preview may be supplied only as an optimistic-concurrency assertion.
    supplied_credit = payload.get("proposed_customer_credit", payload.get("customer_credit"))
    if supplied_credit is not None:
        try:
            supplied_credit = Decimal(str(supplied_credit)).quantize(Decimal("0.01"))
        except Exception:
            return jsonify({"error": "invalid_customer_credit", "message": "customer credit must be a currency amount."}), 422
        current_credit = settlement_preview(loan)["proposed_customer_credit"]
        if supplied_credit != current_credit:
            return jsonify({"error": "stale_settlement_preview", "message": "The supplied customer credit no longer matches the server calculation.", "proposed_customer_credit": float(current_credit)}), 409
    previous_status = loan.status
    try:
        loan, result = finalize_loan_reconciliation(
            loan_id, user_id=get_jwt_identity(), waiver_amount=waiver_amount,
            approval_reference=payload.get("approval_reference"), reason=payload.get("reason"),
            waive_delay_interest=waiver_requested,
        )
    except Exception as exc:
        db.session.rollback()
        return jsonify({"error": "settlement_reconciliation_failed", "message": str(exc)}), 422
    return jsonify(_canonical_reconciliation_result(loan, previous_status, result))



def _early_json(value):
    if isinstance(value, Decimal): return float(value)
    if isinstance(value, date): return value.isoformat()
    if isinstance(value, list): return [_early_json(item) for item in value]
    if isinstance(value, dict): return {key: _early_json(item) for key, item in value.items()}
    return value

def _early_payload():
    payload = request.get_json(silent=True) or {}
    try: settlement_date = date.fromisoformat(payload.get("settlement_date"))
    except (TypeError, ValueError): raise EarlySettlementError("invalid_settlement_date", "settlement_date must be YYYY-MM-DD.")
    return payload, settlement_date

@admin_bp.route("/loans/<int:loan_id>/early-settlement/preview", methods=["POST"], strict_slashes=False)
@role_required(["admin"])
def early_settlement_preview(loan_id):
    try:
        payload, settlement_date = _early_payload()
        return jsonify(_early_json(preview_early_loan_settlement(loan_id, settlement_date, payload.get("interest_rebate", 0), payload.get("penalty_waiver", 0))))
    except LookupError: return jsonify({"error":"loan_not_found", "message":"The selected loan was not found."}), 404
    except EarlySettlementError as exc: return jsonify({"error":exc.error,"message":exc.message}), 422

@admin_bp.route("/loans/<int:loan_id>/early-settlement", methods=["POST"], strict_slashes=False)
@role_required(["admin"])
def early_settlement_post(loan_id):
    try:
        payload, settlement_date = _early_payload()
        if not payload.get("confirm"): return jsonify({"error":"confirmation_required","message":"Confirm the early settlement before posting."}), 422
        # If a UI sends its preview total, treat it as an optimistic-concurrency
        # assertion; balances are still recalculated by the service.
        fresh = preview_early_loan_settlement(loan_id, settlement_date, payload.get("interest_rebate", 0), payload.get("penalty_waiver", 0))
        supplied_total = payload.get("final_settlement_amount")
        if supplied_total is not None and Decimal(str(supplied_total)).quantize(Decimal("0.01")) != fresh["final_settlement_amount"]:
            return jsonify({"error":"settlement_preview_stale", "message":"Loan balances changed after preview. Review the settlement again."}), 409
        result=post_early_loan_settlement(loan_id, settlement_date, payload.get("interest_rebate", 0), payload.get("penalty_waiver", 0), payload.get("approval_reference"), payload.get("reason"), get_jwt_identity())
        if result.get("posted"): db.session.commit()
        else: db.session.rollback()
        return jsonify(_early_json(result))
    except LookupError: db.session.rollback(); return jsonify({"error":"loan_not_found", "message":"The selected loan was not found."}), 404
    except EarlySettlementError as exc: db.session.rollback(); return jsonify({"error":exc.error,"message":exc.message}), 422
    except Exception as exc: db.session.rollback(); return jsonify({"error":"early_settlement_failed","message":str(exc)}), 422

@admin_bp.route("/loans/<int:loan_id>/early-settlement/<int:settlement_id>/reverse", methods=["POST"], strict_slashes=False)
@role_required(["admin"])
def early_settlement_reverse(loan_id, settlement_id):
    try:
        settlement=reverse_early_loan_settlement(loan_id, settlement_id, get_jwt_identity(), (request.get_json(silent=True) or {}).get("reason")); db.session.commit()
        return jsonify({"loan_id":loan_id,"settlement_id":settlement.id,"status":settlement.status})
    except EarlySettlementError as exc: db.session.rollback(); return jsonify({"error":exc.error,"message":exc.message}), 422


def _canonical_reconciliation_result(loan, previous_status, result):
    """Keep every reconciliation URL on the same stable JSON contract."""
    number = lambda value: float(value) if isinstance(value, Decimal) else value
    settled = result.get("settled_date") if result.get("processed") else None
    return {
        "success": bool(result.get("processed")),
        "loan_id": loan.id,
        "loan_number": loan.loan_number,
        "previous_status": previous_status,
        "new_status": serialize_loan_status(loan),
        "status": serialize_loan_status(loan),
        "settled_date": settled.isoformat() if hasattr(settled, "isoformat") else settled,
        "settled_at": loan.settled_at.isoformat() if result.get("processed") and loan.settled_at else None,
        "reconciliation_id": result.get("settlement_journal_id"),
        "reclassification_journal_id": result.get("reclassification_journal_id"),
        "waiver_journal_id": result.get("waiver_journal_id"),
        "total_payable": number(result["total_payable"]),
        "total_paid": number(result["total_paid"]),
        "total_cash_received": number(result["total_cash_received"]),
        "total_applied_to_loan": number(result["total_applied_to_loan"]),
        "unapplied_excess": number(result["unapplied_excess"]),
        "remaining_balance": number(result["remaining_balance"]),
        "outstanding": number(result["outstanding"]),
        "principal_outstanding": number(result.get("principal_outstanding", result.get("contractual_principal_outstanding"))),
        "contractual_interest_outstanding": number(result.get("contractual_interest_outstanding")),
        "delay_interest_outstanding": number(result.get("delay_interest_outstanding")),
        "overpayment": number(result["overpayment"]),
        "proposed_customer_credit": number(result["proposed_customer_credit"]),
        "customer_credit_created": number(result.get("customer_credit_created", 0)),
        "customer_credit_balance": number(result.get("customer_credit_balance", 0)),
        "delay_interest_reclassified": number(result.get("delay_interest_reclassified", 0)),
        "effective_delay_interest_paid": number(result.get("effective_delay_interest_paid", 0)),
        "delay_interest_waived": number(result.get("delay_interest_waived_this_reconciliation", 0)),
        "customer_credit": (
            {"available_amount": number(result["proposed_customer_credit"]), "status": "AVAILABLE"}
            if result.get("processed") and result["proposed_customer_credit"] > Decimal("0.00") else None
        ),
        "warnings": result["warnings"],
        "message": (
            "Loan settled and customer credit created successfully."
            if result.get("processed") and result["proposed_customer_credit"] > Decimal("0.00")
            else "Loan reconciled and settled successfully."
            if result.get("processed")
            else "Loan was not settled; an outstanding balance or reconciliation issue remains."
        ),
    }


def _preview_error_response(exc):
    if isinstance(exc, ValidationError):
        return jsonify(exc.payload), 422
    if str(exc) == "Documentation Charge is required for this loan.":
        return jsonify({"error": "Required disbursement charge missing", "message": str(exc)}), 422
    return jsonify({"error": str(exc), "message": str(exc)}), 422


@admin_bp.route("/loan-applications/<int:application_id>/disbursement-preview", methods=["POST"], strict_slashes=False)
@role_required(["admin"])
def application_disbursement_preview(application_id):
    try:
        return jsonify(preview_loan_application_disbursement(application_id, request.get_json(silent=True) or {}, get_jwt_identity())), 200
    except LookupError:
        return jsonify({"error": "not_found", "message": "Loan application not found"}), 404
    except AccountingError as exc:
        return _preview_error_response(exc)


@admin_bp.route("/loan-applications/<int:application_id>/disburse", methods=["POST"], strict_slashes=False)
@role_required(["admin"])
def admin_disburse_application(application_id):
    from .loan_applications import disburse_application

    return disburse_application(application_id)


@admin_bp.route("/loans/<int:record_id>/disbursement-preview", methods=["POST"], strict_slashes=False)
@role_required(["admin"])
def loan_disbursement_preview(record_id):
    loan = Loan.query.get(record_id)
    if loan is not None:
        data = request.get_json(silent=True) or {}
        try:
            funding_account = None
            if data.get("funding_account_id") is not None:
                funding_account = AccountingAccount.query.get(int(data["funding_account_id"]))
            disbursement_date = date.fromisoformat(data.get("disbursement_date")) if data.get("disbursement_date") else (loan.start_date or date.today())
            require_open_accounting_period(disbursement_date)
            preview = preview_loan_disbursement(loan, data.get("charges") or [], funding_account, disbursement_date)
            from ..accounting import _json_ready_disbursement_preview
            result = _json_ready_disbursement_preview(preview)
            result["deprecation_warning"] = "Use POST /admin/loan-applications/{application_id}/disbursement-preview before disbursement."
            return jsonify(result), 200
        except AccountingError as exc:
            return _preview_error_response(exc)
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid disbursement preview payload"}), 400
    try:
        result = preview_loan_application_disbursement(record_id, request.get_json(silent=True) or {}, get_jwt_identity())
        result["deprecation_warning"] = "Use POST /admin/loan-applications/{application_id}/disbursement-preview before disbursement."
        return jsonify(result), 200
    except LookupError:
        return jsonify({"error": "not_found", "message": "Loan or loan application not found"}), 404
    except AccountingError as exc:
        return _preview_error_response(exc)

def _loan_to_dict(loan: Loan) -> dict:
    from ..loan_totals import loan_totals
    config = loan_config_summary(loan)
    totals = loan_totals(loan)
    return {
        "id": loan.id,
        "loan_number": loan.loan_number,
        "customer_id": loan.customer_id,
        "customer": _loan_customer_to_dict(loan.customer),
        "currency": CURRENCY_CODE,
        "principal_amount": float(loan.principal_amount),
        "principal_amount_formatted": format_currency(loan.principal_amount),
        "gross_principal_amount": float(loan.gross_principal_amount or loan.principal_amount),
        "total_disbursement_deductions": float(loan.total_disbursement_deductions or 0),
        "net_disbursed_amount": float(loan.net_disbursed_amount or loan.principal_amount),
        "disbursement_charge_count": loan.disbursement_charge_count or 0,
        "disbursement_deductions_posted": bool(loan.disbursement_deductions_posted),
        "disbursement_deductions": [{"code": d.charge_type.code if d.charge_type else None, "name": d.charge_type.name if d.charge_type else d.description, "amount": float(d.gross_amount), "tax_amount": float(d.tax_amount or 0), "net_charge_amount": float(d.net_charge_amount), "accounting_treatment": d.accounting_treatment, "account_code": d.destination_account.account_code if d.destination_account else None, "account_name": d.destination_account.account_name if d.destination_account else None, "status": d.status} for d in getattr(loan, "disbursement_deductions", [])],
        "interest_rate": float(loan.interest_rate),
        "total_days": loan.total_days,
        "payment_interval_days": loan.payment_interval_days,
        "start_date": config["start_date"].isoformat() if config.get("start_date") else None,
        "end_date": config["end_date"].isoformat() if config.get("end_date") else None,
        "term_type": config["term_type"],
        "term_value": config["term_value"],
        "loan_days": config["loan_days"],
        "tenure_months": loan.tenure_months,
        "repayment_frequency": config["repayment_frequency"],
        "term_display": config["term_display"],
        "number_of_installments": config["number_of_installments"],
        "installment_count": config["installment_count"],
        "installment_amount": (
            float(loan.installment_amount)
            if loan.installment_amount is not None
            else None
        ),
        "total_repayment": (
            float(loan.total_repayment) if loan.total_repayment is not None else None
        ),
        "total_payable": float(loan.total_payable) if loan.total_payable is not None else None,
        "total_interest": (
            float(loan.total_interest) if loan.total_interest is not None else None
        ),
        "interest_type": loan.interest_type,
        "interest_rate_basis": loan.interest_rate_basis,
        "maturity_date": config["maturity_date"].isoformat() if config.get("maturity_date") else None,
        "final_installment_due_date": (
            config["final_installment_due_date"].isoformat()
            if config.get("final_installment_due_date")
            else None
        ),
        "status": serialize_loan_status(loan),
        "total_paid": float(totals["cash_paid"]),
        "cash_paid": float(totals["cash_paid"]),
        "principal_paid": float(totals["principal_paid"]),
        "normal_interest_paid": float(totals["normal_interest_paid"]),
        "delay_interest_paid": float(totals["delay_interest_paid"]),
        "penalty_paid": float(totals["penalty_paid"]),
        "fees_paid": float(totals["fees_paid"]),
        "interest_waived": float(totals["interest_waived"]),
        "delay_interest_waived": float(totals["delay_interest_waived"]),
        "penalty_waived": float(totals["penalty_waived"]),
        "delay_interest_waiver_amount": float(totals["delay_interest_waived"]),
        "settlement_adjustments": float(totals["settlement_adjustments"]),
        "gross_satisfied_amount": float(totals["gross_satisfied_amount"]),
        "outstanding_amount": float(totals["outstanding_amount"]),
        "customer_credit_balance": float(loan.customer_credit_balance or 0),
        "settlement_type": loan.settlement_type,
        "interest_rebate_amount": float(loan.interest_rebate_amount or 0),
        "penalty_waiver_amount": float(loan.penalty_waiver_amount or 0),
        "final_settlement_amount": float(loan.early_settlements[-1].final_settlement_amount) if loan.early_settlements else None,
        "original_total_payable": float(loan.total_payable or 0),
        "revised_settlement_total": float((loan.total_payable or 0) - (loan.interest_rebate_amount or 0) - (loan.penalty_waiver_amount or 0)),
        "early_settlement_status": loan.early_settlements[-1].status if loan.early_settlements else None,
        "early_settlement_date": loan.early_settlements[-1].settlement_date.isoformat() if loan.early_settlements else None,
        "early_settlement_reference": loan.early_settlements[-1].settlement_number if loan.early_settlements else None,
        "settled_date": loan.settled_date.isoformat() if loan.settled_date else None,
        "interest_accounting_method": loan.interest_accounting_method,
        "historical_accrual_mode": loan.historical_accrual_mode,
        "accrual_processed_through": loan.accrual_processed_through.isoformat() if loan.accrual_processed_through else None,
        "accrued_interest": float(sum((acct_money(e.interest_amount) for e in loan.ledger_entries if e.interest_accrued), Decimal("0.00"))),
        "unaccrued_due_interest": float(sum((acct_money(e.interest_amount) for e in loan.ledger_entries if (not e.interest_accrued and e.due_date and e.due_date <= date.today())), Decimal("0.00"))),
        "future_interest": float(sum((acct_money(e.interest_amount) for e in loan.ledger_entries if e.due_date and e.due_date > date.today()), Decimal("0.00"))),
        "interest_receivable_balance": float(sum((acct_money(e.interest_amount) - acct_money(e.interest_paid) for e in loan.ledger_entries if e.interest_accrued), Decimal("0.00"))),
        "delay_interest_receivable_balance": float(sum((acct_money(e.delay_interest_accrued) - acct_money(e.delay_interest_paid) for e in loan.ledger_entries), Decimal("0.00"))),
        "principal_receivable_balance": float(sum((acct_money(e.principal_amount) - acct_money(e.principal_paid) for e in loan.ledger_entries), Decimal("0.00"))),
    }


def _ledger_to_dict(entry: LoanLedger) -> dict:
    return {
        "id": entry.id,
        "loan_id": entry.loan_id,
        "installment_no": entry.installment_no,
        "period_start_date": (
            entry.period_start_date.isoformat() if entry.period_start_date else None
        ),
        "due_date": entry.due_date.isoformat() if entry.due_date else None,
        "period_days": entry.period_days,
        "currency": CURRENCY_CODE,
        "opening_balance": float(entry.opening_balance),
        "opening_balance_formatted": format_currency(entry.opening_balance),
        "interest_amount": float(entry.interest_amount),
        "interest_amount_formatted": format_currency(entry.interest_amount),
        "principal_amount": float(entry.principal_amount),
        "principal_amount_formatted": format_currency(entry.principal_amount),
        "installment_amount": float(entry.installment_amount),
        "installment_amount_formatted": format_currency(entry.installment_amount),
        "closing_balance": float(entry.closing_balance),
        "closing_balance_formatted": format_currency(entry.closing_balance),
        "paid_amount": float(entry.paid_amount or 0),
        "paid_amount_formatted": format_currency(entry.paid_amount or 0),
        "paid_date": entry.paid_date.isoformat() if entry.paid_date else None,
        "last_payment_date": entry.last_payment_date.isoformat() if entry.last_payment_date else None,
        "delay_days": entry.delay_days or 0,
        "delay_interest": float(entry.delay_interest or 0),
        "delay_interest_formatted": format_currency(entry.delay_interest or 0),
        "status": entry.status,
        "interest_accrued": bool(entry.interest_accrued),
        "interest_accrued_at": entry.interest_accrued_at.isoformat() if entry.interest_accrued_at else None,
        "interest_accrual_journal_id": entry.interest_accrual_journal_id,
        "principal_paid": float(entry.principal_paid or 0),
        "interest_paid": float(entry.interest_paid or 0),
        "contractual_interest_due": float(entry.interest_amount or 0),
        "contractual_interest_outstanding": float(max(Decimal("0"), Decimal(entry.interest_amount or 0) - Decimal(entry.interest_paid or 0))),
        "principal_due": float(entry.principal_amount or 0),
        "principal_outstanding": float(max(Decimal("0"), Decimal(entry.principal_amount or 0) - Decimal(entry.principal_paid or 0))),
        "delay_interest_paid": float(entry.delay_interest_paid or 0),
        "waived_interest_amount": float(entry.waived_interest_amount or 0),
        "waived_delay_interest_amount": float(entry.waived_delay_interest_amount or 0),
        "waived_penalty_amount": float(entry.waived_penalty_amount or 0),
        "delay_interest_accrued": float(entry.delay_interest_accrued or 0),
        "delay_interest_waived": float(entry.delay_interest_waived or 0),
        "delay_interest_outstanding": float(max(Decimal("0"), Decimal(entry.delay_interest_accrued or 0) - Decimal(entry.delay_interest_paid or 0) - Decimal(entry.delay_interest_waived or 0))),
        "contractual_status": "PAID" if Decimal(entry.principal_paid or 0) >= Decimal(entry.principal_amount or 0) and Decimal(entry.interest_paid or 0) >= Decimal(entry.interest_amount or 0) else entry.status,
        "delay_interest_status": "OUTSTANDING" if Decimal(entry.delay_interest_accrued or 0) > Decimal(entry.delay_interest_paid or 0) + Decimal(entry.delay_interest_waived or 0) else "SETTLED",
        "unapplied_amount": float(entry.unapplied_amount or 0),
        "due_status": "OVERDUE" if entry.due_date and entry.due_date < date.today() and entry.status != "PAID" else entry.status,
    }


@admin_bp.route("/loans/<int:loan_id>/ledger", methods=["GET"])
@role_required(["admin"])
def get_loan_ledger(loan_id):
    loan = Loan.query.options(
        joinedload(Loan.customer), joinedload(Loan.ledger_entries)
    ).get_or_404(loan_id)
    if not loan.ledger_entries:
        generate_loan_ledger(loan)
        db.session.commit()
    return jsonify(
        {
            "loan": _loan_to_dict(loan),
            "summary": ledger_totals(loan) | {"installment_count": len(loan.ledger_entries)},
            "ledger": [_ledger_to_dict(entry) for entry in loan.ledger_entries],
            "items": [_ledger_to_dict(entry) for entry in loan.ledger_entries],
            "totals": ledger_totals(loan),
        }
    )


@admin_bp.route("/loans/<int:loan_id>/ledger/<int:entry_id>/payment", methods=["POST"])
@role_required(["admin"])
def record_ledger_payment(loan_id, entry_id):
    data = request.get_json(silent=True) or {}
    logger.info(
        "Record payment request path=%s method=%s loan_id=%s payload_keys=%s",
        request.path,
        request.method,
        loan_id,
        sorted(data.keys()),
    )
    loan = Loan.query.get_or_404(loan_id)
    LoanLedger.query.filter_by(id=entry_id, loan_id=loan.id).first_or_404()

    raw_paid_amount = data.get("paid_amount")
    if raw_paid_amount is None:
        raw_paid_amount = data.get("amount")
    try:
        paid_amount = acct_money(Decimal(str(raw_paid_amount)))
    except (InvalidOperation, TypeError, ValueError):
        return jsonify({"error": "Invalid payment amount", "message": "paid_amount must be a valid number"}), 422
    if paid_amount <= Decimal("0"):
        return jsonify({"error": "Invalid payment amount", "message": "paid_amount must be greater than zero"}), 422

    try:
        paid_date = date.fromisoformat(data.get("payment_date") or data.get("paid_date") or date.today().isoformat())
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid payment date", "message": "payment_date must be a valid ISO date"}), 422
    if paid_date < loan.start_date:
        return jsonify({"error": "Invalid payment date", "message": "payment_date cannot be before loan disbursement date"}), 422

    method = (data.get("collection_method") or data.get("payment_method") or "CASH_OFFICE").upper()
    if method == "CASH":
        method = "CASH_OFFICE"
    if method == "BANK":
        method = "BANK_TRANSFER"

    collector_id = data.get("collector_id")
    account_id = data.get("collection_account_id") or data.get("receipt_account_id")
    reference = data.get("reference") or data.get("transaction_reference")
    remarks = data.get("remarks")
    if reference is not None and not isinstance(reference, str):
        return jsonify({"error": "Invalid reference", "message": "reference must be a string"}), 422
    if remarks is not None and not isinstance(remarks, str):
        return jsonify({"error": "Invalid remarks", "message": "remarks must be a string"}), 422
    if method not in {"CASH_COLLECTOR", "BANK_TRANSFER", "CASH_OFFICE", "CHEQUE", "MOBILE_TRANSFER", "OTHER"}:
        return jsonify({"error": "Invalid collection method", "message": "collection_method is not supported"}), 422

    receipt_account = None
    if account_id is not None:
        try:
            receipt_account = validate_collection_account(AccountingAccount.query.get(int(account_id)), method, collector_id)
        except (AccountingError, TypeError, ValueError) as exc:
            return jsonify({"error": "Collector setup incomplete", "message": str(exc)}), 422
    elif method == "CASH_COLLECTOR":
        return jsonify({"error": "Collector setup incomplete", "message": "The selected collector has no active posting collection account."}), 422

    try:
        require_open_accounting_period(paid_date)
        if str(getattr(loan, "interest_accounting_method", "ACCRUAL_BY_INSTALLMENT")) == "ACCRUAL_BY_INSTALLMENT":
            accrue_due_loan_interest(paid_date, loan.id, historical=True, requested_by=int(get_jwt_identity()))
        if not loan.ledger_entries:
            generate_loan_ledger(loan)
            db.session.flush()
        entry = LoanLedger.query.filter_by(id=entry_id, loan_id=loan.id).first_or_404()
        entry.delay_days = max((paid_date - entry.due_date).days, 0)
        entry.delay_interest = money(Decimal(entry.opening_balance) * daily_interest_rate(loan) * Decimal(entry.delay_days))
        entry.delay_interest_accrued = entry.delay_interest
        principal_paid, interest_paid, penalty_paid, other_fee_paid = allocate_payment(loan, paid_amount, paid_date)
        payment = Payment(
            loan_id=loan.id, amount_collected=paid_amount, principal_paid=principal_paid,
            interest_paid=interest_paid, penalty_paid=penalty_paid, other_fee_paid=other_fee_paid,
            collection_date=paid_date, payment_date=paid_date, accounting_date=paid_date,
            collected_by_id=int(get_jwt_identity()), collector_id=int(collector_id) if collector_id else None,
            payment_method=method, collection_method=method, remarks=remarks,
            transaction_reference=reference,
            receipt_account_id=receipt_account.id if receipt_account else None,
            collection_account_id=receipt_account.id if receipt_account else None,
            bank_reference=reference,
        )
        db.session.add(payment)
        db.session.flush()
        journal = post_loan_payment(payment, int(get_jwt_identity()), receipt_account=receipt_account)
        if not payment.journal_id:
            raise AccountingError("Payment journal was not created")
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.exception("Record payment accounting failure loan_id=%s", loan_id)
        payload = exc.args[0] if isinstance(exc, AccountingError) and exc.args and isinstance(exc.args[0], dict) else {"error": "payment_accounting_failed", "message": str(exc)}
        return jsonify(payload), 422 if method == "CASH_COLLECTOR" or isinstance(exc, AccountingError) else 400
    payload = _payment_success_payload(payment, journal)
    payload.update({"ledger": _ledger_to_dict(entry), "totals": ledger_totals(loan)})
    return jsonify(payload)

def _payment_success_payload(payment, journal):
    acct = payment.collection_account
    alloc = {"delay_interest": Decimal("0.00"), "interest": Decimal("0.00"), "principal": Decimal("0.00"), "unapplied": Decimal("0.00")}
    for a in payment.allocations:
        key = {"DELAY_INTEREST": "delay_interest", "INTEREST": "interest", "PRINCIPAL": "principal", "UNAPPLIED": "unapplied"}.get(a.allocation_type)
        if key:
            alloc[key] = acct_money(alloc[key] + Decimal(a.amount))
    credit = CustomerCreditBalance.query.filter_by(payment_id=payment.id).first()
    return {"message": "Payment recorded", "payment_id": payment.id, "receipt_number": payment.receipt_number,
            "journal_entry_id": payment.journal_id, "journal_number": journal.journal_no,
            "paid_amount": float(acct_money(payment.amount_collected)),
            "loan_status": payment.loan.status, "settled_date": payment.loan.settled_date.isoformat() if payment.loan.settled_date else None,
            "total_applied_to_loan": float(acct_money(payment.amount_collected) - acct_money(payment.other_fee_paid)),
            "overpayment": float(acct_money(payment.other_fee_paid)), "outstanding_amount": float(payment.loan.outstanding),
            "customer_credit": {"id": credit.id, "credit_number": credit.credit_number, "available_amount": float(credit.available_amount), "status": credit.status} if credit else None,
            "allocation": {key: float(value) for key, value in alloc.items()},
            "collection_account": {"id": acct.id, "code": acct.account_code, "name": acct.account_name} if acct else None,
            "deposit_status": payment.deposit_status}


@admin_bp.route("/payments/<int:payment_id>/repair-accounting", methods=["POST"], strict_slashes=False)
@role_required(["admin"])
def repair_payment_accounting(payment_id):
    try:
        result = repair_unposted_payment(payment_id, int(get_jwt_identity()))
        db.session.commit()
        return jsonify(result)
    except AccountingError as exc:
        db.session.rollback()
        return jsonify(exc.args[0] if exc.args and isinstance(exc.args[0], dict) else {"message": str(exc)}), 422


@admin_bp.route("/dashboard", methods=["GET"])
@role_required(["admin"])
def dashboard():
    normalized_loan_status = func.upper(func.trim(Loan.status))
    normalized_payment_status = func.upper(func.trim(Payment.status))

    total_customers = int(Customer.query.count() or 0)
    total_loans = int(db.session.query(func.count(Loan.id)).scalar() or 0)
    active_loans_query = Loan.query.filter(normalized_loan_status.in_(ACTIVE_LOAN_STATUSES))
    active_loans = active_loans_query.all()
    active_loans_count = int(
        db.session.query(func.count(Loan.id))
        .filter(normalized_loan_status.in_(ACTIVE_LOAN_STATUSES))
        .scalar()
        or 0
    )
    total_outstanding = sum((loan.outstanding for loan in active_loans), Decimal("0"))

    today = date.today()
    payments_today = int(
        db.session.query(func.count(Payment.id))
        .filter(
            Payment.collection_date == today,
            normalized_payment_status.in_(POSTED_PAYMENT_STATUSES),
            Payment.reversed_at.is_(None),
        )
        .scalar()
        or 0
    )
    todays_collection = (
        db.session.query(func.coalesce(func.sum(Payment.amount_collected), 0))
        .filter(
            Payment.collection_date == today,
            normalized_payment_status.in_(POSTED_PAYMENT_STATUSES),
            Payment.reversed_at.is_(None),
        )
        .scalar()
        or Decimal("0")
    )

    status_distribution = (
        db.session.query(Loan.status, func.count(Loan.id))
        .group_by(Loan.status)
        .order_by(Loan.status)
        .all()
    )
    response = {
        "total_customers": total_customers,
        "totalCustomers": total_customers,
        "total_loans": total_loans,
        "active_loans": active_loans_count,
        "activeLoans": active_loans_count,
        "total_active_loans": active_loans_count,
        "totalActiveLoans": active_loans_count,
        "payments_today": payments_today,
        "paymentsToday": payments_today,
        "currency": CURRENCY_CODE,
        "total_outstanding": float(total_outstanding),
        "total_outstanding_formatted": format_currency(total_outstanding),
        "todays_collection": float(todays_collection),
        "todays_collection_formatted": format_currency(todays_collection),
    }
    current_app.logger.info(
        "Dashboard loan status distribution=%s active_statuses=%s",
        [(status, int(count or 0)) for status, count in status_distribution],
        sorted(ACTIVE_LOAN_STATUSES),
    )
    current_app.logger.info(
        "Dashboard metrics total_customers=%s total_loans=%s active_loans=%s payments_today=%s response_keys=%s",
        total_customers,
        total_loans,
        active_loans_count,
        payments_today,
        sorted(response.keys()),
    )
    return jsonify(response)


@admin_bp.route("/documents/repository", methods=["GET"])
@role_required(["admin"])
def list_loan_application_documents():
    documents = (
        LoanApplicationDocument.query.join(LoanApplication)
        .join(Customer)
        .order_by(LoanApplicationDocument.uploaded_at.desc())
        .all()
    )

    items = []
    for document in documents:
        loan_application = document.loan_application
        customer = loan_application.customer if loan_application else None
        file_path = document.file_path

        items.append(
            {
                "id": document.id,
                "loan_application_id": document.loan_application_id,
                "document_type": document.document_type,
                "file_path": file_path,
                "file_url": build_public_url(file_path) if file_path else None,
                "uploaded_at": (
                    document.uploaded_at.isoformat() if document.uploaded_at else None
                ),
                "application_number": getattr(
                    loan_application, "application_number", None
                ),
                "application_status": getattr(loan_application, "status", None),
                "loan_type": getattr(loan_application, "loan_type", None),
                "customer_code": getattr(customer, "customer_code", None),
                "customer_name": getattr(customer, "full_name", None),
            }
        )

    return jsonify({"items": items})

@admin_bp.route("/accounting/accrue-interest", methods=["POST"])
@role_required(["admin"])
def admin_accrue_interest():
    data = request.get_json() or {}
    as_of = date.fromisoformat(data.get("as_of_date") or date.today().isoformat())
    try:
        summary = accrue_due_loan_interest(as_of, data.get("loan_id"), historical=True, requested_by=int(get_jwt_identity()))
        db.session.commit()
        summary["total_interest_accrued"] = float(summary["total_interest_accrued"])
        return jsonify(summary)
    except AccountingError as exc:
        db.session.rollback()
        payload = exc.args[0] if exc.args and isinstance(exc.args[0], dict) else {"message": str(exc)}
        return jsonify(payload), 422

@admin_bp.route("/accounting/interest-accrual-status", methods=["GET"])
@role_required(["admin"])
def admin_interest_accrual_status():
    q = LoanLedger.query.join(Loan)
    if request.args.get("loan_id"): q = q.filter(LoanLedger.loan_id == int(request.args["loan_id"]))
    if request.args.get("customer_id"): q = q.filter(Loan.customer_id == int(request.args["customer_id"]))
    if request.args.get("date_from"): q = q.filter(LoanLedger.due_date >= date.fromisoformat(request.args["date_from"]))
    if request.args.get("date_to"): q = q.filter(LoanLedger.due_date <= date.fromisoformat(request.args["date_to"]))
    if request.args.get("accrued") is not None: q = q.filter(LoanLedger.interest_accrued.is_(request.args["accrued"].lower() == "true"))
    if request.args.get("overdue") == "true": q = q.filter(LoanLedger.due_date < date.today(), LoanLedger.status != "PAID")
    return jsonify([_ledger_to_dict(e) for e in q.order_by(LoanLedger.due_date).all()])

@admin_bp.route("/loans/<int:loan_id>/historical-accruals", methods=["POST"])
@role_required(["admin"])
def admin_historical_accruals(loan_id):
    data = request.get_json() or {}
    as_of = date.fromisoformat(data.get("as_of_date") or date.today().isoformat())
    summary = accrue_due_loan_interest(as_of, loan_id, historical=True, requested_by=int(get_jwt_identity()))
    db.session.commit()
    summary["total_interest_accrued"] = float(summary["total_interest_accrued"])
    return jsonify(summary)

@admin_bp.route("/payments/<int:payment_id>/reverse", methods=["POST"])
@role_required(["admin"])
def admin_reverse_payment(payment_id):
    data = request.get_json() or {}
    payment = Payment.query.get_or_404(payment_id)
    try:
        rev = reverse_payment(payment, date.fromisoformat(data.get("reversal_date") or date.today().isoformat()), data.get("reason"), int(get_jwt_identity()))
        db.session.commit()
        return jsonify({"reversal_journal_id": rev.id})
    except AccountingError as exc:
        db.session.rollback(); return jsonify({"message": str(exc)}), 422

@admin_bp.route("/disbursement-charge-types", methods=["GET"])
@role_required(["admin"])
def list_disbursement_charge_types():
    current_app.logger.info("Disbursement charge types milestone=loading charge types")
    charges = (
        DisbursementChargeType.query
        .options(
            joinedload(DisbursementChargeType.income_account),
            joinedload(DisbursementChargeType.payable_account),
            joinedload(DisbursementChargeType.expense_account),
            joinedload(DisbursementChargeType.tax_payable_account),
        )
        .order_by(DisbursementChargeType.display_order, DisbursementChargeType.code)
        .limit(100)
        .all()
    )
    current_app.logger.info("Disbursement charge types milestone=completed count=%s", len(charges))
    return jsonify({"charge_types": [_charge_type_payload(charge) for charge in charges]})


def _apply_charge_type_payload(charge, data):
    for field in ["code", "name", "description", "calculation_method", "accounting_treatment", "tax_method"]:
        if field in data:
            setattr(charge, field, data[field])
    for field in ["default_amount", "default_rate", "tax_rate"]:
        if field in data:
            setattr(charge, field, None if data[field] is None else Decimal(str(data[field])))
    for field in ["active", "included_in_principal", "deducted_from_disbursement", "refundable"]:
        if field in data:
            setattr(charge, field, bool(data[field]))
    for field in ["income_account_id", "payable_account_id", "expense_account_id", "tax_payable_account_id", "display_order"]:
        if field in data:
            setattr(charge, field, data[field])

@admin_bp.route("/disbursement-charge-types", methods=["POST"])
@role_required(["admin"])
def create_disbursement_charge_type():
    data = request.get_json() or {}
    charge = DisbursementChargeType()
    _apply_charge_type_payload(charge, data)
    db.session.add(charge); db.session.commit()
    return jsonify({"charge_type": _charge_type_payload(charge)}), 201

@admin_bp.route("/disbursement-charge-types/<int:charge_id>", methods=["PATCH"])
@role_required(["admin"])
def update_disbursement_charge_type(charge_id):
    charge = DisbursementChargeType.query.get_or_404(charge_id)
    _apply_charge_type_payload(charge, request.get_json() or {})
    db.session.commit()
    return jsonify({"charge_type": _charge_type_payload(charge)})

@admin_bp.route("/disbursement-charge-types/<int:charge_id>/activate", methods=["POST"])
@role_required(["admin"])
def activate_disbursement_charge_type(charge_id):
    charge = DisbursementChargeType.query.get_or_404(charge_id)
    charge.active = True; db.session.commit()
    return jsonify({"charge_type": _charge_type_payload(charge)})

@admin_bp.route("/disbursement-charge-types/<int:charge_id>/deactivate", methods=["POST"])
@role_required(["admin"])
def deactivate_disbursement_charge_type(charge_id):
    charge = DisbursementChargeType.query.get_or_404(charge_id)
    charge.active = False; db.session.commit()
    return jsonify({"charge_type": _charge_type_payload(charge)})


@admin_bp.route("/disbursement-configuration/status", methods=["GET"])
@role_required(["admin"])
def disbursement_configuration_status():
    funding_count = sum(1 for account in AccountingAccount.query.limit(1000).all() if is_funding_account(account))
    active_charge_count = DisbursementChargeType.query.filter_by(active=True).count()
    doc_fee = DisbursementChargeType.query.filter_by(code="DOC_FEE").first()
    doc_destination = _charge_destination_account(doc_fee) if doc_fee else None
    missing = []
    if funding_count == 0:
        missing.append("No active funding account")
    if not doc_fee or not doc_fee.active or not doc_destination:
        missing.append("DOC_FEE has no destination GL account")
    elif not is_posting_account(doc_destination) or not is_active_account(doc_destination):
        missing.append("DOC_FEE destination GL account is not active for posting")
    return jsonify({
        "ready": not missing,
        "funding_accounts": funding_count,
        "active_charge_types": active_charge_count,
        "required_charge_mappings": {
            "DOC_FEE": {
                "configured": bool(doc_fee and doc_fee.active and doc_destination),
                "destination_account_code": doc_destination.account_code if doc_destination else None,
            }
        },
        "missing": missing,
    })


@admin_bp.route("/loan-applications/<int:application_id>/disbursement-options", methods=["GET"])
@role_required(["admin"])
def loan_application_disbursement_options(application_id):
    try:
        current_app.logger.info("Disbursement options milestone=loading application")
        application = LoanApplication.query.options(joinedload(LoanApplication.customer)).get_or_404(application_id)
        current_app.logger.info("Disbursement options milestone=loading active funding accounts")
        funding_accounts = [
            account for account in AccountingAccount.query.order_by(AccountingAccount.account_code).limit(500).all()
            if is_funding_account(account)
        ]
        current_app.logger.info("Disbursement options milestone=loading charge types")
        all_charges = (
            DisbursementChargeType.query
            .options(joinedload(DisbursementChargeType.income_account), joinedload(DisbursementChargeType.payable_account), joinedload(DisbursementChargeType.expense_account), joinedload(DisbursementChargeType.tax_payable_account))
            .order_by(DisbursementChargeType.display_order, DisbursementChargeType.code)
            .limit(100)
            .all()
        )
        charges = [c for c in all_charges if _charge_type_is_disbursement_ready(c)]
        warnings = [{"code": "CHARGE_ACCOUNT_MISSING", "charge_code": c.code, "message": f"{c.name} has no destination GL account."} for c in all_charges if c.active and c.deducted_from_disbursement and _charge_destination_account(c) is None]
        current_app.logger.info("Disbursement options milestone=resolving account mappings")
        settings = {s.setting_key: s for s in AccountingSetting.query.filter(AccountingSetting.setting_key.in_(["allow_manual_disbursement_charges", "require_disbursement_charge_approval", "require_documentation_charge", "allow_zero_net_disbursement"])).all()}
        current_app.logger.info("Disbursement options milestone=serializing response")
        payload = {
            "application": {
                "id": application.id,
                "application_number": application.application_number,
                "gross_principal_amount": float(application.approved_amount or application.applied_amount),
                "customer_name": application.customer.full_name if application.customer else application.full_name,
                "term_display": _term_display(application),
                "repayment_frequency": application.repayment_frequency,
            },
            "funding_accounts": [_compact_account(account) for account in funding_accounts],
            "charge_types": [_charge_type_payload(charge) for charge in charges],
            "settings": {
                "allow_manual_disbursement_charges": _setting_bool(settings, "allow_manual_disbursement_charges", True),
                "require_disbursement_charge_approval": _setting_bool(settings, "require_disbursement_charge_approval", False),
                "require_documentation_charge": _setting_bool(settings, "require_documentation_charge", True),
                "allow_zero_net_disbursement": _setting_bool(settings, "allow_zero_net_disbursement", False),
            },
            "default_charges": [{"charge_type_id": c.id, "amount": float(c.default_amount)} for c in charges if c.default_amount is not None and c.default_amount > 0],
            "warnings": warnings,
        }
        current_app.logger.info("Disbursement options milestone=completed")
        return jsonify(payload)
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Disbursement setup could not be loaded for application %s", application_id)
        return jsonify({"error": "Disbursement setup could not be loaded", "message": "Accounting configuration is unavailable."}), 500


@admin_bp.route("/loans/<int:loan_id>/reverse-disbursement", methods=["POST"])
@role_required(["admin"])
def admin_reverse_disbursement(loan_id):
    data = request.get_json() or {}
    loan = Loan.query.get_or_404(loan_id)
    try:
        result = reverse_loan_disbursement(loan, date.fromisoformat(data.get("reversal_date") or date.today().isoformat()), data.get("reason"), int(get_jwt_identity()))
        db.session.commit()
        return jsonify(result)
    except AccountingError as exc:
        db.session.rollback(); return jsonify({"message": str(exc)}), 422


def _payment_summary(p):
    loan = p.loan
    return {
        "payment_id": p.id,
        "receipt_number": p.receipt_number,
        "customer": loan.customer.full_name if loan and loan.customer else None,
        "customer_id": loan.customer_id if loan else None,
        "loan_id": p.loan_id,
        "loan_number": loan.loan_number if loan else None,
        "payment_date": (p.payment_date or p.collection_date).isoformat() if (p.payment_date or p.collection_date) else None,
        "amount_collected": f"{acct_money(p.amount_collected):.2f}",
        "amount_already_deposited": f"{acct_money(p.deposited_amount):.2f}",
        "undeposited_amount": f"{acct_money(p.undeposited_amount):.2f}",
        "deposit_status": p.deposit_status,
        "collection_account": p.collection_account.account_name if p.collection_account else None,
        "collection_account_id": p.collection_account_id,
    }


@admin_bp.route("/collections/undeposited", methods=["GET"], strict_slashes=False)
@role_required(["admin"])
def undeposited_collections():
    q = Payment.query.options(joinedload(Payment.loan).joinedload(Loan.customer)).filter(
        Payment.reversed_at.is_(None),
        Payment.collection_method == "CASH_COLLECTOR",
        Payment.status == "POSTED",
        Payment.journal_id.isnot(None),
        Payment.deposit_status.in_(["UNDEPOSITED", "PARTIALLY_DEPOSITED"]),
        Payment.amount_collected > Payment.deposited_amount,
    )
    if request.args.get("collector_id"): q = q.filter(Payment.collector_id == int(request.args["collector_id"]))
    if request.args.get("account_id"): q = q.filter(Payment.collection_account_id == int(request.args["account_id"]))
    if request.args.get("loan_id"): q = q.filter(Payment.loan_id == int(request.args["loan_id"]))
    if request.args.get("customer_id"): q = q.join(Loan).filter(Loan.customer_id == int(request.args["customer_id"]))
    if request.args.get("date_from"): q = q.filter(Payment.collection_date >= date.fromisoformat(request.args["date_from"]))
    if request.args.get("date_to"): q = q.filter(Payment.collection_date <= date.fromisoformat(request.args["date_to"]))
    if request.args.get("deposit_status"): q = q.filter(Payment.deposit_status == request.args["deposit_status"])
    return jsonify({"items": [_payment_summary(p) for p in q.order_by(Payment.collection_date, Payment.id).all()]})


@admin_bp.route("/collection-deposits/preview", methods=["POST"], strict_slashes=False)
@role_required(["admin"])
def preview_deposit():
    try:
        result = preview_collection_deposit(request.get_json() or {})
        return jsonify(result)
    except ValidationError as exc:
        return jsonify(exc.payload), 422
    except AccountingError as exc:
        return jsonify(exc.args[0] if exc.args and isinstance(exc.args[0], dict) else {"message": str(exc), "error": str(exc)}), 422
    except Exception:
        db.session.rollback()
        logger.exception("Unexpected collection deposit preview failure")
        return jsonify({"error": "Internal server error", "message": "Unable to preview collection deposit."}), 500


def _deposit_dict(b):
    return {"id": b.id, "deposit_number": b.deposit_number, "collector_id": b.collector_id, "collector": b.collector.name if b.collector else None, "collector_account_id": b.collector_account_id, "collector_account": b.collector_account.account_name if b.collector_account else None, "bank_account_id": b.bank_account_id, "bank_account": b.bank_account.account_name if b.bank_account else None, "deposit_date": b.deposit_date.isoformat(), "accounting_date": b.accounting_date.isoformat(), "total_amount": f"{acct_money(b.total_amount):.2f}", "bank_reference": b.bank_reference, "deposit_slip_reference": b.deposit_slip_reference, "remarks": b.remarks, "journal_entry_id": b.journal_entry_id, "status": b.status, "allocations": [{"payment_id": a.payment_id, "allocated_amount": f"{acct_money(a.allocated_amount):.2f}"} for a in b.allocations]}


@admin_bp.route("/collection-deposits", methods=["POST"], strict_slashes=False)
@role_required(["admin"])
def create_deposit():
    try:
        batch = create_collection_deposit(request.get_json() or {}, int(get_jwt_identity()))
        db.session.commit()
        journal = batch.journal_entry
        return jsonify({
            "deposit_batch_id": batch.id,
            "deposit_number": batch.deposit_number,
            "journal_entry_id": batch.journal_entry_id,
            "journal_number": journal.journal_no if journal else None,
            "total_amount": float(acct_money(batch.total_amount)),
            "collector_account_balance_after": float(acct_money(getattr(batch, "_collector_account_balance_after", 0))),
            "status": batch.status,
        }), 201
    except ValidationError as exc:
        db.session.rollback()
        return jsonify(exc.payload), 422
    except AccountingError as exc:
        db.session.rollback()
        return jsonify(exc.args[0] if exc.args and isinstance(exc.args[0], dict) else {"message": str(exc), "error": str(exc)}), 422
    except Exception:
        db.session.rollback()
        logger.exception("Unexpected collection deposit posting failure")
        return jsonify({"error": "Internal server error", "message": "Unable to post collection deposit."}), 500


@admin_bp.route("/collection-deposits", methods=["GET"], strict_slashes=False)
@role_required(["admin"])
def list_deposits():
    q = CollectionDepositBatch.query
    if request.args.get("collector_id"): q = q.filter_by(collector_id=int(request.args["collector_id"]))
    if request.args.get("status"): q = q.filter_by(status=request.args["status"])
    if request.args.get("date_from"): q = q.filter(CollectionDepositBatch.deposit_date >= date.fromisoformat(request.args["date_from"]))
    if request.args.get("date_to"): q = q.filter(CollectionDepositBatch.deposit_date <= date.fromisoformat(request.args["date_to"]))
    return jsonify({"items": [_deposit_dict(b) for b in q.order_by(CollectionDepositBatch.deposit_date.desc(), CollectionDepositBatch.id.desc()).all()]})


@admin_bp.route("/collection-deposits/<int:deposit_id>", methods=["GET"], strict_slashes=False)
@role_required(["admin"])
def get_deposit(deposit_id):
    return jsonify(_deposit_dict(CollectionDepositBatch.query.get_or_404(deposit_id)))


@admin_bp.route("/collection-deposits/<int:deposit_id>/reverse", methods=["POST"], strict_slashes=False)
@role_required(["admin"])
def reverse_deposit(deposit_id):
    data = request.get_json() or {}
    try:
        rev = reverse_collection_deposit(CollectionDepositBatch.query.get_or_404(deposit_id), date.fromisoformat(data.get("reversal_date") or date.today().isoformat()), data.get("reason"), int(get_jwt_identity()))
        db.session.commit()
        return jsonify({"reversal_journal_id": rev.id})
    except AccountingError as exc:
        db.session.rollback(); return jsonify({"message": str(exc)}), 422


@admin_bp.route("/collectors/<int:collector_id>/cash-position", methods=["GET"], strict_slashes=False)
@role_required(["admin"])
def collector_position(collector_id):
    as_of = date.fromisoformat(request.args["as_of_date"]) if request.args.get("as_of_date") else None
    pos = collector_cash_position(collector_id, as_of); collector = User.query.get_or_404(collector_id)
    return jsonify({"collector": collector.name, "opening_balance": "0.00", "collections": f"{pos['collections']:.2f}", "deposits": f"{pos['deposits']:.2f}", "adjustments": "0.00", "closing_balance": f"{pos['closing_balance']:.2f}", "undeposited_payments": [_payment_summary(p) for p in pos["undeposited_payments"]]})


@admin_bp.route("/collections/reconciliation", methods=["GET"])
@role_required(["admin"])
def collections_reconciliation():
    accounts = AccountingAccount.query.filter_by(is_collection_account=True).all()
    items=[]
    for acct in accounts:
        deb = db.session.query(db.func.coalesce(db.func.sum(AccountingJournalLine.debit), 0)).filter_by(account_id=acct.id).scalar()
        cre = db.session.query(db.func.coalesce(db.func.sum(AccountingJournalLine.credit), 0)).filter_by(account_id=acct.id).scalar()
        gl = acct_money(deb) - acct_money(cre)
        pos = collector_cash_position(acct.collector_id) if acct.collector_id else {"closing_balance": acct_money(0)}
        sub = acct_money(pos["closing_balance"])
        items.append({"collector_id": acct.collector_id, "collector": acct.collector.name if acct.collector else None, "account_id": acct.id, "account": acct.account_name, "gl_collection_account_balance": f"{gl:.2f}", "collector_subledger_balance": f"{sub:.2f}", "difference": f"{acct_money(gl-sub):.2f}"})
    return jsonify({"items": items})

def _collector_account_payload(account):
    if not account:
        return None
    return {"id": account.id, "code": account.account_code, "name": account.account_name}


def _collector_payload(user):
    account = AccountingAccount.query.get(user.default_collection_account_id) if user.default_collection_account_id else None
    return {
        "id": user.id,
        "staff_id": user.id,
        "name": user.name,
        "employee_code": getattr(user, "employee_code", None),
        "collector_code": user.collector_code,
        "status": user.collector_status,
        "is_collector": user.is_collector,
        "can_collect_cash": user.can_collect_cash,
        "default_collection_account": _collector_account_payload(account),
    }


@admin_bp.route("/collectors", methods=["GET"], strict_slashes=False)
@role_required(["admin"])
def list_collectors():
    q = User.query.filter(User.is_collector.is_(True))
    status = request.args.get("status")
    if status:
        q = q.filter(User.collector_status == status.upper())
    if request.args.get("active_only", "").lower() in ("1", "true", "yes"):
        q = q.filter(User.collector_status == "ACTIVE", User.is_active.is_(True))
    if request.args.get("search"):
        s = f"%{request.args['search']}%"
        q = q.filter((User.name.ilike(s)) | (User.email.ilike(s)) | (User.collector_code.ilike(s)))
    return jsonify({"items": [_collector_payload(u) for u in q.order_by(User.name).all()]})


@admin_bp.route("/collectors", methods=["POST"], strict_slashes=False)
@role_required(["admin"])
def create_collector():
    from ..accounting import create_collector_collection_account
    data = request.get_json() or {}
    staff_id = data.get("staff_id")
    if not staff_id:
        return jsonify({"error": "invalid_collector_setup", "message": "staff_id is required"}), 422
    user = User.query.get(int(staff_id))
    if not user:
        return jsonify({"error": "invalid_staff_id", "message": "Submitted staff_id does not match an existing staff record."}), 422
    if user.role not in ("admin", "staff"):
        return jsonify({"error": "invalid_collector_setup", "message": "Only staff/admin users can be collectors"}), 422
    try:
        status = (data.get("status") or data.get("collector_status") or "ACTIVE").upper()
        if status not in ("ACTIVE", "INACTIVE", "SUSPENDED"):
            raise AccountingError("Invalid collector_status")
        collector_code = data.get("collector_code")
        if collector_code and User.query.filter(User.collector_code == collector_code, User.id != user.id).first():
            raise AccountingError("collector_code already exists")
        if user.is_collector and user.collector_status == "ACTIVE" and status == "ACTIVE":
            raise AccountingError("Selected staff is already an active collector")
        user.is_collector = True
        user.can_collect_cash = bool(data.get("can_collect_cash", True))
        user.collector_status = status
        if collector_code is not None:
            user.collector_code = collector_code
        if data.get("collection_account_id"):
            acct = AccountingAccount.query.get(int(data["collection_account_id"]))
            from ..accounting import validate_collection_account
            validate_collection_account(acct, "CASH_COLLECTOR", user.id)
            user.default_collection_account_id = acct.id
        elif data.get("create_collection_account"):
            create_collector_collection_account(user)
        db.session.commit()
        return jsonify(_collector_payload(user)), 201
    except (AccountingError, IntegrityError) as exc:
        db.session.rollback()
        logger.exception("Collector setup failed")
        message = str(exc.orig) if isinstance(exc, IntegrityError) and getattr(exc, "orig", None) else str(exc)
        return jsonify({"error": "Collector setup incomplete", "message": message}), 422


@admin_bp.route("/collectors/<int:collector_id>/collection-account", methods=["POST"], strict_slashes=False)
@role_required(["admin"])
def create_collector_account(collector_id):
    from ..accounting import create_collector_collection_account
    collector = User.query.get_or_404(collector_id)
    try:
        acct = create_collector_collection_account(collector)
        db.session.commit()
        return jsonify({
            "collector_id": collector.id,
            "collection_account": {"id": acct.id, "code": acct.account_code, "name": acct.account_name},
        }), 201
    except AccountingError as exc:
        db.session.rollback()
        return jsonify({"error": "Collector setup incomplete", "message": str(exc)}), 422


@admin_bp.route("/collectors/staff-options", methods=["GET"], strict_slashes=False)
@role_required(["admin"])
def collector_staff_options():
    users = User.query.filter(User.is_active.is_(True), User.role.in_(("admin", "staff"))).order_by(User.name).all()
    items = []
    for user in users:
        already = bool(user.is_collector and user.collector_status == "ACTIVE")
        items.append({
            "staff_id": user.id,
            "name": user.name,
            "employee_code": getattr(user, "employee_code", None),
            "mobile": getattr(user, "mobile", None),
            "already_collector": already,
        })
    return jsonify({"items": items})


@admin_bp.route("/collectors/<int:collector_id>", methods=["PATCH"])
@role_required(["admin"])
def update_collector(collector_id):
    user = User.query.get_or_404(collector_id)
    data = request.get_json() or {}
    try:
        if "collector_code" in data:
            user.collector_code = data["collector_code"]
        if "can_collect_cash" in data:
            user.can_collect_cash = bool(data["can_collect_cash"])
        if "collector_status" in data or "status" in data:
            status = (data.get("collector_status") or data.get("status") or "").upper()
            if status not in ("ACTIVE", "INACTIVE", "SUSPENDED"):
                raise AccountingError("Invalid collector_status")
            user.collector_status = status
        if data.get("create_collection_account"):
            from ..accounting import create_collector_collection_account
            create_collector_collection_account(user)
        user.is_collector = True
        db.session.commit()
        return jsonify(_collector_payload(user))
    except AccountingError as exc:
        db.session.rollback()
        return jsonify({"error": "Collector setup incomplete", "message": str(exc)}), 422


@admin_bp.route("/collectors/<int:collector_id>/activate", methods=["POST"])
@role_required(["admin"])
def activate_collector(collector_id):
    user = User.query.get_or_404(collector_id)
    user.is_collector = True; user.can_collect_cash = True; user.collector_status = "ACTIVE"
    db.session.commit()
    return jsonify(_collector_payload(user))


@admin_bp.route("/collectors/<int:collector_id>/deactivate", methods=["POST"])
@role_required(["admin"])
def deactivate_collector(collector_id):
    user = User.query.get_or_404(collector_id)
    user.collector_status = "INACTIVE"; user.can_collect_cash = False
    db.session.commit()
    return jsonify(_collector_payload(user))


@admin_bp.route("/collections/collectors/options", methods=["GET"])
@role_required(["admin", "staff"])
def collector_options():
    q = User.query.filter(User.is_collector.is_(True), User.can_collect_cash.is_(True), User.collector_status == "ACTIVE", User.is_active.is_(True))
    items = []
    for user in q.order_by(User.name).all():
        acct = AccountingAccount.query.get(user.default_collection_account_id) if user.default_collection_account_id else None
        if acct and acct.is_active and acct.allow_manual_posting and acct.is_collection_account and account_subtype(acct) == "COLLECTION_CLEARING" and acct.collector_id == user.id:
            items.append({"collector_id": user.id, "collector_name": user.name, "collection_account_id": acct.id, "collection_account_code": acct.account_code, "collection_account_name": acct.account_name})
    return jsonify({"items": items})

@admin_bp.route("/customers/options", methods=["GET"])
@role_required(["admin"])
def customer_options():
    include_inactive = str(request.args.get("include_inactive", "false")).lower() in ("1", "true", "yes", "on")
    query = Customer.query
    if not include_inactive:
        query = query.filter(Customer.status.in_(["Active", "ACTIVE", "active"]))
    if request.args.get("search"):
        s = f"%{request.args['search']}%"
        query = query.filter((Customer.customer_code.ilike(s)) | (Customer.full_name.ilike(s)) | (Customer.nic_number.ilike(s)) | (Customer.mobile.ilike(s)))
    items = []
    for c in query.order_by(Customer.customer_code.asc()).limit(100).all():
        label = f"{c.customer_code} — {c.full_name}"
        items.append({"id": c.id, "customer_number": c.customer_code, "display_name": c.full_name, "nic": c.nic_number, "mobile": c.mobile, "label": label})
    return jsonify({"items": items})

@admin_bp.route("/loans/options", methods=["GET"])
@role_required(["admin"])
def loan_options():
    query = Loan.query.options(joinedload(Loan.customer))
    if request.args.get("customer_id"):
        query = query.filter(Loan.customer_id == int(request.args["customer_id"]))
    if request.args.get("status"):
        status = request.args["status"]
        query = query.filter(Loan.status.in_([status, status.upper(), status.title()]))
    if request.args.get("search"):
        s = f"%{request.args['search']}%"
        query = query.join(Customer).filter((Loan.loan_number.ilike(s)) | (Customer.full_name.ilike(s)) | (Customer.customer_code.ilike(s)))
    items = []
    for l in query.order_by(Loan.loan_number.asc()).limit(100).all():
        customer_name = l.customer.full_name if l.customer else None
        items.append({"id": l.id, "loan_number": l.loan_number, "customer_id": l.customer_id, "customer_name": customer_name, "status": l.status, "principal_amount": float(l.principal_amount or 0), "outstanding_amount": float(l.outstanding), "label": f"{l.loan_number} — {customer_name or ''}"})
    return jsonify({"items": items})
