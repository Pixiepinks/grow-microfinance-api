from datetime import date
from decimal import Decimal
from uuid import uuid4

from flask_jwt_extended import create_access_token
from sqlalchemy import event

from app.accounting import seed_default_accounts
from app.extensions import db
from app.models import (
    AccountingAccount, AccountingJournalLine, AccountingSetting, CollectionDepositAllocation,
    CollectionDepositBatch, Customer, Loan, Payment, User,
)


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


def _deposit_setup(app, client):
    suffix = uuid4().hex
    admin = _user("admin", f"Deposit Admin {suffix}", f"deposit-admin-{suffix}@example.com")
    collector = _user("staff", f"Deposit Collector {suffix}", f"deposit-collector-{suffix}@example.com")
    seed_default_accounts(); db.session.commit()
    created = client.post(
        "/admin/collectors",
        headers=_headers(app, admin),
        json={"staff_id": collector.id, "collector_code": f"COL-DEP-{suffix[:8]}", "create_collection_account": True},
    )
    account_id = created.get_json()["default_collection_account"]["id"]
    cu = _user("customer", f"Collector Customer {suffix}", f"collector-customer-{suffix}@example.com")
    c = Customer(user_id=cu.id, customer_code=f"COL-CUST-{suffix[:8]}", full_name="Collector Customer")
    db.session.add(c); db.session.flush()
    loan = Loan(
        loan_number=f"COL-LN-{suffix[:8]}", customer_id=c.id, principal_amount=Decimal("3000"),
        interest_rate=Decimal("0"), total_days=1, payment_interval_days=1,
        daily_installment=Decimal("3000"), total_payable=Decimal("3000"),
        start_date=date.today(), end_date=date.today(), status="Active", created_by_id=admin.id,
    )
    db.session.add(loan); db.session.commit()
    paid = client.post(
        "/staff/payments",
        headers=_headers(app, admin),
        json={
            "loan_id": loan.id,
            "amount_collected": "2100.00",
            "collection_date": date.today().isoformat(),
            "payment_method": "CASH_COLLECTOR",
            "collector_id": collector.id,
            "collection_account_id": account_id,
        },
    )
    assert paid.status_code == 200
    bank = AccountingAccount.query.filter_by(account_code="1010").first()
    return admin, collector, account_id, bank.id, paid.get_json()["payment_id"]


def _deposit_payload(collector, account_id, bank_id, payment_id, **overrides):
    payload = {
        "collector_id": collector.id,
        "collector_account_id": account_id,
        "bank_account_id": bank_id,
        "deposit_date": date.today().isoformat(),
        "allocations": [{"payment_id": payment_id, "amount": "2100.00"}],
    }
    payload.update(overrides)
    return payload


def test_undeposited_collections_returns_scalar_customer_and_collector_fields(app, client):
    admin, collector, account_id, _bank_id, payment_id = _deposit_setup(app, client)
    payment = db.session.get(Payment, payment_id)
    customer = payment.loan.customer

    response = client.get("/admin/collections/undeposited", headers=_headers(app, admin))

    assert response.status_code == 200
    row = next(item for item in response.get_json()["items"] if item["id"] == payment_id)
    assert row["customer_id"] == customer.id
    assert row["customer_name"] == customer.full_name
    assert row["customer_code"] == customer.customer_code
    assert row["collector_id"] == collector.id
    assert row["collector_name"] == collector.name
    assert row["collector_code"] == collector.collector_code
    assert row["loan_number"] == payment.loan.loan_number
    # Existing names remain available alongside normalized response names.
    assert row["payment_id"] == row["id"]
    assert row["customer"] == row["customer_name"]
    assert row["amount_already_deposited"] == row["amount_deposited"] == "0.00"
    assert row["deposit_status"] == row["status"] == "UNDEPOSITED"
    assert row["collection_account_id"] == account_id


def test_undeposited_collections_resolves_account_collector_and_keeps_unknown_legacy_null(app, client):
    admin, collector, _account_id, _bank_id, payment_id = _deposit_setup(app, client)
    account_payment = db.session.get(Payment, payment_id)
    account_payment.collector_id = None
    legacy = Payment(
        loan_id=account_payment.loan_id,
        collection_date=date.today(),
        amount_collected=Decimal("25.00"),
        collected_by_id=admin.id,
        collection_method="CASH_COLLECTOR",
        receipt_number=f"LEGACY-{uuid4().hex}",
        journal_id=account_payment.journal_id,
        status="POSTED",
        deposit_status="UNDEPOSITED",
    )
    db.session.add(legacy)
    db.session.commit()

    response = client.get("/admin/collections/undeposited", headers=_headers(app, admin))

    rows = {item["id"]: item for item in response.get_json()["items"]}
    assert rows[payment_id]["collector_id"] == collector.id
    assert rows[payment_id]["collector_name"] == collector.name
    assert rows[legacy.id]["customer_name"] == account_payment.loan.customer.full_name
    assert rows[legacy.id]["collector_id"] is None
    assert rows[legacy.id]["collector_name"] is None
    assert rows[legacy.id]["collector_code"] is None


def test_undeposited_collections_eager_loads_display_relationships(app, client):
    admin, collector, account_id, _bank_id, payment_id = _deposit_setup(app, client)
    original = db.session.get(Payment, payment_id)
    for index in range(3):
        db.session.add(Payment(
            loan_id=original.loan_id, collection_date=date.today(),
            amount_collected=Decimal("10.00"), collected_by_id=admin.id,
            collection_method="CASH_COLLECTOR", collector_id=collector.id,
            collection_account_id=account_id, receipt_number=f"QUERY-COUNT-{index}-{uuid4().hex}",
            journal_id=original.journal_id, status="POSTED", deposit_status="UNDEPOSITED",
        ))
    db.session.commit()
    statements = []

    def record_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", record_statement)
    try:
        response = client.get("/admin/collections/undeposited", headers=_headers(app, admin))
    finally:
        event.remove(db.engine, "before_cursor_execute", record_statement)

    assert response.status_code == 200
    assert len(response.get_json()["items"]) == 4
    # Authentication may issue its own fixed queries, but all payment display
    # relationships must be served by the endpoint's single joined query.
    assert sum("FROM payments" in statement for statement in statements) == 1


def test_collection_deposit_preview_missing_account_returns_422(app, client):
    admin, collector, account_id, bank_id, payment_id = _deposit_setup(app, client)
    payload = _deposit_payload(collector, account_id, bank_id, payment_id)
    payload.pop("collector_account_id")

    resp = client.post("/admin/collection-deposits/preview", headers=_headers(app, admin), json=payload)

    assert resp.status_code == 422
    body = resp.get_json()
    assert body["error"] == "Collection deposit validation failed"
    assert body["missing_fields"] == ["collector_account_id"]
    assert "Traceback" not in resp.get_data(as_text=True)


def test_collection_deposit_preview_validates_accounts_dates_and_amounts(app, client):
    admin, collector, account_id, bank_id, payment_id = _deposit_setup(app, client)
    headers = _headers(app, admin)

    valid = client.post("/admin/collection-deposits/preview", headers=headers, json=_deposit_payload(collector, account_id, bank_id, payment_id))
    assert valid.status_code == 200
    preview = valid.get_json()
    assert preview["journal_preview"]["debits"][0]["account_code"] == "1010"
    assert preview["journal_preview"]["credits"][0]["account_id"] == account_id

    other = AccountingAccount(account_code="1059", account_name="Wrong Collector", account_type="ASSET", normal_balance="DEBIT", account_subtype="COLLECTION_CLEARING", is_collection_account=True, allow_manual_posting=True, collector_id=collector.id)
    db.session.add(other); db.session.commit()
    wrong = client.post("/admin/collection-deposits/preview", headers=headers, json=_deposit_payload(collector, other.id, bank_id, payment_id))
    assert wrong.status_code == 422

    control = AccountingAccount.query.filter_by(account_code="1050").first()
    control_resp = client.post("/admin/collection-deposits/preview", headers=headers, json=_deposit_payload(collector, control.id, bank_id, payment_id))
    assert control_resp.status_code == 422

    before_payment = client.post("/admin/collection-deposits/preview", headers=headers, json=_deposit_payload(collector, account_id, bank_id, payment_id, deposit_date="2000-01-01"))
    assert before_payment.status_code == 422

    excessive = client.post("/admin/collection-deposits/preview", headers=headers, json=_deposit_payload(collector, account_id, bank_id, payment_id, allocations=[{"payment_id": payment_id, "amount": "2100.01"}]))
    assert excessive.status_code == 422


def test_collection_deposit_posts_full_deposit_and_rolls_back_on_failure(app, client, monkeypatch):
    admin, collector, account_id, bank_id, payment_id = _deposit_setup(app, client)
    headers = _headers(app, admin)

    posted = client.post("/admin/collection-deposits", headers=headers, json=_deposit_payload(collector, account_id, bank_id, payment_id))
    assert posted.status_code == 201
    body = posted.get_json()
    assert body["status"] == "POSTED"
    payment = db.session.get(__import__("app.models", fromlist=["Payment"]).Payment, payment_id)
    assert payment.deposit_status == "DEPOSITED"
    assert payment.undeposited_amount == Decimal("0.00")
    lines = body["journal_entry_id"] and __import__("app.models", fromlist=["AccountingJournalLine"]).AccountingJournalLine.query.filter_by(journal_entry_id=body["journal_entry_id"]).all()
    assert sum((line.debit for line in lines), Decimal("0.00")) == sum((line.credit for line in lines), Decimal("0.00"))

    admin2, collector2, account2, bank2, payment2 = _deposit_setup(app, client)
    import app.accounting as accounting
    def fail_journal(*args, **kwargs):
        raise accounting.AccountingError("journal creation failed")
    monkeypatch.setattr(accounting, "create_draft_journal", fail_journal)
    failed = client.post("/admin/collection-deposits", headers=_headers(app, admin2), json=_deposit_payload(collector2, account2, bank2, payment2))
    assert failed.status_code == 422
    rolled_back_payment = db.session.get(__import__("app.models", fromlist=["Payment"]).Payment, payment2)
    assert rolled_back_payment.deposit_status == "UNDEPOSITED"


def test_payment_can_be_partially_deposited_across_multiple_batches(app, client):
    admin, collector, account_id, bank_id, payment_id = _deposit_setup(app, client)
    headers = _headers(app, admin)
    payment = db.session.get(Payment, payment_id)
    payment_count = Payment.query.filter_by(loan_id=payment.loan_id).count()

    first = client.post("/admin/collection-deposits", headers=headers, json=_deposit_payload(
        collector, account_id, bank_id, payment_id,
        allocations=[{"payment_id": payment_id, "amount": "650.00"}],
    ))
    assert first.status_code == 201
    payment = db.session.get(Payment, payment_id)
    assert payment.deposited_amount == Decimal("650.00")
    assert payment.undeposited_amount == Decimal("1450.00")
    assert payment.deposit_status == "PARTIALLY_DEPOSITED"

    listing = client.get("/admin/collections/undeposited", headers=headers)
    row = next(item for item in listing.get_json()["items"] if item["payment_id"] == payment_id)
    assert row["amount_deposited"] == "650.00"
    assert row["undeposited_amount"] == "1450.00"

    second = client.post("/admin/collection-deposits", headers=headers, json=_deposit_payload(
        collector, account_id, bank_id, payment_id,
        allocations=[{"payment_id": payment_id, "amount": "1450.00"}],
    ))
    assert second.status_code == 201
    assert CollectionDepositAllocation.query.filter_by(payment_id=payment_id).count() == 2
    assert len({a.deposit_batch_id for a in CollectionDepositAllocation.query.filter_by(payment_id=payment_id)}) == 2
    assert Payment.query.filter_by(loan_id=payment.loan_id).count() == payment_count
    assert db.session.get(Payment, payment_id).deposited_amount == Decimal("2100.00")
    assert payment_id not in [item["payment_id"] for item in client.get(
        "/admin/collections/undeposited", headers=headers).get_json()["items"]]
    lines = AccountingJournalLine.query.filter_by(journal_entry_id=second.get_json()["journal_entry_id"]).all()
    assert sum((line.debit for line in lines), Decimal("0")) == Decimal("1450.00")
    assert sum((line.credit for line in lines), Decimal("0")) == Decimal("1450.00")


def test_partial_deposit_controls_reject_invalid_amounts_atomically(app, client):
    admin, collector, account_id, bank_id, payment_id = _deposit_setup(app, client)
    headers = _headers(app, admin)
    for invalid in ("0", "-1"):
        response = client.post("/admin/collection-deposits/preview", headers=headers, json=_deposit_payload(
            collector, account_id, bank_id, payment_id,
            allocations=[{"payment_id": payment_id, "amount": invalid}],
        ))
        assert response.status_code == 422
    first = client.post("/admin/collection-deposits", headers=headers, json=_deposit_payload(
        collector, account_id, bank_id, payment_id,
        allocations=[{"payment_id": payment_id, "amount": "650.00"}],
    ))
    assert first.status_code == 201
    before = (CollectionDepositBatch.query.count(), CollectionDepositAllocation.query.count())

    excessive = client.post("/admin/collection-deposits", headers=headers, json=_deposit_payload(
        collector, account_id, bank_id, payment_id,
        allocations=[{"payment_id": payment_id, "amount": "1450.01"}],
    ))
    assert excessive.status_code == 422
    assert excessive.get_json() == {
        "error": "allocation_exceeds_undeposited_amount",
        "message": "Requested deposit amount exceeds the remaining undeposited balance.",
        "payment_id": payment_id, "amount_collected": "2100.00",
        "amount_deposited": "650.00", "undeposited_amount": "1450.00",
        "requested_amount": "1450.01",
    }
    duplicate = client.post("/admin/collection-deposits", headers=headers, json=_deposit_payload(
        collector, account_id, bank_id, payment_id,
        allocations=[{"payment_id": payment_id, "amount": "1000"}, {"payment_id": payment_id, "amount": "450"}],
    ))
    assert duplicate.status_code == 422
    assert (CollectionDepositBatch.query.count(), CollectionDepositAllocation.query.count()) == before


def test_partial_setting_and_reversed_batches_restore_available_balance(app, client):
    admin, collector, account_id, bank_id, payment_id = _deposit_setup(app, client)
    headers = _headers(app, admin)
    setting = AccountingSetting(setting_key="allow_partial_deposits", setting_value="false")
    db.session.add(setting); db.session.commit()
    partial = client.post("/admin/collection-deposits", headers=headers, json=_deposit_payload(
        collector, account_id, bank_id, payment_id,
        allocations=[{"payment_id": payment_id, "amount": "650"}],
    ))
    assert partial.status_code == 422
    setting.setting_value = "true"; db.session.commit()
    posted = client.post("/admin/collection-deposits", headers=headers, json=_deposit_payload(
        collector, account_id, bank_id, payment_id,
        allocations=[{"payment_id": payment_id, "amount": "650"}],
    ))
    assert posted.status_code == 201
    batch = db.session.get(CollectionDepositBatch, posted.get_json()["deposit_batch_id"])
    batch.status = "REVERSED"
    # Simulate the canonical reversal's cached-balance update while retaining its audit allocation.
    payment = db.session.get(Payment, payment_id)
    payment.deposited_amount = Decimal("0"); payment.deposit_status = "UNDEPOSITED"
    db.session.commit()
    full = client.post("/admin/collection-deposits", headers=headers, json=_deposit_payload(
        collector, account_id, bank_id, payment_id,
    ))
    assert full.status_code == 201


def test_cleared_sheet_payment_is_hidden_and_cannot_be_deposited_again(app, client):
    admin, collector, account_id, bank_id, payment_id = _deposit_setup(app, client)
    payment = db.session.get(__import__("app.models", fromlist=["Payment"]).Payment, payment_id)
    payment.collection_clearance_status = "CLEARED"
    db.session.commit()

    listing = client.get("/admin/collections/undeposited", headers=_headers(app, admin))
    assert listing.status_code == 200
    assert payment_id not in [row["payment_id"] for row in listing.get_json()["items"]]

    rejected = client.post("/admin/collection-deposits", headers=_headers(app, admin),
                           json=_deposit_payload(collector, account_id, bank_id, payment_id))
    assert rejected.status_code == 422
    assert "already cleared" in rejected.get_json()["message"]
