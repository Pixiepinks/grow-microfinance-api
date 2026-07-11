from datetime import date
from decimal import Decimal
from flask import Blueprint, request, jsonify
from flask_jwt_extended import get_jwt_identity
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
)
from ..accounting import post_loan_disbursement, AccountingError
from ..loan_ledger import (
    daily_interest_rate,
    generate_loan_ledger,
    ledger_totals,
    money,
)
from .utils import role_required

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


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
    status = request.args.get("status")
    customer_id = request.args.get("customer_id")
    query = Loan.query.options(joinedload(Loan.customer))
    if status:
        query = query.filter_by(status=status)
    if customer_id:
        query = query.filter_by(customer_id=customer_id)
    loans = query.all()
    results = [
        {
            "id": l.id,
            "loan_number": l.loan_number,
            "customer_id": l.customer_id,
            "customer": _loan_customer_to_dict(l.customer),
            "status": l.status,
            "currency": CURRENCY_CODE,
            "principal_amount": float(l.principal_amount),
            "principal_amount_formatted": format_currency(l.principal_amount),
            "total_payable": float(l.total_payable),
            "total_payable_formatted": format_currency(l.total_payable),
            "total_paid": float(l.total_paid),
            "total_paid_formatted": format_currency(l.total_paid),
            "outstanding": float(l.outstanding),
            "outstanding_formatted": format_currency(l.outstanding),
        }
        for l in loans
    ]
    return jsonify(results)


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
    return jsonify(_loan_to_dict(loan))


def _loan_to_dict(loan: Loan) -> dict:
    return {
        "id": loan.id,
        "loan_number": loan.loan_number,
        "customer_id": loan.customer_id,
        "customer": _loan_customer_to_dict(loan.customer),
        "currency": CURRENCY_CODE,
        "principal_amount": float(loan.principal_amount),
        "principal_amount_formatted": format_currency(loan.principal_amount),
        "interest_rate": float(loan.interest_rate),
        "total_days": loan.total_days,
        "payment_interval_days": loan.payment_interval_days,
        "start_date": loan.start_date.isoformat() if loan.start_date else None,
        "end_date": loan.end_date.isoformat() if loan.end_date else None,
        "loan_days": loan.loan_days,
        "repayment_frequency": loan.repayment_frequency,
        "number_of_installments": loan.number_of_installments,
        "installment_amount": (
            float(loan.installment_amount)
            if loan.installment_amount is not None
            else None
        ),
        "total_repayment": (
            float(loan.total_repayment) if loan.total_repayment is not None else None
        ),
        "total_interest": (
            float(loan.total_interest) if loan.total_interest is not None else None
        ),
        "interest_type": loan.interest_type,
        "maturity_date": loan.maturity_date.isoformat() if loan.maturity_date else None,
        "final_installment_due_date": (
            loan.final_installment_due_date.isoformat()
            if loan.final_installment_due_date
            else None
        ),
        "status": loan.status,
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
        "delay_days": entry.delay_days or 0,
        "delay_interest": float(entry.delay_interest or 0),
        "delay_interest_formatted": format_currency(entry.delay_interest or 0),
        "status": entry.status,
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
            "ledger": [_ledger_to_dict(entry) for entry in loan.ledger_entries],
            "totals": ledger_totals(loan),
        }
    )


@admin_bp.route("/loans/<int:loan_id>/ledger/<int:entry_id>/payment", methods=["POST"])
@role_required(["admin"])
def record_ledger_payment(loan_id, entry_id):
    loan = Loan.query.get_or_404(loan_id)
    entry = LoanLedger.query.filter_by(id=entry_id, loan_id=loan.id).first_or_404()
    data = request.get_json() or {}
    paid_amount = money(Decimal(str(data.get("paid_amount", "0"))))
    paid_date = date.fromisoformat(data.get("paid_date"))

    entry.paid_amount = paid_amount
    entry.paid_date = paid_date
    entry.delay_days = max((paid_date - entry.due_date).days, 0)
    entry.delay_interest = money(
        Decimal(entry.opening_balance)
        * daily_interest_rate(loan)
        * Decimal(entry.delay_days)
    )
    payable = money(
        Decimal(entry.installment_amount) + Decimal(entry.delay_interest or 0)
    )
    if paid_amount >= payable:
        entry.status = "PAID"
    elif paid_amount > 0:
        entry.status = "PARTIAL"
    elif entry.delay_days > 0:
        entry.status = "OVERDUE"
    else:
        entry.status = "PENDING"

    db.session.commit()
    return jsonify(
        {
            "ledger": _ledger_to_dict(entry),
            "totals": ledger_totals(loan),
        }
    )


@admin_bp.route("/dashboard", methods=["GET"])
@role_required(["admin"])
def dashboard():
    total_customers = Customer.query.count()
    active_loans = Loan.query.filter(Loan.status.in_(["Active", "ACTIVE"])).all()
    total_active_loans = len(active_loans)
    total_outstanding = sum((loan.outstanding for loan in active_loans), Decimal("0"))

    today = date.today()
    todays_payments = Payment.query.filter(Payment.collection_date == today).all()
    todays_collection = sum((p.amount_collected for p in todays_payments), Decimal("0"))

    return jsonify(
        {
            "total_customers": total_customers,
            "total_active_loans": total_active_loans,
            "currency": CURRENCY_CODE,
            "total_outstanding": float(total_outstanding),
            "total_outstanding_formatted": format_currency(total_outstanding),
            "todays_collection": float(todays_collection),
            "todays_collection_formatted": format_currency(todays_collection),
        }
    )


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
