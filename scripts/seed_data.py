from datetime import date, timedelta
from app import create_app
from app.extensions import db
from app.models import User, Customer, Loan, Payment

app = create_app()

with app.app_context():
    if User.query.filter_by(email="admin@grow.com").first() is None:
        admin = User(email="admin@grow.com", name="Administrator", role="admin")
        admin.set_password("admin123")
        db.session.add(admin)
    else:
        admin = User.query.filter_by(email="admin@grow.com").first()

    staff = User(email="staff@grow.com", name="Field Staff", role="staff")
    staff.set_password("staff123")
    customer_user = User(email="customer@grow.com", name="Test Customer", role="customer")
    customer_user.set_password("cust123")

    customer = Customer(
        user=customer_user,
        customer_code="CUST001",
        full_name="Sunil Perera",
        nic_number="901234567V",
        mobile="0771234567",
        address="123 Market Street",
        business_type="Grocery",
    )

    loan = Loan(
        loan_number="LN001",
        customer=customer,
        principal_amount=50000,
        interest_rate=5,
        total_days=30,
        daily_installment=1750,
        total_payable=52500,
        start_date=date.today() - timedelta(days=5),
        end_date=date.today() + timedelta(days=25),
        status="Active",
        created_by=admin,
    )

    payment = Payment(
        loan=loan,
        amount_collected=1750,
        collection_date=date.today(),
        collected_by=staff,
        payment_method="Cash",
        remarks="On time",
    )

    db.session.add_all([staff, customer_user, customer, loan, payment])
    db.session.commit()
    print("Seed data created. Admin login: admin@grow.com / admin123")
