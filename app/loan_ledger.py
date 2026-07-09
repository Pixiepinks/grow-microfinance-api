from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from .extensions import db
from .models import Loan, LoanLedger

CENT = Decimal("0.01")


def money(value) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def daily_interest_rate(loan: Loan) -> Decimal:
    return (Decimal(loan.interest_rate) / Decimal("100")) / Decimal("30")


def generate_loan_ledger(loan: Loan):
    """Create repayment ledger rows for a loan if they do not already exist."""
    interval = int(getattr(loan, "payment_interval_days", None) or 7)
    if interval <= 0:
        interval = 7
    total_days = int(loan.total_days)
    if total_days <= 0:
        return []

    existing_count = (
        LoanLedger.query.filter_by(loan_id=loan.id).count() if loan.id else 0
    )
    if existing_count:
        return list(loan.ledger_entries)

    full_periods, remainder = divmod(total_days, interval)
    period_lengths = [interval] * full_periods
    if remainder:
        period_lengths.append(remainder)

    installment_count = len(period_lengths)
    principal = Decimal(loan.principal_amount)
    principal_per_installment = money(principal / Decimal(installment_count))
    opening_balance = money(principal)
    rate = daily_interest_rate(loan)
    period_start = loan.start_date
    entries = []

    for index, period_days in enumerate(period_lengths, start=1):
        is_last = index == installment_count
        principal_amount = opening_balance if is_last else principal_per_installment
        interest_amount = money(opening_balance * rate * Decimal(period_days))
        installment_amount = money(principal_amount + interest_amount)
        closing_balance = money(opening_balance - principal_amount)
        if is_last:
            closing_balance = Decimal("0.00")

        entry = LoanLedger(
            loan_id=loan.id,
            installment_no=index,
            period_start_date=period_start,
            due_date=period_start + timedelta(days=period_days - 1),
            period_days=period_days,
            opening_balance=opening_balance,
            interest_amount=interest_amount,
            principal_amount=principal_amount,
            installment_amount=installment_amount,
            closing_balance=closing_balance,
            paid_amount=Decimal("0.00"),
            delay_days=0,
            delay_interest=Decimal("0.00"),
            status="PENDING",
        )
        db.session.add(entry)
        entries.append(entry)
        opening_balance = closing_balance
        period_start = period_start + timedelta(days=period_days)

    loan.total_payable = money(
        sum((e.installment_amount for e in entries), Decimal("0.00"))
    )
    loan.daily_installment = money(loan.total_payable / Decimal(total_days))
    return entries


def ledger_totals(loan: Loan) -> dict:
    entries = list(loan.ledger_entries)
    total_principal = money(
        sum((Decimal(e.principal_amount) for e in entries), Decimal("0.00"))
    )
    total_interest = money(
        sum((Decimal(e.interest_amount) for e in entries), Decimal("0.00"))
    )
    total_delay_interest = money(
        sum((Decimal(e.delay_interest or 0) for e in entries), Decimal("0.00"))
    )
    total_payable = money(total_principal + total_interest + total_delay_interest)
    total_paid = money(
        sum((Decimal(e.paid_amount or 0) for e in entries), Decimal("0.00"))
    )
    return {
        "total_principal": float(total_principal),
        "total_interest": float(total_interest),
        "total_payable": float(total_payable),
        "total_paid": float(total_paid),
        "outstanding": float(money(total_payable - total_paid)),
        "total_delay_interest": float(total_delay_interest),
    }
