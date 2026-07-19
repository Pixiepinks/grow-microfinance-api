from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
import calendar

from .currency import CURRENCY_CODE, format_currency
from .extensions import db
from .models import Loan, LoanLedger
from .loan_terms import resolve_loan_term

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
    count = int(loan.installment_count or loan.number_of_installments or 0)
    if count <= 0:
        return []
    principal = money(loan.principal_amount)
    total_interest = money(loan.total_interest or (money(loan.total_repayment or 0) - principal))
    total_repayment = money(loan.total_repayment or (principal + total_interest))
    frequency = (loan.repayment_frequency or "").upper()

    term_type = getattr(loan, "term_type", None) or ("DAYS" if loan.loan_days else None) or ("MONTHS" if getattr(loan, "tenure_months", None) else None)
    term_value = getattr(loan, "term_value", None) or loan.loan_days or getattr(loan, "tenure_months", None)
    resolved = resolve_loan_term(loan.start_date, term_type, term_value, frequency)
    periods = resolved.installment_periods
    count = resolved.installment_count

    principal_regular = money(principal / Decimal(count))
    interest_regular = money(total_interest / Decimal(count))
    installment_regular = money(total_repayment / Decimal(count))
    opening_balance = principal
    principal_allocated = Decimal("0.00")
    interest_allocated = Decimal("0.00")
    installment_allocated = Decimal("0.00")
    entries = []

    for period in periods:
        is_last = period.installment_no == count
        principal_amount = money(principal - principal_allocated) if is_last else principal_regular
        interest_amount = money(total_interest - interest_allocated) if is_last else interest_regular
        installment_amount = money(total_repayment - installment_allocated) if is_last else installment_regular
        closing_balance = money(opening_balance - principal_amount)
        if is_last:
            closing_balance = Decimal("0.00")
        entry = LoanLedger(
            loan_id=loan.id, installment_no=period.installment_no, period_start_date=period.period_start,
            due_date=period.due_date, period_days=period.days_in_period, opening_balance=opening_balance,
            interest_amount=interest_amount, principal_amount=principal_amount,
            installment_amount=installment_amount, closing_balance=closing_balance,
            paid_amount=Decimal("0.00"), delay_days=0, delay_interest=Decimal("0.00"), status="PENDING")
        db.session.add(entry); entries.append(entry)
        principal_allocated += principal_amount; interest_allocated += interest_amount; installment_allocated += installment_amount
        opening_balance = closing_balance
    loan.final_installment_due_date = entries[-1].due_date if entries else None
    loan.maturity_date = resolved.maturity_date
    loan.end_date = resolved.maturity_date
    loan.total_days = resolved.total_days
    loan.number_of_installments = count
    loan.installment_count = count
    loan.total_payable = total_repayment
    loan.daily_installment = money(total_repayment / Decimal(max(int(loan.total_days or count), 1)))
    return entries


def _ordered_entries(loan: Loan):
    return sorted(list(loan.ledger_entries), key=lambda e: (e.installment_no or 0, e.due_date))

def infer_frequency_from_ledger(entries) -> str | None:
    if not entries:
        return None
    days = [int(e.period_days) for e in entries if e.period_days]
    if days and len(days) == len(entries) and all(day == 1 for day in days):
        return "DAILY"
    if days and len(days) == len(entries) and all(day == 7 for day in days):
        return "WEEKLY"
    due_dates = [e.due_date for e in entries if e.due_date]
    if len(due_dates) == len(entries) and len(due_dates) > 1:
        current = due_dates[0]
        monthly = True
        for nxt in due_dates[1:]:
            current = _add_month(current)
            if nxt != current:
                monthly = False
                break
        if monthly:
            return "MONTHLY"
    return None

def derive_loan_metadata_from_ledger(loan: Loan) -> dict:
    entries = _ordered_entries(loan)
    if not entries:
        return {}
    total_days = sum(int(e.period_days or 0) for e in entries)
    first = entries[0]
    start_dates = [e.period_start_date for e in entries if e.period_start_date]
    start_date = min(start_dates) if start_dates else (first.due_date - timedelta(days=int(first.period_days or 0)) if first.due_date and first.period_days else None)
    maturity_date = max((e.due_date for e in entries if e.due_date), default=None)
    frequency = infer_frequency_from_ledger(entries)
    return {
        "installment_count": len(entries),
        "number_of_installments": len(entries),
        "total_days": total_days or None,
        "term_type": "DAYS" if total_days else None,
        "term_value": total_days or None,
        "loan_days": total_days or None,
        "repayment_frequency": frequency,
        "start_date": start_date,
        "maturity_date": maturity_date,
        "end_date": maturity_date,
        "final_installment_due_date": maturity_date,
    }

def loan_config_summary(loan: Loan) -> dict:
    derived = derive_loan_metadata_from_ledger(loan)
    term_type = loan.term_type or derived.get("term_type")
    term_value = loan.term_value if loan.term_value is not None else derived.get("term_value")
    loan_days = loan.loan_days if loan.loan_days is not None else derived.get("loan_days")
    frequency = loan.repayment_frequency or derived.get("repayment_frequency")
    installment_count = loan.installment_count or derived.get("installment_count")
    number_of_installments = loan.number_of_installments or derived.get("number_of_installments")
    start_date = loan.start_date or derived.get("start_date")
    maturity_date = loan.maturity_date or derived.get("maturity_date")
    final_due = loan.final_installment_due_date or derived.get("final_installment_due_date")
    term_display = None
    if term_type == "DAYS" and term_value is not None:
        term_display = f"{term_value} days"
    elif term_type == "MONTHS" and term_value is not None:
        term_display = f"{term_value} months"
    return {
        "term_type": term_type, "term_value": term_value, "loan_days": loan_days, "tenure_months": loan.tenure_months,
        "term_display": term_display, "repayment_frequency": frequency, "installment_count": installment_count,
        "number_of_installments": number_of_installments, "start_date": start_date, "maturity_date": maturity_date,
        "end_date": loan.end_date or derived.get("end_date"), "final_installment_due_date": final_due,
        "interest_rate": loan.interest_rate, "interest_rate_basis": loan.interest_rate_basis,
    }

def backfill_period_start_dates_from_schedule(loan: Loan) -> int:
    entries = _ordered_entries(loan)
    if not entries:
        return 0
    changed = 0
    previous_due = None
    for entry in entries:
        if entry.period_start_date is None:
            if entry.installment_no == 1 or previous_due is None:
                entry.period_start_date = loan.start_date or (entry.due_date - timedelta(days=int(entry.period_days or 0)) if entry.due_date and entry.period_days else None)
            else:
                entry.period_start_date = previous_due
            if entry.period_start_date is not None:
                changed += 1
        previous_due = entry.due_date or previous_due
    return changed

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
    outstanding = max(Decimal("0.00"), money(total_payable - total_paid))
    return {"currency": CURRENCY_CODE,"total_principal": float(total_principal),"total_principal_formatted": format_currency(total_principal),"total_interest": float(total_interest),"total_interest_formatted": format_currency(total_interest),"total_payable": float(total_payable),"total_payable_formatted": format_currency(total_payable),"total_paid": float(total_paid),"total_paid_formatted": format_currency(total_paid),"outstanding": float(outstanding),"outstanding_formatted": format_currency(outstanding),"total_delay_interest": float(total_delay_interest),"total_delay_interest_formatted": format_currency(total_delay_interest)}
