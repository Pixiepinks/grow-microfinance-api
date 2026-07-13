from datetime import date
from decimal import Decimal

from flask_jwt_extended import create_access_token

from app.accounting import seed_default_accounts
from app.extensions import db
from app.models import AccountingAccount, Customer, Loan, User


def _user(role="admin", name=None, email=None):
    u = User(email=email or f"{role}-{name or role}@example.com", name=name or role, role=role)
    u.set_password("password")
    db.session.add(u)
    db.session.commit()
    return u


def _headers(app, user):
    with app.app_context():
        token = create_access_token(identity=str(user.id), additional_claims={"role": user.role})
    return {"Authorization": f"Bearer {token}"}


def _loan(staff):
    cu = _user("customer", "Collector Customer", "collector-customer@example.com")
    c = Customer(user_id=cu.id, customer_code="COL-CUST", full_name="Collector Customer")
    db.session.add(c)
    db.session.flush()
    loan = Loan(
        loan_number="COL-LN", customer_id=c.id, principal_amount=Decimal("1000"),
        interest_rate=Decimal("0"), total_days=1, payment_interval_days=1,
        daily_installment=Decimal("1000"), total_payable=Decimal("1000"),
        start_date=date.today(), end_date=date.today(), status="Active", created_by_id=staff.id,
    )
    db.session.add(loan)
    db.session.commit()
    return loan


def test_existing_staff_enabled_as_collector_creates_account_and_options_exclude_control(app, client):
    admin = _user("admin", "Admin", "collector-admin@example.com")
    sanjana = _user("staff", "Sanjana", "sanjana@example.com")
    seed_default_accounts(); db.session.commit()

    resp = client.post(
        "/admin/collectors",
        headers=_headers(app, admin),
        json={"staff_id": sanjana.id, "collector_code": "COL-0012", "create_collection_account": True},
    )

    assert resp.status_code == 201
    body = resp.get_json()
    assert body["is_collector"] is True
    assert body["default_collection_account"]["name"] == "Collection Account – Sanjana"

    options = client.get("/admin/collections/collectors/options", headers=_headers(app, admin))
    assert options.status_code == 200
    items = options.get_json()["items"]
    assert any(i["collector_name"] == "Sanjana" for i in items)
    assert all(i["collection_account_code"] != "1050" for i in items)


def test_cash_collector_payment_validations_and_inactive_blocked(app, client):
    admin = _user("admin", "Admin2", "collector-admin2@example.com")
    sanjana = _user("staff", "Sanjana", "sanjana2@example.com")
    seed_default_accounts(); db.session.commit()
    setup = client.post("/admin/collectors", headers=_headers(app, admin), json={"staff_id": sanjana.id, "collector_code": "COL-002", "create_collection_account": True})
    acct_id = setup.get_json()["default_collection_account"]["id"]
    loan = _loan(admin)

    missing = client.post("/staff/payments", headers=_headers(app, admin), json={"loan_id": loan.id, "amount_collected": "10", "collection_date": date.today().isoformat(), "payment_method": "CASH_COLLECTOR"})
    assert missing.status_code == 422

    control = AccountingAccount.query.filter_by(account_code="1050").first()
    bad = client.post("/staff/payments", headers=_headers(app, admin), json={"loan_id": loan.id, "amount_collected": "10", "collection_date": date.today().isoformat(), "payment_method": "CASH_COLLECTOR", "collector_id": sanjana.id, "collection_account_id": control.id})
    assert bad.status_code == 422

    client.post(f"/admin/collectors/{sanjana.id}/deactivate", headers=_headers(app, admin))
    options = client.get("/admin/collections/collectors/options", headers=_headers(app, admin)).get_json()["items"]
    assert all(i["collector_id"] != sanjana.id for i in options)
    inactive = client.post("/staff/payments", headers=_headers(app, admin), json={"loan_id": loan.id, "amount_collected": "10", "collection_date": date.today().isoformat(), "payment_method": "CASH_COLLECTOR", "collector_id": sanjana.id, "collection_account_id": acct_id})
    assert inactive.status_code == 422


def test_second_default_account_for_collector_blocked(app, client):
    admin = _user("admin", "Admin3", "collector-admin3@example.com")
    viraj = _user("staff", "Viraj", "viraj@example.com")
    seed_default_accounts(); db.session.commit()
    first = client.post("/admin/collectors", headers=_headers(app, admin), json={"staff_id": viraj.id, "collector_code": "COL-003", "create_collection_account": True})
    assert first.status_code == 201
    second = client.patch(f"/admin/collectors/{viraj.id}", headers=_headers(app, admin), json={"create_collection_account": True})
    assert second.status_code == 422


def test_collector_routes_support_trailing_slash_staff_options_and_invalid_staff(app, client):
    admin = _user("admin", "Admin4", "collector-admin4@example.com")
    staff = _user("staff", "Trailing", "trailing@example.com")
    seed_default_accounts(); db.session.commit()

    route_rules = {str(rule) for rule in app.url_map.iter_rules()}
    assert "/admin/collectors" in route_rules
    assert "/admin/collectors/staff-options" in route_rules
    assert "/admin/collections/collectors/options" in route_rules

    options = client.get("/admin/collectors/staff-options", headers=_headers(app, admin))
    assert options.status_code == 200
    assert any(item["staff_id"] == staff.id and item["already_collector"] is False for item in options.get_json()["items"])

    invalid = client.post("/admin/collectors/", headers=_headers(app, admin), json={"staff_id": 999999})
    assert invalid.status_code == 422
    assert invalid.is_json
    assert invalid.get_json()["error"] == "invalid_staff_id"

    created = client.post(
        "/admin/collectors/",
        headers=_headers(app, admin),
        json={"staff_id": staff.id, "collector_code": "COL-SLASH", "status": "ACTIVE", "can_collect_cash": True, "create_collection_account": True},
    )
    assert created.status_code == 201
    body = created.get_json()
    assert body["staff_id"] == staff.id
    assert body["default_collection_account"]["code"] != "1050"


def test_seed_default_accounts_is_idempotent_for_collector_control_account(app):
    seed_default_accounts(); db.session.commit()
    seed_default_accounts(); db.session.commit()

    accounts = AccountingAccount.query.filter_by(account_code="1050").all()
    assert len(accounts) == 1
    acct = accounts[0]
    assert acct.account_name == "Collector Cash Clearing – Control"
    assert acct.account_subtype == "COLLECTION_CLEARING_CONTROL"
    assert acct.cash_flow_category == "COLLECTION_CLEARING_CONTROL"
    assert acct.allow_manual_posting is False
    assert acct.is_system_account is True
