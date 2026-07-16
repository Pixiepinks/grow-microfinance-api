from datetime import date
from decimal import Decimal

from app.extensions import db
from app.accounting import seed_default_accounts
from app.investor_funding import (
    create_agreement,
    create_investor,
    record_funding,
    principal_repayment,
    calculate_investor_interest,
    post_investor_interest_accrual,
    pay_interest,
)
from app.models import AccountingJournalEntry, InvestorInterestAccrual


def setup_investor_agreement():
    seed_default_accounts()
    inv = create_investor({"full_name": "Investor A"})
    db.session.flush()
    agr = create_agreement({"investor_id": inv.id, "agreement_date": "2026-07-01", "start_date": "2026-07-01", "interest_rate": "2"})
    db.session.flush()
    return inv, agr


def test_initial_funding_posts_bank_debit_and_liability_credit(app):
    with app.app_context():
        _, agr = setup_investor_agreement()
        tx = record_funding(agr.id, {"transaction_date": "2026-07-01", "amount": "1000000", "reference": "INV-FUND-001"})
        db.session.commit()
        journal = AccountingJournalEntry.query.get(tx.journal_entry_id)
        assert agr.current_principal_balance == Decimal("1000000.00")
        assert sum(line.debit for line in journal.lines) == Decimal("1000000.00")
        assert sum(line.credit for line in journal.lines) == Decimal("1000000.00")
        assert journal.lines[0].account_id == agr.funding_account_id
        assert journal.lines[1].account_id == agr.investor_liability_account_id


def test_full_month_and_mid_month_average_daily_interest(app):
    with app.app_context():
        _, agr = setup_investor_agreement()
        record_funding(agr.id, {"transaction_date": "2026-07-01", "amount": "1000000"})
        calc = calculate_investor_interest(agr.id, date(2026, 7, 1), date(2026, 7, 31))
        assert calc["gross_interest_amount"] == Decimal("20000.00")
        record_funding(agr.id, {"transaction_date": "2026-07-16", "amount": "500000"})
        calc = calculate_investor_interest(agr.id, date(2026, 7, 1), date(2026, 7, 31))
        assert calc["average_daily_balance"] == Decimal("1258064.52")
        assert calc["gross_interest_amount"] == Decimal("25161.29")


def test_principal_repayment_and_duplicate_accrual_prevention(app):
    with app.app_context():
        _, agr = setup_investor_agreement()
        record_funding(agr.id, {"transaction_date": "2026-07-01", "amount": "1000000"})
        tx = principal_repayment(agr.id, {"transaction_date": "2026-07-15", "amount": "200000"})
        assert agr.current_principal_balance == Decimal("800000.00")
        journal = AccountingJournalEntry.query.get(tx.journal_entry_id)
        assert journal.lines[0].account_id == agr.investor_liability_account_id
        assert journal.lines[0].debit == Decimal("200000.00")
        accrual = post_investor_interest_accrual(agr.id, date(2026, 7, 1), date(2026, 7, 31))
        again = post_investor_interest_accrual(agr.id, date(2026, 7, 1), date(2026, 7, 31))
        assert accrual.id == again.id
        assert InvestorInterestAccrual.query.count() == 1


def test_interest_payment_clears_payable_to_bank(app):
    with app.app_context():
        _, agr = setup_investor_agreement()
        record_funding(agr.id, {"transaction_date": "2026-07-01", "amount": "1000000"})
        accrual = post_investor_interest_accrual(agr.id, date(2026, 7, 1), date(2026, 7, 31))
        paid = pay_interest(accrual.id, {"payment_date": "2026-08-05", "amount": "20000"})
        assert paid.status == "PAID"
        journal = AccountingJournalEntry.query.get(paid.payment_journal_entry_id)
        assert journal.lines[0].account_id == agr.accrued_interest_payable_account_id
        assert journal.lines[0].debit == Decimal("20000.00")
        assert journal.lines[1].account_id == agr.funding_account_id
        assert journal.lines[1].credit == Decimal("20000.00")
