from datetime import date
from decimal import Decimal

from flask_jwt_extended import create_access_token

from app.accounting import seed_default_accounts
from app.extensions import db
from app.models import AccountingJournalEntry, Customer, CustomerCreditBalance, Loan, LoanLedger, Payment, User


def _setup(app, delay="144.44", waived="0.00", status="SETTLED"):
    admin = User(email=f"admin-{delay}-{waived}@example.com", name="Admin", role="admin")
    admin.set_password("password")
    customer_user = User(email=f"customer-{delay}-{waived}@example.com", name="Customer", role="customer")
    customer_user.set_password("password")
    db.session.add_all([admin, customer_user]); db.session.flush()
    customer = Customer(user_id=customer_user.id, customer_code=f"C-{delay}-{waived}", full_name="Customer")
    db.session.add(customer); db.session.flush()
    loan = Loan(loan_number=f"PS-{delay}-{waived}", customer_id=customer.id, principal_amount=Decimal("18000"),
        interest_rate=Decimal("5"), total_days=30, payment_interval_days=30, daily_installment=Decimal("630"),
        total_payable=Decimal("18900"), start_date=date.today(), end_date=date.today(), status=status, created_by_id=admin.id)
    db.session.add(loan); db.session.flush()
    db.session.add(LoanLedger(loan_id=loan.id, installment_no=1, due_date=date.today(), period_days=30,
        opening_balance=Decimal("18000"), principal_amount=Decimal("18000"), principal_paid=Decimal("18000"),
        interest_amount=Decimal("900"), interest_paid=Decimal("900"), installment_amount=Decimal("18900"),
        closing_balance=Decimal("0"), delay_interest=Decimal(delay), delay_interest_accrued=Decimal(delay),
        delay_interest_waived=Decimal(waived), status="PAID"))
    seed_default_accounts()
    if not __import__("app.models", fromlist=["AccountingAccount"]).AccountingAccount.query.filter_by(account_code="2250").first():
        from app.models import AccountingAccount
        db.session.add(AccountingAccount(account_code="2250", account_name="Customer Advances", account_type="LIABILITY", normal_balance="CREDIT", account_subtype="CUSTOMER_ADVANCE", is_active=True, allow_manual_posting=True))
    db.session.commit()
    with app.app_context():
        token = create_access_token(identity=str(admin.id), additional_claims={"role": "admin"})
    return loan, {"Authorization": f"Bearer {token}", "Idempotency-Key": f"key-{delay}-{waived}"}


def test_post_settlement_payment_allocates_delay_then_credit_and_is_idempotent(app, client):
    loan, headers = _setup(app)
    payload = {"amount": "1000.00", "payment_date": date.today().isoformat(), "payment_method": "CASH", "reference_number": "PS-001"}
    first = client.post(f"/admin/loans/{loan.id}/post-settlement-payment", headers=headers, json=payload)
    assert first.status_code == 201, first.get_json()
    assert first.get_json()["delay_interest_paid"] == "144.44"
    assert first.get_json()["customer_credit_created"] == "855.56"
    assert first.get_json()["loan_status"] == "SETTLED"
    duplicate = client.post(f"/admin/loans/{loan.id}/post-settlement-payment", headers=headers, json=payload)
    assert duplicate.status_code == 200
    assert duplicate.get_json()["payment_id"] == first.get_json()["payment_id"]
    assert Payment.query.filter_by(transaction_type="POST_SETTLEMENT_PAYMENT").count() == 1
    assert CustomerCreditBalance.query.filter_by(source_type="POST_SETTLEMENT_PAYMENT").one().available_amount == Decimal("855.56")
    journal = AccountingJournalEntry.query.get(first.get_json()["journal_entry_id"])
    assert journal.total_debit == journal.total_credit == Decimal("1000.00")
    assert Loan.query.get(loan.id).status == "SETTLED"


def test_post_settlement_partial_delay_and_fully_waived_credit(app, client):
    partial, headers = _setup(app, delay="500.00")
    response = client.post(f"/admin/loans/{partial.id}/post-settlement-payment", headers=headers,
        json={"amount": "200", "reference_number": "PARTIAL"})
    assert response.status_code == 201
    assert response.get_json()["delay_interest_paid"] == "200.00"
    assert response.get_json()["delay_interest_outstanding"] == "300.00"
    assert response.get_json()["customer_credit_created"] == "0.00"

    waived, waived_headers = _setup(app, delay="500.00", waived="500.00")
    response = client.post(f"/admin/loans/{waived.id}/post-settlement-payment", headers=waived_headers,
        json={"amount": "1000", "reference_number": "WAIVED"})
    assert response.status_code == 201, response.get_json()
    assert response.get_json()["delay_interest_paid"] == "0.00"
    assert response.get_json()["customer_credit_created"] == "1000.00"


def test_post_settlement_endpoint_rejects_active_loan(app, client):
    loan, headers = _setup(app, status="ACTIVE")
    response = client.post(f"/admin/loans/{loan.id}/post-settlement-payment", headers=headers,
        json={"amount": "100", "reference_number": "ACTIVE"})
    assert response.status_code == 409
    assert "normal Record Payment" in response.get_json()["message"]
