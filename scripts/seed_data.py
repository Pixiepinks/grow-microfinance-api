from datetime import date, timedelta
from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app import create_app
from app.extensions import db
from app.models import Customer, Loan, Payment, User


def ensure_user(email: str, password: str, name: str, role: str) -> User:
    """Create or update a user with the provided credentials."""

    user = User.query.filter_by(email=email).first()
    if user is None:
        user = User(email=email)
        db.session.add(user)

    user.name = name
    user.role = role
    user.set_password(password)
    return user


app = create_app()

with app.app_context():
    admin = ensure_user(
        email="admin@grow.com",
        password="admin123",
        name="Administrator",
        role="admin",
    )

    staff = ensure_user(
        email="staff@grow.com",
        password="staff123",
        name="Staff User",
        role="staff",
    )

    customer_user = ensure_user(
        email="customer@grow.com",
        password="cust123",
        name="Sunil Perera",
        role="customer",
    )

    customer = Customer.query.filter_by(customer_code="CUST001").first()
    if customer is None:
        customer = Customer(customer_code="CUST001", user=customer_user)
        db.session.add(customer)
    else:
        customer.user = customer_user

    customer.full_name = "Sunil Perera"
    customer.nic_number = "901234567V"
    customer.mobile = "0771234567"
    customer.address = "123 Market Street"
    customer.business_type = "Grocery"
    customer.status = "Active"

    if customer.user is None:
        customer.user = customer_user

    loan = Loan.query.filter_by(loan_number="LN001").first()
    default_start_date = date.today() - timedelta(days=5)
    if loan is None:
        loan = Loan(
            loan_number="LN001",
            customer=customer,
            principal_amount=50000,
            interest_rate=5,
            total_days=30,
            daily_installment=1750,
            total_payable=52500,
            start_date=default_start_date,
            end_date=default_start_date + timedelta(days=29),
            status="Active",
            created_by=admin,
        )
        db.session.add(loan)
    else:
        loan.customer = customer
        loan.created_by = admin
        loan.principal_amount = 50000
        loan.interest_rate = 5
        loan.total_days = 30
        loan.daily_installment = 1750
        loan.total_payable = 52500
        if loan.start_date is None:
            loan.start_date = default_start_date
        if loan.end_date is None:
            loan.end_date = loan.start_date + timedelta(days=loan.total_days - 1)
        loan.status = "Active"

    db.session.flush()

    payment = Payment.query.filter_by(
        loan_id=loan.id, collection_date=date.today()
    ).first()
    if payment is None:
        payment = Payment(
            loan=loan,
            amount_collected=1750,
            collection_date=date.today(),
            collected_by=staff,
            payment_method="Cash",
            remarks="Initial payment",
        )
        db.session.add(payment)

    db.session.commit()
    print("Seed data ensured. Admin login: admin@grow.com / admin123")
