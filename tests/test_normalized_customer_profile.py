from datetime import date
from decimal import Decimal

from flask_jwt_extended import create_access_token

from app.extensions import db
from app.models import Customer, Loan, User


CANONICAL_PATH = "/admin/customers/<int:customer_id>/profile-normalized"


def _user(role, email):
    user = User(email=email, name=role.title(), role=role)
    user.set_password("password")
    db.session.add(user)
    db.session.commit()
    return user


def _headers(app, user):
    with app.app_context():
        token = create_access_token(
            identity=str(user.id), additional_claims={"role": user.role}
        )
    return {"Authorization": f"Bearer {token}"}


def _customer(number=19, customer_id=None):
    user = _user("customer", f"customer-{number}@example.test")
    customer = Customer(
        id=customer_id,
        user_id=user.id,
        customer_code=f"CUST-{number:05d}",
        full_name="Baskaran Parthiban",
        nic_number="941334539V",
        mobile="0766285870",
        permanent_address_line1="1 Main Street",
        current_address_line1="2 Current Street",
        current_city="Colombo",
    )
    db.session.add(customer)
    db.session.commit()
    return customer


def _active_loan(customer, admin):
    loan = Loan(
        loan_number="GROW-LOAN-PROFILE-001",
        customer_id=customer.id,
        principal_amount=Decimal("1000.00"),
        interest_rate=Decimal("12.00"),
        total_days=30,
        payment_interval_days=7,
        daily_installment=Decimal("0.00"),
        total_payable=Decimal("1100.00"),
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 31),
        status="ACTIVE",
        created_by_id=admin.id,
    )
    db.session.add(loan)
    db.session.commit()
    return loan


def test_normalized_profile_is_registered_at_the_canonical_admin_path(app):
    rules = {rule.rule: rule.methods for rule in app.url_map.iter_rules()}

    assert CANONICAL_PATH in rules
    assert "GET" in rules[CANONICAL_PATH]
    assert "/api/admin/customers/<int:customer_id>/profile-normalized" not in rules


def test_normalized_profile_exact_admin_request_returns_consolidated_profile(app, client):
    admin = _user("admin", "admin-profile@example.test")
    customer = _customer(customer_id=19)
    loan = _active_loan(customer, admin)

    response = client.get(
        "/admin/customers/19/profile-normalized",
        headers=_headers(app, admin),
    )

    assert response.status_code == 200
    profile = response.get_json()["profile"]
    assert profile["customer_id"] == 19
    assert profile["full_name"] == "Baskaran Parthiban"
    assert profile["current_address_line1"] == "2 Current Street"
    assert profile["permanent_address_line1"] == "1 Main Street"
    assert profile["current_address_line2"] is None
    assert profile["monthly_income"] is None
    assert profile["has_existing_loans"] is True
    assert profile["existing_loans"] == [
        {"loan_id": loan.id, "loan_number": loan.loan_number, "status": "ACTIVE"}
    ]


def test_normalized_profile_missing_customer_returns_structured_404(app, client):
    admin = _user("admin", "admin-missing-profile@example.test")

    response = client.get(
        "/admin/customers/99999/profile-normalized", headers=_headers(app, admin)
    )

    assert response.status_code == 404
    assert response.get_json() == {
        "error": "customer_not_found",
        "message": "Customer not found.",
    }


def test_normalized_profile_requires_an_admin_token(app, client):
    customer = _customer()
    staff = _user("staff", "staff-profile@example.test")
    path = f"/admin/customers/{customer.id}/profile-normalized"

    assert client.get(path).status_code == 401
    assert client.get(path, headers=_headers(app, staff)).status_code == 403
