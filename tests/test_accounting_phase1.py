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
