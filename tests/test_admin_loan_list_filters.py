from datetime import date
from decimal import Decimal

from flask_jwt_extended import create_access_token

from app.extensions import db
from app.models import Customer, Loan, Payment, User


def _user(role, name, email):
    user = User(email=email, name=name, role=role)
    user.set_password("password")
    db.session.add(user)
    db.session.flush()
    return user


def _loan(admin, customer, number, start_date, status="ACTIVE", principal="1000.00"):
    loan = Loan(
        loan_number=number, customer_id=customer.id, principal_amount=Decimal(principal),
        interest_rate=Decimal("10"), total_days=30, payment_interval_days=30,
        daily_installment=Decimal("36.67"), total_payable=Decimal("1100.00"),
        start_date=start_date, end_date=start_date, status=status, created_by_id=admin.id,
    )
    db.session.add(loan)
    return loan


def _headers(app, user):
    with app.app_context():
        token = create_access_token(identity=str(user.id), additional_claims={"role": "admin"})
    return {"Authorization": f"Bearer {token}"}


def test_admin_loan_list_search_filters_and_pagination(app, client):
    admin = _user("admin", "Admin", "admin-list@example.com")
    prakash_user = _user("customer", "Prakash Silva", "prakash@example.com")
    other_user = _user("customer", "Other Customer", "other@example.com")
    prakash = Customer(user_id=prakash_user.id, customer_code="CUST-PRAKASH", full_name="Prakash Silva", nic_number="198900701481", mobile="0760677104")
    other = Customer(user_id=other_user.id, customer_code="CUST-OTHER", full_name="Other Customer", nic_number="200012345678", mobile="0719999999")
    db.session.add_all([prakash, other])
    db.session.flush()
    settled = _loan(admin, prakash, "GROW-LOAN-20260718", date(2026, 7, 18), "Settled")
    active = _loan(admin, prakash, "GROW-LOAN-20260719", date(2026, 7, 19), "Active", "2000.00")
    other_loan = _loan(admin, other, "GROW-LOAN-OTHER", date(2026, 7, 1), "ACTIVE")
    db.session.flush()
    db.session.add(Payment(loan_id=settled.id, amount_collected=Decimal("1100.00"), collected_by_id=admin.id))
    db.session.commit()

    headers = _headers(app, admin)
    for query in ("GROW-LOAN-20260718", "Prakash", "076", "198900701481"):
        response = client.get("/admin/loans", query_string={"q": query}, headers=headers)
        assert response.status_code == 200
        assert {item["id"] for item in response.get_json()["items"]} >= {settled.id}

    assert [item["id"] for item in client.get("/admin/loans?status=SETTLED", headers=headers).get_json()["items"]] == [settled.id]
    outstanding = client.get("/admin/loans?balance_status=OUTSTANDING", headers=headers).get_json()["items"]
    assert {item["id"] for item in outstanding} == {active.id, other_loan.id}
    ranged = client.get("/admin/loans?date_from=2026-07-18&date_to=2026-07-19", headers=headers).get_json()["items"]
    assert {item["id"] for item in ranged} == {settled.id, active.id}
    combined = client.get("/admin/loans?q=Prakash&status=ACTIVE&date_from=2026-07-19", headers=headers).get_json()["items"]
    assert [item["id"] for item in combined] == [active.id]
    paged = client.get("/admin/loans?page=2&page_size=1&sort_by=loan_number&sort_direction=asc", headers=headers).get_json()
    assert paged["pagination"] == {"page": 2, "page_size": 1, "total_items": 3, "total_pages": 3, "has_next": True, "has_previous": True}
    empty = client.get("/admin/loans?q=does-not-exist", headers=headers).get_json()
    assert empty["items"] == [] and empty["pagination"]["total_items"] == 0


def test_admin_loan_list_rejects_invalid_ranges(app, client):
    admin = _user("admin", "Admin", "admin-invalid-list@example.com")
    db.session.commit()
    response = client.get("/admin/loans?date_from=2026-07-20&date_to=2026-07-19", headers=_headers(app, admin))
    assert response.status_code == 422
    assert response.get_json() == {"error": "invalid_date_range", "message": "Date From cannot be later than Date To."}
