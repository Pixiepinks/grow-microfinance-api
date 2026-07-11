from datetime import date
from decimal import Decimal
from io import BytesIO

from flask_jwt_extended import create_access_token

from app.extensions import db
from app.models import Customer, Loan, LoanApplication, LoanApplicationDocument, User
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
        kyc_status="APPROVED",
        eligibility_status="ELIGIBLE",
    )
    db.session.add(customer)
    db.session.commit()
    return customer


def _auth_headers(app, user: User):
    with app.app_context():
        token = create_access_token(
            identity=str(user.id), additional_claims={"role": user.role}
        )
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


def test_staff_can_submit_draft_application_without_documents(app, client):
    staff_user = _create_user("staff", "Staff Submit", "staff-submit@example.com")
    customer_user = _create_user(
        "customer", "Submit Customer", "submit-customer@example.com"
    )
    customer = _customer_profile(customer_user, code="CUST-SUBMIT")
    application = LoanApplication(
        application_number="APP-SUBMIT-NO-DOCS",
        customer_id=customer.id,
        loan_type="GROW_PERSONAL",
        status="DRAFT",
        applied_amount=Decimal("7000"),
        tenure_months=5,
        full_name="Submit Customer",
        nic_number="123456789V",
        mobile_number="0700000000",
        monthly_income=Decimal("45000"),
        monthly_expenses=Decimal("12000"),
        extra_data={
            "employment_type": "salaried",
            "net_monthly_salary": "45000",
            "employer_name": "Acme Ltd",
        },
    )
    db.session.add(application)
    db.session.commit()

    response = client.post(
        f"/loan-applications/{application.id}/submit",
        headers=_auth_headers(app, staff_user),
        json={},
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == STATUS_SUBMITTED
    assert body["submitted_at"] is not None
    assert body["documents"] == []

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

    list_response = client.get(
        "/loan-applications", headers=_auth_headers(app, staff_user)
    )
    assert list_response.status_code == 200
    assert len(list_response.get_json()) == 1
    assert list_response.get_json()[0]["status"] == STATUS_SUBMITTED

    approve_response = client.post(
        f"/loan-applications/{application.id}/approve",
        headers=_auth_headers(app, staff_user),
        json={
            "review_notes": "Looks good",
            "approved_amount": "10000",
            "approved_tenure": 6,
        },
    )
    assert approve_response.status_code == 200
    assert approve_response.get_json()["status"] == STATUS_STAFF_APPROVED


def test_staff_awaiting_review_endpoint(app, client):
    staff_user = _create_user("staff", "Staff Awaiting", "staff-await@example.com")
    customer_user = _create_user(
        "customer", "Customer Awaiting", "cust-await@example.com"
    )
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
    customer_user = _create_user(
        "customer", "Customer Blocked", "cust-block@example.com"
    )

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

    list_response = client.get(
        "/loan-applications", headers=_auth_headers(app, admin_user)
    )
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


def test_staff_active_loans_endpoint(app, client):
    staff_user = _create_user("staff", "Active Staff", "active-staff@example.com")
    customer_user = _create_user(
        "customer", "Active Customer", "active-cust@example.com"
    )
    customer = _customer_profile(customer_user, code="CUST-020")

    loan = Loan(
        loan_number="LN-ACTIVE-1",
        customer_id=customer.id,
        principal_amount=Decimal("7500"),
        interest_rate=Decimal("10"),
        total_days=60,
        daily_installment=Decimal("150"),
        total_payable=Decimal("9000"),
        start_date=date.today(),
        end_date=date.today(),
        status="Active",
        created_by_id=staff_user.id,
    )
    db.session.add(loan)
    db.session.commit()

    response = client.get("/staff/active-loans", headers=_auth_headers(app, staff_user))

    assert response.status_code == 200
    body = response.get_json()
    assert len(body) == 1
    assert body[0]["loan_id"] == loan.id
    assert body[0]["customer_name"] == customer.full_name


def test_staff_loan_applications_endpoint(app, client):
    staff_user = _create_user("staff", "Staff Reviewer", "staff-review@example.com")
    customer_user = _create_user("customer", "Applicant", "applicant@example.com")
    customer = _customer_profile(customer_user, code="CUST-021")

    submitted = LoanApplication(
        application_number="APP-SUB-1",
        customer_id=customer.id,
        loan_type="GROW_BUSINESS",
        status=STATUS_SUBMITTED,
        applied_amount=Decimal("12000"),
        tenure_months=12,
        full_name="Applicant",
        nic_number="123456789V",
        mobile_number="0700000000",
    )
    db.session.add(submitted)
    db.session.commit()

    response = client.get(
        "/staff/loan-applications?status=SUBMITTED",
        headers=_auth_headers(app, staff_user),
    )

    assert response.status_code == 200
    body = response.get_json()
    assert len(body) == 1
    assert body[0]["id"] == submitted.id
    assert body[0]["status"] == STATUS_SUBMITTED


def test_admin_api_lists_all_statuses(app, client):
    admin_user = _create_user("admin", "Admin Loans", "admin-loans@example.com")
    customer_user = _create_user("customer", "Applicant", "admin-list@example.com")
    customer = _customer_profile(customer_user, code="CUST-099")

    submitted = LoanApplication(
        application_number="APP-ADMIN-1",
        customer_id=customer.id,
        loan_type="GROW_BUSINESS",
        status=STATUS_SUBMITTED,
        applied_amount=Decimal("5000"),
        tenure_months=6,
        full_name="Applicant",
        nic_number="123456789V",
        mobile_number="0700000000",
    )
    staff_review = LoanApplication(
        application_number="APP-ADMIN-2",
        customer_id=customer.id,
        loan_type="GROW_BUSINESS",
        status=STATUS_STAFF_APPROVED,
        applied_amount=Decimal("6000"),
        tenure_months=9,
        full_name="Applicant",
        nic_number="123456789V",
        mobile_number="0700000000",
    )
    approved = LoanApplication(
        application_number="APP-ADMIN-3",
        customer_id=customer.id,
        loan_type="GROW_BUSINESS",
        status=STATUS_APPROVED,
        applied_amount=Decimal("7000"),
        tenure_months=12,
        full_name="Applicant",
        nic_number="123456789V",
        mobile_number="0700000000",
    )

    db.session.add_all([submitted, staff_review, approved])
    db.session.commit()

    response = client.get(
        "/api/loan-applications", headers=_auth_headers(app, admin_user)
    )

    assert response.status_code == 200
    body = response.get_json()
    statuses = {item["status"] for item in body}
    assert statuses == {STATUS_SUBMITTED, STATUS_STAFF_APPROVED, STATUS_APPROVED}
    assert {item["application_number"] for item in body} == {
        "APP-ADMIN-1",
        "APP-ADMIN-2",
        "APP-ADMIN-3",
    }


def test_admin_api_can_filter_status(app, client):
    admin_user = _create_user("admin", "Admin Filter", "admin-filter@example.com")
    customer_user = _create_user(
        "customer", "Applicant", "admin-filter-customer@example.com"
    )
    customer = _customer_profile(customer_user, code="CUST-098")

    db.session.add_all(
        [
            LoanApplication(
                application_number="APP-FILTER-1",
                customer_id=customer.id,
                loan_type="GROW_BUSINESS",
                status=STATUS_SUBMITTED,
                applied_amount=Decimal("5500"),
                tenure_months=6,
                full_name="Applicant",
                nic_number="123456789V",
                mobile_number="0700000000",
            ),
            LoanApplication(
                application_number="APP-FILTER-2",
                customer_id=customer.id,
                loan_type="GROW_BUSINESS",
                status=STATUS_STAFF_APPROVED,
                applied_amount=Decimal("6500"),
                tenure_months=9,
                full_name="Applicant",
                nic_number="123456789V",
                mobile_number="0700000000",
            ),
        ]
    )
    db.session.commit()

    response = client.get(
        "/api/loan-applications?status=UNDER_REVIEW",
        headers=_auth_headers(app, admin_user),
    )

    assert response.status_code == 200
    body = response.get_json()
    assert len(body) == 1
    assert body[0]["status"] == STATUS_STAFF_APPROVED
    assert body[0]["application_number"] == "APP-FILTER-2"


def test_staff_can_approve_submitted_application_via_staff_endpoint(app, client):
    staff_user = _create_user("staff", "Staff Approver", "staff-approve@example.com")
    customer_user = _create_user(
        "customer", "Applicant", "staff-approve-applicant@example.com"
    )
    customer = _customer_profile(customer_user, code="CUST-030")

    application = LoanApplication(
        application_number="APP-STAFF-1",
        customer_id=customer.id,
        loan_type="GROW_BUSINESS",
        status=STATUS_SUBMITTED,
        applied_amount=Decimal("10000"),
        tenure_months=6,
        full_name="Applicant",
        nic_number="123456789V",
        mobile_number="0700000000",
    )
    db.session.add(application)
    db.session.commit()

    response = client.post(
        f"/staff/loan-applications/{application.id}/approve",
        headers=_auth_headers(app, staff_user),
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == STATUS_STAFF_APPROVED
    assert body.get("assigned_officer_id") == staff_user.id


def test_staff_approval_requires_submitted_status(app, client):
    staff_user = _create_user("staff", "Staff Approver", "staff-approve2@example.com")
    customer_user = _create_user(
        "customer", "Applicant", "staff-approve2-applicant@example.com"
    )
    customer = _customer_profile(customer_user, code="CUST-031")

    application = LoanApplication(
        application_number="APP-STAFF-2",
        customer_id=customer.id,
        loan_type="GROW_BUSINESS",
        status="DRAFT",
        applied_amount=Decimal("8000"),
        tenure_months=4,
        full_name="Applicant",
        nic_number="123456789V",
        mobile_number="0700000000",
    )
    db.session.add(application)
    db.session.commit()

    response = client.post(
        f"/staff/loan-applications/{application.id}/approve",
        headers=_auth_headers(app, staff_user),
    )

    assert response.status_code == 400
    body = response.get_json()
    assert body["status"] == "DRAFT"


def test_non_staff_cannot_call_staff_approval_endpoint(app, client):
    customer_user = _create_user(
        "customer", "Customer", "customer-approval@example.com"
    )
    customer = _customer_profile(customer_user, code="CUST-032")

    application = LoanApplication(
        application_number="APP-STAFF-3",
        customer_id=customer.id,
        loan_type="GROW_BUSINESS",
        status=STATUS_SUBMITTED,
        applied_amount=Decimal("5000"),
        tenure_months=3,
        full_name="Customer",
        nic_number="123456789V",
        mobile_number="0700000000",
    )
    db.session.add(application)
    db.session.commit()

    response = client.post(
        f"/staff/loan-applications/{application.id}/approve",
        headers=_auth_headers(app, customer_user),
    )

    assert response.status_code == 403


def test_non_staff_cannot_access_staff_endpoints(app, client):
    customer_user = _create_user("customer", "Blocked", "blocked@example.com")

    response = client.get(
        "/staff/active-loans",
        headers=_auth_headers(app, customer_user),
    )

    assert response.status_code == 403
    assert response.headers.get("Access-Control-Allow-Origin") is not None


def test_admin_can_approve_submitted_application_without_payload(app, client):
    admin_user = _create_user("admin", "Admin Direct", "admin-direct@example.com")
    customer_user = _create_user(
        "customer", "Direct Customer", "direct-customer@example.com"
    )
    customer = _customer_profile(customer_user, code="CUST-DIRECT")
    application = LoanApplication(
        application_number="APP-DIRECT",
        customer_id=customer.id,
        loan_type="GROW_BUSINESS",
        status=STATUS_SUBMITTED,
        applied_amount=Decimal("8000"),
        tenure_months=4,
        full_name="Direct Customer",
        nic_number="123456789V",
        mobile_number="0700000000",
    )
    db.session.add(application)
    db.session.commit()

    response = client.post(
        f"/loan-applications/{application.id}/approve",
        headers=_auth_headers(app, admin_user),
        json={},
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == STATUS_APPROVED
    assert body["approved_amount"] == 8000.0
    assert body["approved_tenure"] == 4
    assert body["approved_at"] is not None


def test_admin_can_disburse_approved_application_into_active_loan(app, client):
    admin_user = _create_user("admin", "Admin Disburse", "admin-disburse@example.com")
    customer_user = _create_user(
        "customer", "Disburse Customer", "disburse-customer@example.com"
    )
    customer = _customer_profile(customer_user, code="CUST-DISBURSE")
    application = LoanApplication(
        application_number="APP-DISBURSE",
        customer_id=customer.id,
        loan_type="GROW_BUSINESS",
        status=STATUS_APPROVED,
        applied_amount=Decimal("10000"),
        approved_amount=Decimal("9000"),
        tenure_months=6,
        approved_tenure=3,
        interest_rate=Decimal("10"),
        full_name="Disburse Customer",
        nic_number="123456789V",
        mobile_number="0700000000",
    )
    db.session.add(application)
    db.session.commit()

    response = client.post(
        f"/loan-applications/{application.id}/disburse",
        headers=_auth_headers(app, admin_user),
        json={},
    )

    assert response.status_code == 201
    body = response.get_json()
    assert body["loan_number"].startswith("GROW-LOAN-")
    assert body["application"]["status"] == "DISBURSED"

    loan = Loan.query.get(body["loan_id"])
    assert loan is not None
    assert loan.customer_id == customer.id
    assert loan.principal_amount == Decimal("9000.00")
    assert loan.interest_rate == Decimal("10.00")
    assert loan.total_days == 90
    assert loan.status == "ACTIVE"

    loans_response = client.get("/admin/loans", headers=_auth_headers(app, admin_user))
    assert loans_response.status_code == 200
    assert body["loan_number"] in {
        item["loan_number"] for item in loans_response.get_json()
    }


def test_admin_loan_creation_generates_63_day_weekly_ledger(app, client):
    admin_user = _create_user("admin", "Ledger Admin", "ledger-admin@example.com")
    customer_user = _create_user(
        "customer", "Ledger Customer", "ledger-customer@example.com"
    )
    customer = _customer_profile(customer_user, code="CUST-LEDGER-1")

    response = client.post(
        "/admin/loans",
        headers=_auth_headers(app, admin_user),
        json={
            "loan_number": "LN-LEDGER-63",
            "customer_id": customer.id,
            "principal_amount": "9000",
            "interest_rate": "3",
            "total_days": 63,
            "payment_interval_days": 7,
            "start_date": "2026-01-01",
            "end_date": "2026-03-04",
        },
    )

    assert response.status_code == 200
    ledger_response = client.get(
        f"/admin/loans/{response.get_json()['loan_id']}/ledger",
        headers=_auth_headers(app, admin_user),
    )
    body = ledger_response.get_json()
    assert ledger_response.status_code == 200
    assert len(body["ledger"]) == 9
    assert all(entry["period_days"] == 7 for entry in body["ledger"])
    assert body["ledger"][0]["opening_balance"] == 9000.0
    assert body["ledger"][-1]["closing_balance"] == 0.0
    assert body["totals"]["total_principal"] == 9000.0


def test_admin_loan_creation_generates_final_stub_period(app, client):
    admin_user = _create_user("admin", "Stub Admin", "stub-admin@example.com")
    customer_user = _create_user(
        "customer", "Stub Customer", "stub-customer@example.com"
    )
    customer = _customer_profile(customer_user, code="CUST-STUB-1")

    response = client.post(
        "/admin/loans",
        headers=_auth_headers(app, admin_user),
        json={
            "loan_number": "LN-LEDGER-STUB",
            "customer_id": customer.id,
            "principal_amount": "10000",
            "interest_rate": "3",
            "total_days": 65,
            "start_date": "2026-01-01",
            "end_date": "2026-03-06",
        },
    )

    assert response.status_code == 200
    body = client.get(
        f"/admin/loans/{response.get_json()['loan_id']}/ledger",
        headers=_auth_headers(app, admin_user),
    ).get_json()
    assert [entry["period_days"] for entry in body["ledger"]] == [7] * 9 + [2]
    assert body["ledger"][-1]["due_date"] == "2026-03-06"


def test_admin_ledger_payment_calculates_delay_interest(app, client):
    admin_user = _create_user("admin", "Payment Admin", "payment-admin@example.com")
    customer_user = _create_user(
        "customer", "Payment Customer", "payment-customer@example.com"
    )
    customer = _customer_profile(customer_user, code="CUST-PAY-1")
    loan = Loan(
        loan_number="LN-PAY-DELAY",
        customer_id=customer.id,
        principal_amount=Decimal("3000"),
        interest_rate=Decimal("3"),
        total_days=7,
        payment_interval_days=7,
        daily_installment=Decimal("0"),
        total_payable=Decimal("0"),
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 7),
        status="ACTIVE",
        created_by_id=admin_user.id,
    )
    db.session.add(loan)
    db.session.flush()
    from app.loan_ledger import generate_loan_ledger

    generate_loan_ledger(loan)
    db.session.commit()
    entry = loan.ledger_entries[0]

    response = client.post(
        f"/admin/loans/{loan.id}/ledger/{entry.id}/payment",
        headers=_auth_headers(app, admin_user),
        json={"paid_amount": "3020", "paid_date": "2026-01-10"},
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["ledger"]["delay_days"] == 3
    assert body["ledger"]["delay_interest"] == 9.0
    assert body["ledger"]["status"] == "PARTIAL"


def test_disbursement_creates_ledger_automatically(app, client):
    admin_user = _create_user("admin", "Auto Ledger", "auto-ledger@example.com")
    customer_user = _create_user(
        "customer", "Auto Customer", "auto-customer@example.com"
    )
    customer = _customer_profile(customer_user, code="CUST-AUTO-LEDGER")
    application = LoanApplication(
        application_number="APP-AUTO-LEDGER",
        customer_id=customer.id,
        loan_type="GROW_BUSINESS",
        status=STATUS_APPROVED,
        applied_amount=Decimal("7000"),
        approved_amount=Decimal("7000"),
        tenure_months=1,
        approved_tenure=1,
        interest_rate=Decimal("3"),
        full_name="Auto Customer",
        nic_number="123456789V",
        mobile_number="0700000000",
    )
    db.session.add(application)
    db.session.commit()

    response = client.post(
        f"/loan-applications/{application.id}/disburse",
        headers=_auth_headers(app, admin_user),
        json={"payment_interval_days": 7},
    )

    assert response.status_code == 201
    loan = Loan.query.get(response.get_json()["loan_id"])
    assert len(loan.ledger_entries) == 5
    assert [entry.period_days for entry in loan.ledger_entries] == [7, 7, 7, 7, 2]


def _draft_application(customer: Customer, number: str = "APP-DOCS") -> LoanApplication:
    application = LoanApplication(
        application_number=number,
        customer_id=customer.id,
        loan_type="GROW_PERSONAL",
        status="DRAFT",
        applied_amount=Decimal("7000"),
        tenure_months=5,
        full_name=customer.full_name,
        nic_number="123456789V",
        mobile_number="0700000000",
        monthly_income=Decimal("45000"),
        monthly_expenses=Decimal("12000"),
        extra_data={
            "employment_type": "salaried",
            "net_monthly_salary": "45000",
            "employer_name": "Acme Ltd",
        },
    )
    db.session.add(application)
    db.session.commit()
    return application


def _multipart_file(filename: str = "nic-front.png"):
    return (BytesIO(b"fake-image-bytes"), filename)


def test_document_upload_storage_unavailable_returns_json_without_document_row(app, client, monkeypatch):
    for key in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"):
        monkeypatch.delenv(key, raising=False)
    customer_user = _create_user("customer", "Docs Customer", "docs-customer@example.com")
    customer = _customer_profile(customer_user, code="CUST-DOCS")
    application = _draft_application(customer, "APP-DOCS-STORAGE")

    response = client.post(
        f"/loan-applications/{application.id}/documents",
        headers=_auth_headers(app, customer_user),
        data={"document_type": "NIC_FRONT", "file": _multipart_file()},
        content_type="multipart/form-data",
    )

    assert response.status_code == 500
    body = response.get_json()
    assert body["success"] is False
    assert body["message"] == "Document upload failed"
    assert "SUPABASE" in body["error"]
    assert LoanApplicationDocument.query.filter_by(loan_application_id=application.id).count() == 0


def test_document_upload_invalid_document_type(app, client):
    customer_user = _create_user("customer", "Invalid Doc", "invalid-doc@example.com")
    customer = _customer_profile(customer_user, code="CUST-BAD-DOC")
    application = _draft_application(customer, "APP-DOCS-INVALID")

    response = client.post(
        f"/loan-applications/{application.id}/documents",
        headers=_auth_headers(app, customer_user),
        data={"document_type": "NOT_ALLOWED", "file": _multipart_file()},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert response.get_json()["message"] == "Invalid document_type"
    assert LoanApplicationDocument.query.filter_by(loan_application_id=application.id).count() == 0


def test_document_upload_missing_file(app, client):
    customer_user = _create_user("customer", "Missing File", "missing-file@example.com")
    customer = _customer_profile(customer_user, code="CUST-NO-FILE")
    application = _draft_application(customer, "APP-DOCS-MISSING")

    response = client.post(
        f"/loan-applications/{application.id}/documents",
        headers=_auth_headers(app, customer_user),
        data={"document_type": "NIC_FRONT"},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert response.get_json()["message"] == "file is required"
    assert LoanApplicationDocument.query.filter_by(loan_application_id=application.id).count() == 0


def test_document_upload_success(app, client, monkeypatch):
    customer_user = _create_user("customer", "Upload Success", "upload-success@example.com")
    customer = _customer_profile(customer_user, code="CUST-UPLOAD")
    application = _draft_application(customer, "APP-DOCS-SUCCESS")

    def fake_upload(application_id, document_type, uploaded_file):
        assert application_id == application.id
        assert document_type == "NIC_FRONT"
        assert uploaded_file.filename == "nic-front.png"
        return f"loan_documents/{application_id}/NIC_FRONT_test.png"

    monkeypatch.setattr("app.routes.loan_applications.upload_document_to_supabase", fake_upload)

    response = client.post(
        f"/loan-applications/{application.id}/documents",
        headers=_auth_headers(app, customer_user),
        data={"document_type": "nic_front", "file": _multipart_file()},
        content_type="multipart/form-data",
    )

    assert response.status_code == 201
    body = response.get_json()
    assert body["message"] == "Document uploaded"
    assert body["file_path"] == f"loan_documents/{application.id}/NIC_FRONT_test.png"
    document = LoanApplicationDocument.query.get(body["document_id"])
    assert document.document_type == "NIC_FRONT"
    assert document.file_path == body["file_path"]


def test_document_upload_unhandled_exception_returns_structured_json(app, client, monkeypatch):
    customer_user = _create_user("customer", "Upload Error", "upload-error@example.com")
    customer = _customer_profile(customer_user, code="CUST-UPLOAD-ERR")
    application = _draft_application(customer, "APP-DOCS-ERROR")

    def fake_upload(*_args, **_kwargs):
        raise RuntimeError("storage service unavailable")

    monkeypatch.setattr("app.routes.loan_applications.upload_document_to_supabase", fake_upload)

    response = client.post(
        f"/loan-applications/{application.id}/documents",
        headers=_auth_headers(app, customer_user),
        data={"document_type": "NIC_FRONT", "file": _multipart_file()},
        content_type="multipart/form-data",
    )

    assert response.status_code == 500
    assert response.get_json() == {
        "success": False,
        "message": "Document upload failed",
        "error": "storage service unavailable",
    }
    assert LoanApplicationDocument.query.filter_by(loan_application_id=application.id).count() == 0


def test_customer_currency_fields_are_lkr_formatted_without_changing_numbers(app, client):
    customer_user = _create_user("customer", "Currency Customer", "currency@example.com")
    staff_user = _create_user("staff", "Currency Staff", "currency-staff@example.com")
    customer = _customer_profile(customer_user, code="CUST-CURRENCY")

    loan = Loan(
        loan_number="LN-CURRENCY-1",
        customer_id=customer.id,
        principal_amount=Decimal("15000.00"),
        interest_rate=Decimal("10.00"),
        total_days=30,
        daily_installment=Decimal("550.00"),
        total_payable=Decimal("16500.00"),
        start_date=date.today(),
        end_date=date.today(),
        status="Active",
        created_by_id=staff_user.id,
    )
    db.session.add(loan)
    db.session.commit()

    response = client.get("/customer/loans", headers=_auth_headers(app, customer_user))

    assert response.status_code == 200
    body = response.get_json()
    assert body["summary"]["currency"] == "LKR"
    assert body["summary"]["total_outstanding"] == 16500.0
    assert isinstance(body["summary"]["total_outstanding"], float)
    assert body["summary"]["total_outstanding_formatted"] == "Rs. 16,500.00"

    loan_body = body["loans"][0]
    assert loan_body["currency"] == "LKR"
    assert loan_body["principal_amount"] == 15000.0
    assert isinstance(loan_body["principal_amount"], float)
    assert loan_body["principal_amount_formatted"] == "Rs. 15,000.00"
    assert Loan.query.get(loan.id).principal_amount == Decimal("15000.00")


def test_admin_ledger_currency_fields_are_lkr_formatted_without_changing_numbers(app, client):
    admin_user = _create_user("admin", "Currency Admin", "currency-admin@example.com")
    customer_user = _create_user(
        "customer", "Ledger Currency Customer", "ledger-currency@example.com"
    )
    customer = _customer_profile(customer_user, code="CUST-LEDGER-CURRENCY")

    create_response = client.post(
        "/admin/loans",
        headers=_auth_headers(app, admin_user),
        json={
            "loan_number": "LN-LEDGER-CURRENCY",
            "customer_id": customer.id,
            "principal_amount": "15000.00",
            "interest_rate": "0",
            "total_days": 1,
            "payment_interval_days": 1,
            "start_date": date.today().isoformat(),
            "end_date": date.today().isoformat(),
        },
    )
    assert create_response.status_code == 200
    loan_id = create_response.get_json()["loan_id"]

    response = client.get(
        f"/admin/loans/{loan_id}/ledger", headers=_auth_headers(app, admin_user)
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["loan"]["currency"] == "LKR"
    assert body["loan"]["principal_amount"] == 15000.0
    assert body["loan"]["principal_amount_formatted"] == "Rs. 15,000.00"
    assert body["ledger"][0]["currency"] == "LKR"
    assert body["ledger"][0]["installment_amount"] == 15000.0
    assert body["ledger"][0]["installment_amount_formatted"] == "Rs. 15,000.00"
    assert body["totals"]["currency"] == "LKR"
    assert body["totals"]["total_payable"] == 15000.0
    assert body["totals"]["total_payable_formatted"] == "Rs. 15,000.00"
    assert Loan.query.get(loan_id).total_payable == Decimal("15000.00")


def _approved_terms_payload(**overrides):
    payload = {
        "approved_amount": "15000",
        "loan_days": 63,
        "repayment_frequency": "WEEKLY",
        "number_of_installments": 8,
        "installment_amount": "2400",
        "interest_type": "FLAT",
    }
    payload.update(overrides)
    return payload


def test_flexible_terms_approval_and_disbursement_create_eight_weekly_rows(app, client):
    from app.models import AccountingJournalEntry, Payment

    admin_user = _create_user("admin", "Flexible Admin", "flex-admin@example.com")
    customer_user = _create_user("customer", "Flexible Customer", "flex-customer@example.com")
    customer = _customer_profile(customer_user, code="CUST-FLEX")
    application = LoanApplication(
        application_number="APP-FLEX",
        customer_id=customer.id,
        loan_type="GROW_BUSINESS",
        status=STATUS_SUBMITTED,
        applied_amount=Decimal("15000"),
        tenure_months=3,
        full_name="Flexible Customer",
        nic_number="123456789V",
        mobile_number="0700000000",
    )
    db.session.add(application); db.session.commit()

    before_journals = AccountingJournalEntry.query.count()
    approval = client.post(f"/loan-applications/{application.id}/approve", headers=_auth_headers(app, admin_user), json=_approved_terms_payload())
    assert approval.status_code == 200
    body = approval.get_json()
    assert body["status"] == STATUS_APPROVED
    assert body["approved_amount"] == 15000.0
    assert body["total_repayment"] == 19200.0
    assert body["total_interest"] == 4200.0
    assert body["interest_rate"] == 28.0
    assert AccountingJournalEntry.query.count() == before_journals

    disbursed = client.post(f"/loan-applications/{application.id}/disburse", headers=_auth_headers(app, admin_user), json={"disbursement_date": "2026-01-01"})
    assert disbursed.status_code == 201
    loan = Loan.query.get(disbursed.get_json()["loan_id"])
    assert loan.principal_amount == Decimal("15000.00")
    assert loan.loan_days == 63
    assert loan.maturity_date == date(2026, 3, 5)
    assert len(loan.ledger_entries) == 8
    assert sum((entry.principal_amount for entry in loan.ledger_entries), Decimal("0")) == Decimal("15000.00")
    assert sum((entry.interest_amount for entry in loan.ledger_entries), Decimal("0")) == Decimal("4200.00")
    assert sum((entry.installment_amount for entry in loan.ledger_entries), Decimal("0")) == Decimal("19200.00")
    assert loan.ledger_entries[0].principal_amount == Decimal("1875.00")
    assert loan.ledger_entries[0].interest_amount == Decimal("525.00")

    pay = client.post("/staff/payments", headers=_auth_headers(app, admin_user), json={"loan_id": loan.id, "amount_collected": "2400", "collection_date": "2026-01-07", "payment_method": "Cash"})
    assert pay.status_code == 200
    payment = Payment.query.get(pay.get_json()["payment_id"])
    assert payment.principal_paid == Decimal("1875.00")
    assert payment.interest_paid == Decimal("525.00")
    journals = AccountingJournalEntry.query.filter_by(status="POSTED").all()
    assert all(j.total_debit == j.total_credit for j in journals)


def test_flexible_terms_validation_rejects_bad_payloads(app, client):
    admin_user = _create_user("admin", "Bad Flex Admin", "bad-flex-admin@example.com")
    customer_user = _create_user("customer", "Bad Flex Customer", "bad-flex-customer@example.com")
    customer = _customer_profile(customer_user, code="CUST-BAD-FLEX")
    for idx, overrides in enumerate([
        {"installment_amount": "1000"},
        {"approved_amount": "0"},
        {"loan_days": -1},
        {"repayment_frequency": "YEARLY"},
        {"number_of_installments": 0},
    ]):
        application = LoanApplication(application_number=f"APP-BAD-FLEX-{idx}", customer_id=customer.id, loan_type="GROW_BUSINESS", status=STATUS_SUBMITTED, applied_amount=Decimal("15000"), tenure_months=3, full_name="Bad Flex Customer", nic_number="123456789V", mobile_number="0700000000")
        db.session.add(application); db.session.commit()
        response = client.post(f"/loan-applications/{application.id}/approve", headers=_auth_headers(app, admin_user), json=_approved_terms_payload(**overrides))
        assert response.status_code == 400
        assert response.get_json().get("errors")


def test_legacy_loan_with_null_flexible_fields_still_loads(app, client):
    admin_user = _create_user("admin", "Legacy Admin", "legacy-admin@example.com")
    customer_user = _create_user("customer", "Legacy Customer", "legacy-customer@example.com")
    customer = _customer_profile(customer_user, code="CUST-LEGACY")
    loan = Loan(loan_number="LN-LEGACY-NULLS", customer_id=customer.id, principal_amount=Decimal("1000"), interest_rate=Decimal("0"), total_days=10, daily_installment=Decimal("100"), total_payable=Decimal("1000"), start_date=date.today(), end_date=date.today(), status="ACTIVE", created_by_id=admin_user.id)
    db.session.add(loan); db.session.commit()
    response = client.get(f"/admin/loans/{loan.id}/ledger", headers=_auth_headers(app, admin_user))
    assert response.status_code == 200
    assert response.get_json()["loan"]["loan_days"] is None
