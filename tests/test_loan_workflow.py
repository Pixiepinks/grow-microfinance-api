from datetime import date
from decimal import Decimal

from flask_jwt_extended import create_access_token

from app.extensions import db
from app.models import Customer, Loan, LoanApplication, User
from app.routes.loan_applications import (
    STATUS_APPROVED,
    STATUS_REJECTED,
    STATUS_STAFF_APPROVED,
    STATUS_SUBMITTED,
)


def _create_user(role: str, name: str, email: str) -> User:
    user = User(email=email, name=name, role=role)
    user.set_password("password")
    db.session.add(user)
    db.session.commit()
    return user


def _customer_profile(user: User, code: str = "CUST-001") -> Customer:
    customer = Customer(
        user_id=user.id,
        customer_code=code,
        full_name=user.name,
        nic_number="123456789V",
        mobile="0700000000",
        address="123 Street",
        business_type="Retail",
    )
    db.session.add(customer)
    db.session.commit()
    return customer


def _auth_headers(app, user: User):
    with app.app_context():
        token = create_access_token(identity=str(user.id), additional_claims={"role": user.role})
    return {"Authorization": f"Bearer {token}"}


def _sample_application_payload():
    return {
        "loan_type": "GROW_BUSINESS",
        "full_name": "Jane Customer",
        "nic_number": "123456789V",
        "mobile_number": "0700000000",
        "applied_amount": "10000",
        "tenure_months": 6,
        "monthly_income": "50000",
        "monthly_expenses": "10000",
        "business_name": "Jane's Shop",
        "business_address": "123 Street",
        "monthly_sales": "60000",
        "business_type": "Retail",
    }


def test_customer_submission_sets_submitted_status(app, client):
    customer_user = _create_user("customer", "Customer One", "customer1@example.com")
    _customer_profile(customer_user)

    response = client.post(
        "/loan-applications",
        json=_sample_application_payload(),
        headers=_auth_headers(app, customer_user),
    )

    assert response.status_code == 201
    body = response.get_json()
    assert body["status"] == STATUS_SUBMITTED
    assert body["submitted_at"] is not None


def test_staff_listing_and_approval_flow(app, client):
    staff_user = _create_user("staff", "Staff One", "staff1@example.com")
    customer_user = _create_user("customer", "Customer Two", "customer2@example.com")
    customer = _customer_profile(customer_user, code="CUST-002")

    application = LoanApplication(
        application_number="APP-001",
        customer_id=customer.id,
        loan_type="GROW_BUSINESS",
        status=STATUS_SUBMITTED,
        applied_amount=Decimal("10000"),
        tenure_months=6,
        full_name="Customer Two",
        nic_number="123456789V",
        mobile_number="0700000000",
        monthly_income=Decimal("50000"),
        monthly_expenses=Decimal("10000"),
    )
    db.session.add(application)
    db.session.commit()

    list_response = client.get("/loan-applications", headers=_auth_headers(app, staff_user))
    assert list_response.status_code == 200
    assert len(list_response.get_json()) == 1
    assert list_response.get_json()[0]["status"] == STATUS_SUBMITTED

    approve_response = client.post(
        f"/loan-applications/{application.id}/approve",
        headers=_auth_headers(app, staff_user),
        json={"review_notes": "Looks good"},
    )
    assert approve_response.status_code == 200
    assert approve_response.get_json()["status"] == STATUS_STAFF_APPROVED


def test_staff_awaiting_review_endpoint(app, client):
    staff_user = _create_user("staff", "Staff Awaiting", "staff-await@example.com")
    customer_user = _create_user("customer", "Customer Awaiting", "cust-await@example.com")
    customer = _customer_profile(customer_user, code="CUST-010")

    submitted_app = LoanApplication(
        application_number="APP-AWAIT",
        customer_id=customer.id,
        loan_type="GROW_BUSINESS",
        status=STATUS_SUBMITTED,
        applied_amount=Decimal("5000"),
        tenure_months=3,
        full_name="Customer Awaiting",
        nic_number="123456789V",
        mobile_number="0700000000",
        monthly_income=Decimal("30000"),
        monthly_expenses=Decimal("5000"),
    )
    db.session.add(submitted_app)
    db.session.commit()

    response = client.get(
        "/loan-applications/awaiting-review",
        headers=_auth_headers(app, staff_user),
    )

    assert response.status_code == 200
    body = response.get_json()
    assert len(body) == 1
    assert body[0]["status"] == STATUS_SUBMITTED
    assert body[0]["customer_name"] == "Customer Awaiting"


def test_non_staff_cannot_list_awaiting_review(app, client):
    customer_user = _create_user("customer", "Customer Blocked", "cust-block@example.com")

    response = client.get(
        "/loan-applications/awaiting-review",
        headers=_auth_headers(app, customer_user),
    )

    assert response.status_code == 403


def test_admin_listing_and_decision_flow(app, client):
    admin_user = _create_user("admin", "Admin One", "admin1@example.com")
    customer_user = _create_user("customer", "Customer Three", "customer3@example.com")
    customer = _customer_profile(customer_user, code="CUST-003")

    ready_application = LoanApplication(
        application_number="APP-READY",
        customer_id=customer.id,
        loan_type="GROW_BUSINESS",
        status=STATUS_STAFF_APPROVED,
        applied_amount=Decimal("15000"),
        tenure_months=12,
        approved_tenure=12,
        approved_amount=Decimal("12000"),
        full_name="Customer Three",
        nic_number="123456789V",
        mobile_number="0700000000",
        monthly_income=Decimal("60000"),
        monthly_expenses=Decimal("15000"),
    )
    db.session.add(ready_application)

    review_application = LoanApplication(
        application_number="APP-REJECT",
        customer_id=customer.id,
        loan_type="GROW_BUSINESS",
        status=STATUS_STAFF_APPROVED,
        applied_amount=Decimal("8000"),
        tenure_months=4,
        full_name="Customer Three",
        nic_number="123456789V",
        mobile_number="0700000000",
        monthly_income=Decimal("40000"),
        monthly_expenses=Decimal("8000"),
    )
    db.session.add(review_application)
    db.session.commit()

    list_response = client.get("/loan-applications", headers=_auth_headers(app, admin_user))
    results = list_response.get_json()
    assert list_response.status_code == 200
    assert all(item["status"] == STATUS_STAFF_APPROVED for item in results)

    approve_response = client.post(
        f"/loan-applications/{ready_application.id}/approve",
        headers=_auth_headers(app, admin_user),
        json={"approved_amount": "12000", "approved_tenure": 12},
    )
    assert approve_response.status_code == 200
    assert approve_response.get_json()["status"] == STATUS_APPROVED

    reject_response = client.post(
        f"/loan-applications/{review_application.id}/reject",
        headers=_auth_headers(app, admin_user),
        json={"reject_reason": "Insufficient documents"},
    )
    assert reject_response.status_code == 200
    assert reject_response.get_json()["status"] == STATUS_REJECTED


def test_staff_can_record_payment_for_active_loan(app, client):
    staff_user = _create_user("staff", "Collector", "collector@example.com")
    customer_user = _create_user("customer", "Customer Four", "customer4@example.com")
    customer = _customer_profile(customer_user, code="CUST-004")

    loan = Loan(
        loan_number="LN-001",
        customer_id=customer.id,
        principal_amount=Decimal("10000"),
        interest_rate=Decimal("12.5"),
        total_days=100,
        daily_installment=Decimal("150"),
        total_payable=Decimal("15000"),
        start_date=date.today(),
        end_date=date.today(),
        status="Active",
        created_by_id=staff_user.id,
    )
    db.session.add(loan)
    db.session.commit()

    response = client.post(
        "/staff/payments",
        json={
            "loan_id": loan.id,
            "amount_collected": "500",
            "collection_date": date.today().isoformat(),
            "payment_method": "Cash",
        },
        headers=_auth_headers(app, staff_user),
    )

    assert response.status_code == 200
    assert response.get_json()["message"] == "Payment recorded"
    assert Loan.query.get(loan.id).payments[0].amount_collected == Decimal("500")
