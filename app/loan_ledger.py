from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
import calendar

from .currency import CURRENCY_CODE, format_currency
from .extensions import db
from .models import Loan, LoanLedger

CENT = Decimal("0.01")


def money(value) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def daily_interest_rate(loan: Loan) -> Decimal:
    return (Decimal(loan.interest_rate) / Decimal("100")) / Decimal("30")


def _add_month(d):
    month = d.month + 1
    year = d.year + (month - 1) // 12
    month = ((month - 1) % 12) + 1
    return d.replace(year=year, month=month, day=min(d.day, calendar.monthrange(year, month)[1]))


def _next_due_date(start_date, frequency, installment_no):
    if frequency == "DAILY":
        return start_date + timedelta(days=installment_no - 1)
    if frequency == "WEEKLY":
        return start_date + timedelta(days=(installment_no * 7) - 1)
    if frequency == "MONTHLY":
        due = start_date
        for _ in range(installment_no):
            due = _add_month(due)
        return due - timedelta(days=1)
    return None


def _generate_fixed_terms_ledger(loan: Loan):
    count = int(loan.number_of_installments or 0)
    if count <= 0:
        return []
    principal = money(loan.principal_amount)
    total_interest = money(loan.total_interest or (money(loan.total_repayment or 0) - principal))
    total_repayment = money(loan.total_repayment or (principal + total_interest))
    frequency = (loan.repayment_frequency or "").upper()

    principal_regular = money(principal / Decimal(count))
    interest_regular = money(total_interest / Decimal(count))
    opening_balance = principal
    period_start = loan.start_date
    principal_allocated = Decimal("0.00")
    interest_allocated = Decimal("0.00")
    entries = []

    for index in range(1, count + 1):
        is_last = index == count
        principal_amount = money(principal - principal_allocated) if is_last else principal_regular
        interest_amount = money(total_interest - interest_allocated) if is_last else interest_regular
        installment_amount = money(total_repayment - sum((e.installment_amount for e in entries), Decimal("0.00"))) if is_last else money(principal_amount + interest_amount)
        due_date = _next_due_date(loan.start_date, frequency, index)
        period_days = max((due_date - period_start).days + 1, 1) if due_date else 1
        closing_balance = money(opening_balance - principal_amount)
        if is_last:
            closing_balance = Decimal("0.00")
        entry = LoanLedger(
            loan_id=loan.id, installment_no=index, period_start_date=period_start,
            due_date=due_date, period_days=period_days, opening_balance=opening_balance,
            interest_amount=interest_amount, principal_amount=principal_amount,
            installment_amount=installment_amount, closing_balance=closing_balance,
            paid_amount=Decimal("0.00"), delay_days=0, delay_interest=Decimal("0.00"), status="PENDING")
        db.session.add(entry); entries.append(entry)
        principal_allocated += principal_amount; interest_allocated += interest_amount
        opening_balance = closing_balance; period_start = due_date + timedelta(days=1)
    loan.final_installment_due_date = entries[-1].due_date if entries else None
    loan.total_payable = total_repayment
    loan.daily_installment = money(total_repayment / Decimal(max(int(loan.total_days or loan.loan_days or count), 1)))
    return entries


def generate_loan_ledger(loan: Loan):
    """Create repayment ledger rows for a loan if they do not already exist."""
    existing_count = LoanLedger.query.filter_by(loan_id=loan.id).count() if loan.id else 0
    if existing_count:
        return list(loan.ledger_entries)

    if getattr(loan, "number_of_installments", None) and getattr(loan, "installment_amount", None):
        return _generate_fixed_terms_ledger(loan)

    interval = int(getattr(loan, "payment_interval_days", None) or 7)
    if interval <= 0:
        interval = 7
    total_days = int(loan.total_days)
    if total_days <= 0:
        return []
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
        if is_last: closing_balance = Decimal("0.00")
        entry = LoanLedger(loan_id=loan.id, installment_no=index, period_start_date=period_start, due_date=period_start + timedelta(days=period_days - 1), period_days=period_days, opening_balance=opening_balance, interest_amount=interest_amount, principal_amount=principal_amount, installment_amount=installment_amount, closing_balance=closing_balance, paid_amount=Decimal("0.00"), delay_days=0, delay_interest=Decimal("0.00"), status="PENDING")
        db.session.add(entry); entries.append(entry)
        opening_balance = closing_balance; period_start = period_start + timedelta(days=period_days)
    loan.final_installment_due_date = entries[-1].due_date if entries else None
    loan.total_payable = money(sum((e.installment_amount for e in entries), Decimal("0.00")))
    loan.daily_installment = money(loan.total_payable / Decimal(total_days))
    return entries


def ledger_totals(loan: Loan) -> dict:
    entries = list(loan.ledger_entries)
    total_principal = money(sum((Decimal(e.principal_amount) for e in entries), Decimal("0.00")))
    total_interest = money(sum((Decimal(e.interest_amount) for e in entries), Decimal("0.00")))
    total_delay_interest = money(sum((Decimal(e.delay_interest or 0) for e in entries), Decimal("0.00")))
    total_payable = money(total_principal + total_interest + total_delay_interest)
    total_paid = money(sum((Decimal(e.paid_amount or 0) for e in entries), Decimal("0.00")))
    return {"currency": CURRENCY_CODE,"total_principal": float(total_principal),"total_principal_formatted": format_currency(total_principal),"total_interest": float(total_interest),"total_interest_formatted": format_currency(total_interest),"total_payable": float(total_payable),"total_payable_formatted": format_currency(total_payable),"total_paid": float(total_paid),"total_paid_formatted": format_currency(total_paid),"outstanding": float(money(total_payable - total_paid)),"outstanding_formatted": format_currency(money(total_payable - total_paid)),"total_delay_interest": float(total_delay_interest),"total_delay_interest_formatted": format_currency(total_delay_interest)}
