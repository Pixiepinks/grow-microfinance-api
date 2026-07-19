from datetime import date, datetime
from decimal import Decimal

from app.extensions import db
from app.loan_totals import loan_totals
from app.models import Customer, Loan, LoanLedger, Payment, User


def _loan():
    user = User(email="totals@example.com", name="Totals", role="admin")
    user.set_password("password")
    db.session.add(user); db.session.flush()
    customer = Customer(user_id=user.id, customer_code="TOTALS", full_name="Totals", nic_number="N", mobile="0", address="A")
    db.session.add(customer); db.session.flush()
    loan = Loan(loan_number="TOTALS-1", customer_id=customer.id, principal_amount=Decimal("25000"),
                interest_rate=Decimal("0"), total_days=30, payment_interval_days=30,
                daily_installment=Decimal("0"), total_payable=Decimal("41105.55"),
                start_date=date.today(), end_date=date.today(), created_by_id=user.id, status="SETTLED")
    db.session.add(loan); db.session.flush()
    ledger = LoanLedger(loan_id=loan.id, installment_no=1, due_date=date.today(), period_days=30,
                        opening_balance=Decimal("25000"), principal_amount=Decimal("25000"),
                        interest_amount=Decimal("6500"), installment_amount=Decimal("31500"), closing_balance=Decimal(),
                        principal_paid=Decimal("25000"), interest_paid=Decimal("6500"), delay_interest_paid=Decimal("1000"),
                        delay_interest_accrued=Decimal("9605.55"), waived_delay_interest_amount=Decimal("8605.55"))
    db.session.add(ledger)
    return loan, user


def test_cash_paid_excludes_delay_waiver_and_invalid_receipts(app):
    loan, user = _loan()
    db.session.add_all([
        Payment(loan_id=loan.id, collection_date=date.today(), amount_collected=Decimal("32500"), collected_by_id=user.id, status="POSTED"),
        Payment(loan_id=loan.id, collection_date=date.today(), amount_collected=Decimal("999"), collected_by_id=user.id, status="DRAFT"),
        Payment(loan_id=loan.id, collection_date=date.today(), amount_collected=Decimal("500"), collected_by_id=user.id, status="POSTED", reversed_at=datetime.utcnow()),
    ])
    db.session.commit()
    totals = loan_totals(loan)
    assert totals["total_paid"] == Decimal("32500.00")
    assert totals["cash_paid"] == Decimal("32500.00")
    assert totals["delay_interest_waived"] == Decimal("8605.55")
    assert totals["settlement_adjustments"] == Decimal("8605.55")
    assert totals["gross_satisfied_amount"] == Decimal("41105.55")
    assert totals["outstanding_amount"] == Decimal("0.00")


def test_normal_cash_settlement_has_no_adjustments(app):
    loan, user = _loan()
    loan.total_payable = Decimal("100.00")
    loan.ledger_entries[0].waived_delay_interest_amount = Decimal()
    db.session.add(Payment(loan_id=loan.id, collection_date=date.today(), amount_collected=Decimal("100"), collected_by_id=user.id, status="POSTED"))
    db.session.commit()
    totals = loan_totals(loan)
    assert totals["total_paid"] == Decimal("100.00")
    assert totals["settlement_adjustments"] == Decimal("0.00")
