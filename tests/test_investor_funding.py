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
    agr = create_agreement({"investor_id": inv.id, "agreement_date": "2026-07-01", "start_date": "2026-07-01", "interest_rate": "2", "status": "ACTIVE"})
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


def test_historical_catch_up_starts_from_posted_funding_and_skips_incomplete_month(app):
    from app.investor_funding import catch_up_investor_interest

    with app.app_context():
        seed_default_accounts()
        inv = create_investor({"full_name": "Historical Investor"})
        db.session.flush()
        agr = create_agreement({"investor_id": inv.id, "agreement_date": "2026-02-20", "start_date": "2026-02-20", "interest_rate": "2", "status": "ACTIVE"})
        db.session.flush()

        no_funding = catch_up_investor_interest(agr.id, date(2026, 7, 18), post=False)
        assert no_funding["skipped_periods"][0]["reason"] == "No posted investor funding transaction exists."

        record_funding(agr.id, {"transaction_date": "2026-02-20", "amount": "50000"})
        result = catch_up_investor_interest(agr.id, date(2026, 7, 18), post=True)
        db.session.commit()

        assert [p["period_start"] for p in result["created_periods"]] == ["2026-02-20", "2026-03-01", "2026-04-01", "2026-05-01", "2026-06-01"]
        assert [Decimal(str(p["interest"])).quantize(Decimal("0.01")) for p in result["created_periods"]] == [Decimal("321.43"), Decimal("1000.00"), Decimal("1000.00"), Decimal("1000.00"), Decimal("1000.00")]
        assert InvestorInterestAccrual.query.count() == 5
        assert sum((a.gross_interest_amount for a in InvestorInterestAccrual.query.all()), Decimal("0.00")) == Decimal("4321.43")

        second = catch_up_investor_interest(agr.id, date(2026, 7, 18), post=True)
        db.session.commit()
        assert second["created_periods"] == []
        assert len(second["existing_periods"]) == 5
        assert InvestorInterestAccrual.query.count() == 5


def test_catch_up_uses_actual_funding_date_not_agreement_start(app):
    from app.investor_funding import catch_up_investor_interest

    with app.app_context():
        seed_default_accounts()
        inv = create_investor({"full_name": "Later Funded Investor"})
        db.session.flush()
        agr = create_agreement({"investor_id": inv.id, "agreement_date": "2026-02-20", "start_date": "2026-02-20", "interest_rate": "2", "status": "ACTIVE"})
        db.session.flush()
        record_funding(agr.id, {"transaction_date": "2026-03-15", "amount": "50000"})

        result = catch_up_investor_interest(agr.id, date(2026, 7, 18), post=False)
        assert result["funding_start_date"] == "2026-03-15"
        assert result["created_periods"][0]["period_start"] == "2026-03-15"
        assert result["created_periods"][0]["period_end"] == "2026-03-31"
        assert Decimal(str(result["created_periods"][0]["interest"])).quantize(Decimal("0.01")) == Decimal("548.39")
        assert all(not p["period_start"].startswith("2026-02") for p in result["created_periods"])
