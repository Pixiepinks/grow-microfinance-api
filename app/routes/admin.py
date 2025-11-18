from datetime import date
from decimal import Decimal
from flask import Blueprint, request, jsonify
from flask_jwt_extended import get_jwt_identity

from ..extensions import db
from ..models import User, Customer, Loan, Payment
from .utils import role_required

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


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

    user = User(email=user_data["email"], name=user_data.get("name", ""), role="customer")
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
        start_date=start_date,
        end_date=end_date,
        total_payable=total_payable,
        daily_installment=daily_installment,
        created_by_id=get_jwt_identity(),
    )
    db.session.add(loan)
    db.session.commit()

    return jsonify({"message": "Loan created", "loan_id": loan.id})


@admin_bp.route("/loans", methods=["GET"])
@role_required(["admin"])
def list_loans():
    status = request.args.get("status")
    customer_id = request.args.get("customer_id")
    query = Loan.query
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
            "status": l.status,
            "principal_amount": float(l.principal_amount),
            "total_payable": float(l.total_payable),
            "total_paid": float(l.total_paid),
            "outstanding": float(l.outstanding),
        }
        for l in loans
    ]
    return jsonify(results)


@admin_bp.route("/dashboard", methods=["GET"])
@role_required(["admin"])
def dashboard():
    total_customers = Customer.query.count()
    active_loans = Loan.query.filter_by(status="Active").all()
    total_active_loans = len(active_loans)
    total_outstanding = sum((loan.outstanding for loan in active_loans), Decimal("0"))

    today = date.today()
    todays_payments = Payment.query.filter(Payment.collection_date == today).all()
    todays_collection = sum((p.amount_collected for p in todays_payments), Decimal("0"))

    return jsonify(
        {
            "total_customers": total_customers,
            "total_active_loans": total_active_loans,
            "total_outstanding": float(total_outstanding),
            "todays_collection": float(todays_collection),
        }
    )
