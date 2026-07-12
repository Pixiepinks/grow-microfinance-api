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
    AccountingAccount,
    AccountingJournalLine,
    CollectionDepositBatch,
)
from ..accounting import post_loan_disbursement, AccountingError, accrue_due_loan_interest, reverse_payment, reverse_loan_disbursement, money as acct_money, preview_collection_deposit, create_collection_deposit, reverse_collection_deposit, collector_cash_position, account_subtype
from ..loan_ledger import (
    daily_interest_rate,
    generate_loan_ledger,
    ledger_totals,
    loan_config_summary,
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
    config = loan_config_summary(loan)
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
        "status": loan.status,
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
        "delay_days": entry.delay_days or 0,
        "delay_interest": float(entry.delay_interest or 0),
        "delay_interest_formatted": format_currency(entry.delay_interest or 0),
        "status": entry.status,
        "interest_accrued": bool(entry.interest_accrued),
        "interest_accrued_at": entry.interest_accrued_at.isoformat() if entry.interest_accrued_at else None,
        "interest_accrual_journal_id": entry.interest_accrual_journal_id,
        "principal_paid": float(entry.principal_paid or 0),
        "interest_paid": float(entry.interest_paid or 0),
        "delay_interest_paid": float(entry.delay_interest_paid or 0),
        "delay_interest_accrued": float(entry.delay_interest_accrued or 0),
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


@admin_bp.route("/collections/undeposited", methods=["GET"])
@role_required(["admin"])
def undeposited_collections():
    q = Payment.query.options(joinedload(Payment.loan).joinedload(Loan.customer)).filter(Payment.reversed_at.is_(None), Payment.deposit_status.in_(["UNDEPOSITED", "PARTIALLY_DEPOSITED"]))
    if request.args.get("collector_id"): q = q.filter(Payment.collector_id == int(request.args["collector_id"]))
    if request.args.get("account_id"): q = q.filter(Payment.collection_account_id == int(request.args["account_id"]))
    if request.args.get("loan_id"): q = q.filter(Payment.loan_id == int(request.args["loan_id"]))
    if request.args.get("customer_id"): q = q.join(Loan).filter(Loan.customer_id == int(request.args["customer_id"]))
    if request.args.get("date_from"): q = q.filter(Payment.collection_date >= date.fromisoformat(request.args["date_from"]))
    if request.args.get("date_to"): q = q.filter(Payment.collection_date <= date.fromisoformat(request.args["date_to"]))
    if request.args.get("deposit_status"): q = q.filter(Payment.deposit_status == request.args["deposit_status"])
    return jsonify({"items": [_payment_summary(p) for p in q.order_by(Payment.collection_date, Payment.id).all()]})


@admin_bp.route("/collection-deposits/preview", methods=["POST"])
@role_required(["admin"])
def preview_deposit():
    try:
        result = preview_collection_deposit(request.get_json() or {})
        return jsonify({"total_amount": f"{result['total_amount']:.2f}", "journal_preview": result["journal_preview"], "validation_errors": result["validation_errors"]})
    except AccountingError as exc:
        return jsonify({"message": str(exc)}), 422


def _deposit_dict(b):
    return {"id": b.id, "deposit_number": b.deposit_number, "collector_id": b.collector_id, "collector": b.collector.name if b.collector else None, "collector_account_id": b.collector_account_id, "collector_account": b.collector_account.account_name if b.collector_account else None, "bank_account_id": b.bank_account_id, "bank_account": b.bank_account.account_name if b.bank_account else None, "deposit_date": b.deposit_date.isoformat(), "accounting_date": b.accounting_date.isoformat(), "total_amount": f"{acct_money(b.total_amount):.2f}", "bank_reference": b.bank_reference, "deposit_slip_reference": b.deposit_slip_reference, "remarks": b.remarks, "journal_entry_id": b.journal_entry_id, "status": b.status, "allocations": [{"payment_id": a.payment_id, "allocated_amount": f"{acct_money(a.allocated_amount):.2f}"} for a in b.allocations]}


@admin_bp.route("/collection-deposits", methods=["POST"])
@role_required(["admin"])
def create_deposit():
    try:
        batch = create_collection_deposit(request.get_json() or {}, int(get_jwt_identity()))
        db.session.commit()
        return jsonify(_deposit_dict(batch)), 201
    except AccountingError as exc:
        db.session.rollback()
        return jsonify(exc.args[0] if exc.args and isinstance(exc.args[0], dict) else {"message": str(exc)}), 422


@admin_bp.route("/collection-deposits", methods=["GET"])
@role_required(["admin"])
def list_deposits():
    q = CollectionDepositBatch.query
    if request.args.get("collector_id"): q = q.filter_by(collector_id=int(request.args["collector_id"]))
    if request.args.get("status"): q = q.filter_by(status=request.args["status"])
    if request.args.get("date_from"): q = q.filter(CollectionDepositBatch.deposit_date >= date.fromisoformat(request.args["date_from"]))
    if request.args.get("date_to"): q = q.filter(CollectionDepositBatch.deposit_date <= date.fromisoformat(request.args["date_to"]))
    return jsonify({"items": [_deposit_dict(b) for b in q.order_by(CollectionDepositBatch.deposit_date.desc(), CollectionDepositBatch.id.desc()).all()]})


@admin_bp.route("/collection-deposits/<int:deposit_id>", methods=["GET"])
@role_required(["admin"])
def get_deposit(deposit_id):
    return jsonify(_deposit_dict(CollectionDepositBatch.query.get_or_404(deposit_id)))


@admin_bp.route("/collection-deposits/<int:deposit_id>/reverse", methods=["POST"])
@role_required(["admin"])
def reverse_deposit(deposit_id):
    data = request.get_json() or {}
    try:
        rev = reverse_collection_deposit(CollectionDepositBatch.query.get_or_404(deposit_id), date.fromisoformat(data.get("reversal_date") or date.today().isoformat()), data.get("reason"), int(get_jwt_identity()))
        db.session.commit()
        return jsonify({"reversal_journal_id": rev.id})
    except AccountingError as exc:
        db.session.rollback(); return jsonify({"message": str(exc)}), 422


@admin_bp.route("/collectors/<int:collector_id>/cash-position", methods=["GET"])
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
        "name": user.name,
        "employee_code": getattr(user, "employee_code", None),
        "collector_code": user.collector_code,
        "status": user.collector_status,
        "is_collector": user.is_collector,
        "can_collect_cash": user.can_collect_cash,
        "default_collection_account": _collector_account_payload(account),
    }


@admin_bp.route("/collectors", methods=["GET"])
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


@admin_bp.route("/collectors", methods=["POST"])
@role_required(["admin"])
def create_collector():
    from ..accounting import create_collector_collection_account
    data = request.get_json() or {}
    staff_id = data.get("staff_id")
    if not staff_id:
        return jsonify({"message": "staff_id is required"}), 400
    user = User.query.get_or_404(int(staff_id))
    if user.role not in ("admin", "staff"):
        return jsonify({"message": "Only staff/admin users can be collectors"}), 422
    try:
        user.is_collector = True
        user.can_collect_cash = bool(data.get("can_collect_cash", True))
        user.collector_status = (data.get("collector_status") or "ACTIVE").upper()
        if user.collector_status not in ("ACTIVE", "INACTIVE", "SUSPENDED"):
            raise AccountingError("Invalid collector_status")
        if data.get("collector_code") is not None:
            user.collector_code = data.get("collector_code")
        if data.get("collection_account_id"):
            acct = AccountingAccount.query.get(int(data["collection_account_id"]))
            from ..accounting import validate_collection_account
            validate_collection_account(acct, "CASH_COLLECTOR", user.id)
            user.default_collection_account_id = acct.id
        elif data.get("create_collection_account"):
            create_collector_collection_account(user)
        db.session.commit()
        return jsonify(_collector_payload(user)), 201
    except AccountingError as exc:
        db.session.rollback()
        return jsonify({"error": "Collector setup incomplete", "message": str(exc)}), 422


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
