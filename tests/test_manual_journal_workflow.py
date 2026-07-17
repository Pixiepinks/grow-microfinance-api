from datetime import date
from decimal import Decimal

from flask_jwt_extended import create_access_token

from app.accounting import seed_default_accounts
from app.extensions import db
from app.models import AccountingAccount, AccountingJournalEntry, Customer, Loan, User


def _user(role="admin"):
    u = User(email=f"manual-{role}@example.com", name=role, role=role)
    u.set_password("password")
    db.session.add(u); db.session.commit(); return u


def _headers(app, user):
    with app.app_context():
        token = create_access_token(identity=str(user.id), additional_claims={"role": user.role})
    return {"Authorization": f"Bearer {token}"}


def _customer_and_loan(admin):
    cu = _user("customer")
    customer = Customer(user_id=cu.id, customer_code="GROW-CUS-000001", full_name="Customer Name", status="Active")
    db.session.add(customer); db.session.commit()
    loan = Loan(loan_number="GROW-LOAN-000001", customer_id=customer.id, principal_amount=Decimal("15000.00"), interest_rate=Decimal("12.00"), total_days=30, payment_interval_days=30, daily_installment=Decimal("0.00"), total_payable=Decimal("15000.00"), start_date=date.today(), end_date=date.today(), status="ACTIVE", created_by_id=admin.id)
    db.session.add(loan); db.session.commit()
    return customer, loan


def test_manual_journal_without_customer_or_loan_posts(app, client):
    admin = _user("admin"); seed_default_accounts(); db.session.commit(); headers = _headers(app, admin)
    rent = AccountingAccount.query.filter_by(account_code="5010").one(); bank = AccountingAccount.query.filter_by(account_code="1010").one()
    resp = client.post("/admin/accounting/journal-entries/post", headers=headers, json={"journal_date": date.today().isoformat(), "reference": "Rent", "description": "Rent Expense", "lines": [{"account_id": rent.id, "debit_amount": 50000, "credit_amount": 0, "customer_id": None, "loan_id": None}, {"account_id": bank.id, "debit_amount": 0, "credit_amount": 50000, "customer_id": None, "loan_id": None}]})
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["status"] == "POSTED"
    assert all(line["customer_id"] is None and line["loan_id"] is None for line in body["lines"])


def test_manual_journal_loan_derives_customer_and_mismatch_422(app, client):
    admin = _user("admin"); seed_default_accounts(); db.session.commit(); headers = _headers(app, admin)
    customer, loan = _customer_and_loan(admin)
    suspense = AccountingAccount.query.filter_by(account_code="1990").one(); principal = AccountingAccount.query.filter_by(account_code="1100").one()
    payload = {"journal_date": date.today().isoformat(), "description": "Loan adjustment", "lines": [{"account_id": suspense.id, "debit_amount": 100, "credit_amount": 0}, {"account_id": principal.id, "debit_amount": 0, "credit_amount": 100, "loan_id": loan.id}]}
    resp = client.post("/admin/accounting/journal-entries/post", headers=headers, json=payload)
    assert resp.status_code == 201
    assert resp.get_json()["lines"][1]["customer_id"] == customer.id

    other_user = _user("customer2")
    other = Customer(user_id=other_user.id, customer_code="GROW-CUS-000002", full_name="Other", status="Active")
    db.session.add(other); db.session.commit()
    payload["lines"][1]["customer_id"] = other.id
    mismatch = client.post("/admin/accounting/journal-entries/post", headers=headers, json=payload)
    assert mismatch.status_code == 422
    assert mismatch.get_json()["error"] == "loan_customer_mismatch"


def test_draft_excluded_from_general_ledger_until_posted(app, client):
    admin = _user("admin"); seed_default_accounts(); db.session.commit(); headers = _headers(app, admin)
    rent = AccountingAccount.query.filter_by(account_code="5010").one(); bank = AccountingAccount.query.filter_by(account_code="1010").one()
    draft = client.post("/admin/accounting/journal-entries", headers=headers, json={"journal_date": date.today().isoformat(), "description": "Draft", "status": "DRAFT", "lines": [{"account_id": rent.id, "debit_amount": 25}, {"account_id": bank.id, "credit_amount": 25}]})
    assert draft.status_code == 201
    assert draft.get_json()["status"] == "DRAFT"
    gl = client.get(f"/admin/accounting/general-ledger?account_id={rent.id}", headers=headers)
    assert gl.status_code == 200
    assert gl.get_json()["transactions"] == []
    posted = client.post(f"/admin/accounting/journal-entries/{draft.get_json()['id']}/post", headers=headers)
    assert posted.status_code == 200
    assert AccountingJournalEntry.query.get(draft.get_json()["id"]).status == "POSTED"
