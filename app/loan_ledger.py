from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from .models import LoanLedger

MONEY = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(MONEY, rounding=ROUND_HALF_UP)


def generate_repayment_schedule(
    loan, payment_interval_days: int = 7
) -> list[LoanLedger]:
    """Build repayment ledger rows for a newly disbursed loan."""
    interval = int(payment_interval_days or 7)
    if interval <= 0:
        interval = 7

    total_days = int(loan.total_days)
    full_periods, remaining_days = divmod(total_days, interval)
    period_lengths = [interval] * full_periods
    if remaining_days:
        period_lengths.append(remaining_days)
    if not period_lengths:
        period_lengths = [total_days or interval]

    opening_balance = Decimal(loan.principal_amount)
    daily_interest_rate = Decimal(loan.interest_rate) / Decimal("100") / Decimal("30")
    installment_count = Decimal(len(period_lengths))
    base_principal = money(Decimal(loan.principal_amount) / installment_count)
    entries = []
    period_start = loan.start_date

    for index, period_days in enumerate(period_lengths, start=1):
        if index == len(period_lengths):
            principal_amount = money(opening_balance)
        else:
            principal_amount = base_principal
        interest_amount = money(
            opening_balance * daily_interest_rate * Decimal(period_days)
        )
        installment_amount = money(principal_amount + interest_amount)
        closing_balance = money(opening_balance - principal_amount)
        entries.append(
            LoanLedger(
                loan=loan,
                installment_no=index,
                period_start_date=period_start,
                due_date=period_start + timedelta(days=period_days - 1),
                period_days=period_days,
                opening_balance=money(opening_balance),
                interest_amount=interest_amount,
                principal_amount=principal_amount,
                installment_amount=installment_amount,
                closing_balance=closing_balance,
                paid_amount=Decimal("0.00"),
                delay_days=0,
                delay_interest=Decimal("0.00"),
                status="PENDING",
            )
        )
        opening_balance = closing_balance
        period_start = period_start + timedelta(days=period_days)

    return entries


def ledger_totals(entries) -> dict:
    total_principal = sum((Decimal(e.principal_amount) for e in entries), Decimal("0"))
    total_interest = sum((Decimal(e.interest_amount) for e in entries), Decimal("0"))
    total_delay_interest = sum(
        (Decimal(e.delay_interest or 0) for e in entries), Decimal("0")
    )
    total_paid = sum((Decimal(e.paid_amount or 0) for e in entries), Decimal("0"))
    total_payable = total_principal + total_interest + total_delay_interest
    return {
        "total_principal": float(money(total_principal)),
        "total_interest": float(money(total_interest)),
        "total_payable": float(money(total_payable)),
        "total_paid": float(money(total_paid)),
        "outstanding": float(money(max(total_payable - total_paid, Decimal("0")))),
        "delay_interest": float(money(total_delay_interest)),
    }
