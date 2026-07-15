from datetime import date
from decimal import Decimal

from flask_jwt_extended import create_access_token

from app.accounting import seed_default_accounts
from app.extensions import db
from app.models import AccountingAccount, AccountingJournalEntry, Customer, Loan, Payment, User


def _user(role="admin"):
    u = User(email=f"{role}@example.com", name=role, role=role)
    u.set_password("password")
    db.session.add(u); db.session.commit(); return u


def _headers(app, user):
    with app.app_context():
        token = create_access_token(identity=str(user.id), additional_claims={"role": user.role})
    return {"Authorization": f"Bearer {token}"}


def _customer():
    u = _user("customer")
    c = Customer(user_id=u.id, customer_code="C-ACCT", full_name="Acct Customer")
    db.session.add(c); db.session.commit(); return c


def _loan(admin, customer):
    loan = Loan(loan_number="LN-ACCT", customer_id=customer.id, principal_amount=Decimal("10000.00"), interest_rate=Decimal("12.00"), total_days=30, payment_interval_days=30, daily_installment=Decimal("0.00"), total_payable=Decimal("10000.00"), start_date=date.today(), end_date=date.today(), status="Active", created_by_id=admin.id)
    db.session.add(loan); db.session.commit(); return loan


def test_chart_of_accounts_create_duplicate_invalid_and_inactive_posting(app, client):
    admin = _user("admin")
    seed_default_accounts(); db.session.commit()
    headers = _headers(app, admin)
    resp = client.post("/admin/accounting/accounts", headers=headers, json={"account_code":"9000","account_name":"Test Asset","account_type":"ASSET","normal_balance":"DEBIT"})
    assert resp.status_code == 201
    dup = client.post("/admin/accounting/accounts", headers=headers, json={"account_code":"9000","account_name":"Dup","account_type":"ASSET","normal_balance":"DEBIT"})
    assert dup.status_code == 400
    invalid = client.post("/admin/accounting/accounts", headers=headers, json={"account_code":"9001","account_name":"Bad","account_type":"BAD","normal_balance":"DEBIT"})
    assert invalid.status_code == 400
    acct = AccountingAccount.query.filter_by(account_code="9000").first(); acct.is_active = False; db.session.commit()
    bank = AccountingAccount.query.filter_by(account_code="1010").first()
    post = client.post("/admin/accounting/journals", headers=headers, json={"journal_date":date.today().isoformat(),"description":"bad inactive","status":"POSTED","lines":[{"account_id":acct.id,"debit":"10.00"},{"account_id":bank.id,"credit":"10.00"}]})
    assert post.status_code == 400


def test_manual_journal_post_and_reverse_swaps_lines(app, client):
    admin = _user("admin")
    seed_default_accounts(); db.session.commit()
    headers = _headers(app, admin)
    rent = AccountingAccount.query.filter_by(account_code="5010").first(); bank = AccountingAccount.query.filter_by(account_code="1010").first()
    resp = client.post("/admin/accounting/journals", headers=headers, json={"journal_date":date.today().isoformat(),"description":"rent","status":"POSTED","lines":[{"account_id":rent.id,"debit":"100.00"},{"account_id":bank.id,"credit":"100.00"}]})
    assert resp.status_code == 201
    body = resp.get_json(); assert body["total_debit"] == "100.00" and body["status"] == "POSTED"
    unbalanced = client.post("/admin/accounting/journals", headers=headers, json={"journal_date":date.today().isoformat(),"description":"bad","status":"POSTED","lines":[{"account_id":rent.id,"debit":"100.00"},{"account_id":bank.id,"credit":"99.00"}]})
    assert unbalanced.status_code == 400
    rev = client.post(f"/admin/accounting/journals/{body['id']}/reverse", headers=headers, json={"journal_date":date.today().isoformat(),"reason":"wrong"})
    assert rev.status_code == 201
    rbody = rev.get_json(); assert rbody["lines"][0]["credit"] == "100.00" and rbody["lines"][1]["debit"] == "100.00"


def test_loan_creation_posts_idempotent_disbursement_journal(app, client):
    admin = _user("admin"); customer = _customer(); headers = _headers(app, admin)
    resp = client.post("/admin/loans", headers=headers, json={"loan_number":"LN-DISB","customer_id":customer.id,"principal_amount":"12345.00","interest_rate":"10","total_days":30,"payment_interval_days":30,"start_date":date.today().isoformat(),"end_date":date.today().isoformat()})
    assert resp.status_code == 200
    loan_id = resp.get_json()["loan_id"]
    journals = AccountingJournalEntry.query.filter_by(reference_type="LOAN_DISBURSEMENT", reference_id=str(loan_id)).all()
    assert len(journals) == 1 and journals[0].total_debit == Decimal("12345.00") and journals[0].total_credit == Decimal("12345.00")
    assert {line.loan_id for line in journals[0].lines} == {loan_id}


def test_payment_posts_journal_and_general_ledger_csv(app, client):
    admin = _user("admin"); customer = _customer(); loan = _loan(admin, customer); headers = _headers(app, admin)
    resp = client.post("/staff/payments", headers=headers, json={"loan_id":loan.id,"amount_collected":"500.00","collection_date":date.today().isoformat(),"payment_method":"Cash"})
    assert resp.status_code == 200
    payment_id = resp.get_json()["payment_id"]
    payment = Payment.query.get(payment_id)
    journal = AccountingJournalEntry.query.filter_by(reference_type="LOAN_PAYMENT", reference_id=str(payment_id)).first()
    assert journal and journal.total_debit == journal.total_credit == Decimal("500.00")
    assert journal.lines[0].customer_id == customer.id and journal.lines[0].payment_id == payment_id
    cash = AccountingAccount.query.filter_by(account_code="1000").first()
    gl = client.get(f"/admin/accounting/general-ledger?account_id={cash.id}", headers=headers)
    assert gl.status_code == 200 and gl.get_json()["closing_balance"] == "500.00"
    csv = client.get(f"/admin/accounting/general-ledger/export.csv?account_id={cash.id}", headers=headers)
    assert csv.status_code == 200 and b"journal_no" in csv.data


def test_reconciliation_reports_missing_payment_journal(app, client):
    admin = _user("admin"); customer = _customer(); loan = _loan(admin, customer); headers = _headers(app, admin)
    payment = Payment(loan_id=loan.id, amount_collected=Decimal("10.00"), collection_date=date.today(), collected_by_id=admin.id)
    db.session.add(payment); db.session.commit()
    resp = client.get("/admin/accounting/reconciliation/issues", headers=headers)
    assert resp.status_code == 200
    assert any(i["type"] == "MISSING_LOAN_PAYMENT_JOURNAL" for i in resp.get_json()["issues"])


def _posted_journal(client, headers, journal_date, description, lines, reference_type=None):
    payload = {"journal_date": journal_date.isoformat(), "description": description, "status": "POSTED", "lines": lines}
    resp = client.post("/admin/accounting/journals", headers=headers, json=payload)
    assert resp.status_code == 201
    entry = AccountingJournalEntry.query.get(resp.get_json()["id"])
    if reference_type:
        entry.reference_type = reference_type
        db.session.commit()
    return entry


def test_general_ledger_disbursement_filters_balances_and_csv(app, client):
    from app.accounting import post_loan_disbursement

    admin = _user("admin"); customer = _customer(); loan = _loan(admin, customer); headers = _headers(app, admin)
    seed_default_accounts(); db.session.commit()
    post_loan_disbursement(loan, admin.id, disbursement_date=date(2026, 7, 10)); db.session.commit()
    receivable = AccountingAccount.query.filter_by(account_code="1100").first()
    bank = AccountingAccount.query.filter_by(account_code="1010").first()

    resp = client.get(
        f"/admin/accounting/general-ledger?account_id={receivable.id}&customer_id=&loan_id=&date_from=&date_to=&search=",
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["account"]["id"] == receivable.id
    assert body["account"]["account_code"] == "1100"
    assert body["opening_balance"] == "0.00"
    assert body["total_debit"] == "10000.00"
    assert body["total_credit"] == "0.00"
    assert body["closing_balance"] == "10000.00"
    assert len(body["transactions"]) == 1
    assert body["transactions"][0]["reference_type"] == "LOAN_DISBURSEMENT"
    assert body["transactions"][0]["customer_id"] == customer.id
    assert body["transactions"][0]["loan_id"] == loan.id

    by_code = client.get("/admin/accounting/general-ledger?account_code=1100", headers=headers)
    assert by_code.status_code == 200 and len(by_code.get_json()["transactions"]) == 1
    wrong_id = client.get("/admin/accounting/general-ledger?account_id=1100", headers=headers)
    assert wrong_id.status_code == 400

    customer_filtered = client.get(f"/admin/accounting/general-ledger?account_id={receivable.id}&customer_id={customer.id}", headers=headers)
    assert customer_filtered.status_code == 200 and len(customer_filtered.get_json()["transactions"]) == 1
    loan_filtered = client.get(f"/admin/accounting/general-ledger?account_id={receivable.id}&loan_id={loan.id}", headers=headers)
    assert loan_filtered.status_code == 200 and len(loan_filtered.get_json()["transactions"]) == 1

    bank_resp = client.get(f"/admin/accounting/general-ledger?account_id={bank.id}", headers=headers)
    assert bank_resp.status_code == 200
    assert bank_resp.get_json()["total_credit"] == "10000.00"

    csv = client.get(f"/admin/accounting/general-ledger/export.csv?account_id={receivable.id}", headers=headers)
    assert csv.status_code == 200
    assert b"LOAN_DISBURSEMENT" in csv.data


def test_general_ledger_draft_excluded_opening_and_running_balances(app, client):
    admin = _user("admin"); headers = _headers(app, admin)
    seed_default_accounts(); db.session.commit()
    rent = AccountingAccount.query.filter_by(account_code="5010").first()
    bank = AccountingAccount.query.filter_by(account_code="1010").first()
    income = AccountingAccount.query.filter_by(account_code="4000").first()

    _posted_journal(client, headers, date(2026, 1, 1), "opening rent", [{"account_id": rent.id, "debit":"25.00"}, {"account_id": bank.id, "credit":"25.00"}])
    _posted_journal(client, headers, date(2026, 1, 2), "period rent", [{"account_id": rent.id, "debit":"75.00"}, {"account_id": bank.id, "credit":"75.00"}])
    draft = client.post("/admin/accounting/journals", headers=headers, json={"journal_date":"2026-01-02","description":"draft excluded","status":"DRAFT","lines":[{"account_id":rent.id,"debit":"999.00"},{"account_id":bank.id,"credit":"999.00"}]})
    assert draft.status_code == 201

    resp = client.get(f"/admin/accounting/general-ledger?account_id={rent.id}&date_from=2026-01-02&date_to=2026-01-02", headers=headers)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["opening_balance"] == "25.00"
    assert body["total_debit"] == "75.00"
    assert body["closing_balance"] == "100.00"
    assert len(body["transactions"]) == 1
    assert body["transactions"][0]["running_balance"] == "100.00"

    _posted_journal(client, headers, date(2026, 1, 3), "income", [{"account_id": bank.id, "debit":"40.00"}, {"account_id": income.id, "credit":"40.00"}])
    credit_resp = client.get(f"/admin/accounting/general-ledger?account_id={income.id}", headers=headers)
    assert credit_resp.status_code == 200
    credit_body = credit_resp.get_json()
    assert credit_body["total_credit"] == "40.00"
    assert credit_body["closing_balance"] == "40.00"
    assert credit_body["transactions"][-1]["running_balance"] == "40.00"

    no_date = client.get(f"/admin/accounting/general-ledger?account_id={rent.id}", headers=headers)
    assert no_date.status_code == 200
    assert no_date.get_json()["opening_balance"] == "0.00"
    assert len(no_date.get_json()["transactions"]) == 2


def _post_balanced_journal(client, headers, debit_account, credit_account):
    resp = client.post("/admin/accounting/journals", headers=headers, json={
        "journal_date": date.today().isoformat(),
        "description": "posted account activity",
        "status": "POSTED",
        "lines": [
            {"account_id": debit_account.id, "debit": "10.00"},
            {"account_id": credit_account.id, "credit": "10.00"},
        ],
    })
    assert resp.status_code == 201


def test_account_update_rename_ordinary_income_account(app, client):
    admin = _user("admin"); seed_default_accounts(); db.session.commit(); headers = _headers(app, admin)
    acct = AccountingAccount.query.filter_by(account_code="4030").first()
    resp = client.patch(f"/admin/accounting/accounts/{acct.id}", headers=headers, json={"account_name": "Updated Documentation Fee Income"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["account_name"] == "Updated Documentation Fee Income"
    assert body["account_role"] == "DOCUMENTATION_FEE_INCOME"


def test_account_update_edit_expense_account_name(app, client):
    admin = _user("admin"); seed_default_accounts(); db.session.commit(); headers = _headers(app, admin)
    acct = AccountingAccount.query.filter_by(account_code="5010").first()
    resp = client.patch(f"/admin/accounting/accounts/{acct.id}", headers=headers, json={"account_name": "Updated Rent Expense"})
    assert resp.status_code == 200
    assert resp.get_json()["account_name"] == "Updated Rent Expense"


def test_account_update_blocks_type_change_after_posted_transactions(app, client):
    admin = _user("admin"); seed_default_accounts(); db.session.commit(); headers = _headers(app, admin)
    income = AccountingAccount.query.filter_by(account_code="4030").first()
    cash = AccountingAccount.query.filter_by(account_code="1000").first()
    _post_balanced_journal(client, headers, cash, income)
    resp = client.patch(f"/admin/accounting/accounts/{income.id}", headers=headers, json={"account_type": "EXPENSE"})
    assert resp.status_code == 422
    assert resp.get_json()["error"] == "Account structure change blocked"


def test_account_update_collection_account_without_collector_is_rejected(app, client):
    admin = _user("admin"); seed_default_accounts(); db.session.commit(); headers = _headers(app, admin)
    parent = AccountingAccount.query.filter_by(account_code="1050").first()
    acct = AccountingAccount(account_code="1058", account_name="Collector Clearing", account_type="ASSET", normal_balance="DEBIT", account_subtype="COLLECTION_CLEARING", is_collection_account=True, collector_id=admin.id, parent_account_id=parent.id, allow_manual_posting=True)
    db.session.add(acct); db.session.commit()
    resp = client.patch(f"/admin/accounting/accounts/{acct.id}", headers=headers, json={"collector_id": None})
    assert resp.status_code == 422


def test_account_update_assign_collector_to_ordinary_income_is_rejected(app, client):
    admin = _user("admin"); seed_default_accounts(); db.session.commit(); headers = _headers(app, admin)
    acct = AccountingAccount.query.filter_by(account_code="4030").first()
    resp = client.patch(f"/admin/accounting/accounts/{acct.id}", headers=headers, json={"collector_id": admin.id})
    assert resp.status_code == 422


def test_account_update_duplicate_account_code_is_rejected(app, client):
    admin = _user("admin"); seed_default_accounts(); db.session.commit(); headers = _headers(app, admin)
    acct = AccountingAccount.query.filter_by(account_code="4030").first()
    resp = client.patch(f"/admin/accounting/accounts/{acct.id}", headers=headers, json={"account_code": " 1000 "})
    assert resp.status_code == 422
    assert resp.get_json()["error"] == "Duplicate account code"


def test_account_serializer_includes_editor_metadata(app, client):
    admin = _user("admin"); seed_default_accounts(); db.session.commit(); headers = _headers(app, admin)
    acct = AccountingAccount.query.filter_by(account_code="4030").first()
    resp = client.get(f"/admin/accounting/accounts/{acct.id}", headers=headers)
    assert resp.status_code == 200
    body = resp.get_json()
    for key in ["account_subtype", "account_role", "is_collection_account", "collector_id", "parent_account_id", "has_posted_transactions"]:
        assert key in body
