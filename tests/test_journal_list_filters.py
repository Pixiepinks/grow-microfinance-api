from datetime import date
from decimal import Decimal

from flask_jwt_extended import create_access_token

from app.extensions import db
from app.models import AccountingAccount, AccountingJournalEntry, AccountingJournalLine, Customer, Loan, User


def _headers(app, user):
    with app.app_context():
        token = create_access_token(identity=str(user.id), additional_claims={"role": "admin"})
    return {"Authorization": f"Bearer {token}"}


def _journal(number, journal_date, status, account, customer, loan, description, source="LOAN_PAYMENT"):
    entry = AccountingJournalEntry(journal_no=number, journal_date=journal_date, accounting_date=journal_date, description=description, status=status, source_type=source, reference_type=source, total_debit=Decimal("10"), total_credit=Decimal("10"))
    db.session.add(entry); db.session.flush()
    # Two matching lines verify the account EXISTS predicate cannot duplicate an entry.
    for line_no in (1, 2):
        db.session.add(AccountingJournalLine(journal_entry_id=entry.id, line_no=line_no, account_id=account.id, debit=Decimal("10") if line_no == 1 else Decimal("0"), credit=Decimal("0") if line_no == 1 else Decimal("10"), customer_id=customer.id, loan_id=loan.id))
    return entry


def test_journal_list_filters_are_server_side_and_composable(app, client):
    admin = User(email="journal-admin@example.com", name="Admin", role="admin"); admin.set_password("password")
    customer_user = User(email="journal-customer@example.com", name="Customer", role="customer"); customer_user.set_password("password")
    db.session.add_all([admin, customer_user]); db.session.flush()
    customer = Customer(user_id=customer_user.id, customer_code="CUST-100", full_name="Interest Customer", status="Active")
    account = AccountingAccount(account_code="4000", account_name="Interest Income", account_type="INCOME", normal_balance="CREDIT")
    other_account = AccountingAccount(account_code="5000", account_name="Other", account_type="EXPENSE", normal_balance="DEBIT")
    db.session.add_all([customer, account, other_account]); db.session.flush()
    loan = Loan(loan_number="LOAN-15", customer_id=customer.id, principal_amount=Decimal("100"), interest_rate=Decimal("1"), total_days=1, daily_installment=Decimal("100"), total_payable=Decimal("101"), start_date=date(2026, 7, 1), end_date=date(2026, 7, 2), created_by_id=admin.id)
    db.session.add(loan); db.session.flush()
    matching = _journal("J-INTEREST", date(2026, 7, 10), "POSTED", account, customer, loan, "Interest received")
    _journal("J-DRAFT", date(2026, 7, 11), "DRAFT", account, customer, loan, "Interest draft")
    _journal("J-OUTSIDE", date(2026, 7, 20), "POSTED", other_account, customer, loan, "Outside range")
    db.session.commit()

    headers = _headers(app, admin)
    response = client.get(f"/admin/accounting/journal-entries?date_from=2026-07-10&date_to=2026-07-10&status= posted &reference_type=loan_payment&account_id={account.id}&customer_id={customer.id}&loan_id={loan.id}&search=interest&page=1&page_size=25", headers=headers)
    assert response.status_code == 200
    body = response.get_json()
    assert [item["id"] for item in body["items"]] == [matching.id]
    assert body["pagination"] == {"page": 1, "page_size": 25, "total_items": 1, "total_pages": 1, "has_next": False, "has_previous": False}
    assert body["items"][0]["journal_number"] == "J-INTEREST"

    empty = client.get("/admin/accounting/journal-entries?search=no-match", headers=headers)
    assert empty.status_code == 200
    assert empty.get_json()["items"] == []
    assert empty.get_json()["pagination"]["total_pages"] == 0


def test_journal_list_rejects_invalid_date_ranges_and_ids(app, client):
    admin = User(email="invalid-filter@example.com", name="Admin", role="admin"); admin.set_password("password")
    db.session.add(admin); db.session.commit(); headers = _headers(app, admin)
    invalid_date = client.get("/admin/accounting/journal-entries?date_from=2026-07-20&date_to=2026-07-01", headers=headers)
    assert invalid_date.status_code == 422
    assert invalid_date.get_json() == {"error": "invalid_date_range", "message": "Date From cannot be later than Date To."}
    invalid_id = client.get("/admin/accounting/journal-entries?account_id=not-an-id", headers=headers)
    assert invalid_id.status_code == 422
