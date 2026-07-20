from datetime import date, timedelta
from decimal import Decimal

from flask_jwt_extended import create_access_token

from app.extensions import db
from app.models import Customer, Loan, LoanApplication, Payment, User


def _user(role="admin", suffix=""):
    user = User(email=f"{role}{suffix}@example.com", name=f"{role}{suffix}", role=role)
    user.set_password("password")
    db.session.add(user)
    db.session.commit()
    return user


def _headers(app, user):
    with app.app_context():
        token = create_access_token(identity=str(user.id), additional_claims={"role": user.role})
    return {"Authorization": f"Bearer {token}"}


def _customer(suffix="1"):
    user = _user("customer", suffix)
    customer = Customer(
        user_id=user.id,
        customer_code=f"GROW-CUS-{suffix}",
        full_name=f"Customer {suffix}",
    )
    db.session.add(customer)
    db.session.commit()
    return customer


def _loan(admin, customer, status, suffix="1"):
    loan = Loan(
        loan_number=f"GROW-LOAN-{suffix}",
        customer_id=customer.id,
        principal_amount=Decimal("1000.00"),
        interest_rate=Decimal("12.00"),
        total_days=30,
        payment_interval_days=30,
        daily_installment=Decimal("0.00"),
        total_payable=Decimal("1000.00"),
        start_date=date.today(),
        end_date=date.today() + timedelta(days=30),
        status=status,
        created_by_id=admin.id,
    )
    db.session.add(loan)
    db.session.commit()
    return loan


def _dashboard(client, app):
    admin = _user("admin", "metrics")
    response = client.get("/admin/dashboard", headers=_headers(app, admin))
    assert response.status_code == 200
    return response.get_json()


def test_dashboard_reports_zero_loan_metrics_when_none_exist(app, client):
    body = _dashboard(client, app)

    assert body["total_loans"] == 0
    assert body["active_loans"] == 0
    assert body["activeLoans"] == 0
    assert isinstance(body["active_loans"], int)


def test_dashboard_counts_uppercase_active_loan(app, client):
    admin = _user("admin", "active")
    customer = _customer("active")
    _loan(admin, customer, "ACTIVE", "active")

    body = client.get("/admin/dashboard", headers=_headers(app, admin)).get_json()

    assert body["active_loans"] == 1


def test_dashboard_counts_legacy_mixed_case_active_loan(app, client):
    admin = _user("admin", "legacy")
    customer = _customer("legacy")
    _loan(admin, customer, " Active ", "legacy")

    body = client.get("/admin/dashboard", headers=_headers(app, admin)).get_json()

    assert body["active_loans"] == 1


def test_dashboard_excludes_closed_loan(app, client):
    admin = _user("admin", "closed")
    customer = _customer("closed")
    _loan(admin, customer, "CLOSED", "closed")

    body = client.get("/admin/dashboard", headers=_headers(app, admin)).get_json()

    assert body["active_loans"] == 0


def test_dashboard_counts_all_loans_regardless_of_status_and_excludes_applications(app, client):
    admin = _user("admin", "total-loans")
    customer = _customer("total-loans")

    statuses = ["ACTIVE"] * 10 + ["SETTLED"] * 5 + ["OVERDUE"] * 2
    for index, status in enumerate(statuses, start=1):
        _loan(admin, customer, status, f"total-loans-{index}")

    db.session.add(
        LoanApplication(
            application_number="APP-TOTAL-LOANS",
            customer_id=customer.id,
            loan_type="GROW",
            status="APPROVED",
            applied_amount=Decimal("1000.00"),
            tenure_months=1,
            full_name=customer.full_name,
            nic_number="123456789V",
            mobile_number="0700000000",
        )
    )
    db.session.commit()

    body = client.get("/admin/dashboard", headers=_headers(app, admin)).get_json()

    assert body["total_loans"] == 17
    assert body["active_loans"] == 10


def test_dashboard_count_metrics_are_always_numeric(app, client):
    body = _dashboard(client, app)

    for field in ("total_customers", "total_loans", "active_loans", "payments_today"):
        assert field in body
        assert isinstance(body[field], int)


def test_dashboard_reports_zero_payments_today_when_none_exist(app, client):
    body = _dashboard(client, app)

    assert body["payments_today"] == 0
    assert body["paymentsToday"] == 0


def test_dashboard_counts_only_posted_non_reversed_payments_today(app, client):
    admin = _user("admin", "payments")
    customer = _customer("payments")
    loan = _loan(admin, customer, "ACTIVE", "payments")
    db.session.add_all([
        Payment(loan_id=loan.id, amount_collected=Decimal("10.00"), collection_date=date.today(), collected_by_id=admin.id, status="POSTED"),
        Payment(loan_id=loan.id, amount_collected=Decimal("10.00"), collection_date=date.today(), collected_by_id=admin.id, status="DRAFT"),
        Payment(loan_id=loan.id, amount_collected=Decimal("10.00"), collection_date=date.today() - timedelta(days=1), collected_by_id=admin.id, status="POSTED"),
        Payment(loan_id=loan.id, amount_collected=Decimal("10.00"), collection_date=date.today(), collected_by_id=admin.id, status="POSTED", reversed_at=date.today()),
    ])
    db.session.commit()

    body = client.get("/admin/dashboard", headers=_headers(app, admin)).get_json()

    assert body["payments_today"] == 1
