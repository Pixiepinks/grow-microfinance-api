import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")
ALLOWED_TERM_TYPES = {"DAYS", "MONTHS"}
ALLOWED_REPAYMENT_FREQUENCIES = {"DAILY", "WEEKLY", "MONTHLY"}
ALLOWED_INTEREST_RATE_BASIS = {"FLAT_TERM", "MONTHLY", "ANNUAL"}


def money(value) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class InstallmentPeriod:
    installment_no: int
    period_start: date
    due_date: date
    days_in_period: int


@dataclass(frozen=True)
class ResolvedLoanTerm:
    start_date: date
    maturity_date: date
    total_days: int
    installment_count: int
    installment_periods: list[InstallmentPeriod]


def add_calendar_months(d: date, months: int) -> date:
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return d.replace(year=year, month=month, day=day)


def _period_days(freq: str) -> int:
    return {"DAILY": 1, "WEEKLY": 7}.get(freq)


def resolve_loan_term(start_date: date, term_type: str, term_value: int, repayment_frequency: str) -> ResolvedLoanTerm:
    term_type = (term_type or "").upper()
    repayment_frequency = (repayment_frequency or "").upper()
    if term_type not in ALLOWED_TERM_TYPES:
        raise ValueError("term_type must be DAYS or MONTHS")
    if repayment_frequency not in ALLOWED_REPAYMENT_FREQUENCIES:
        raise ValueError("repayment_frequency is unsupported")
    try:
        term_value = int(term_value)
    except (TypeError, ValueError):
        raise ValueError("term_value must be a number")
    if term_value <= 0:
        raise ValueError("term_value must be greater than zero")

    maturity_date = start_date + timedelta(days=term_value) if term_type == "DAYS" else add_calendar_months(start_date, term_value)
    total_days = (maturity_date - start_date).days
    periods = []
    period_start = start_date
    no = 1
    if repayment_frequency == "MONTHLY":
        while period_start < maturity_date:
            next_due_exclusive = min(add_calendar_months(start_date, no), maturity_date)
            days = (next_due_exclusive - period_start).days
            periods.append(InstallmentPeriod(no, period_start, next_due_exclusive, days))
            period_start = next_due_exclusive
            no += 1
    else:
        step = _period_days(repayment_frequency)
        while period_start < maturity_date:
            next_due_exclusive = min(period_start + timedelta(days=step), maturity_date)
            days = (next_due_exclusive - period_start).days
            periods.append(InstallmentPeriod(no, period_start, next_due_exclusive, days))
            period_start = next_due_exclusive
            no += 1
    return ResolvedLoanTerm(start_date, maturity_date, total_days, len(periods), periods)


def calculate_flat_term_amounts(principal, interest_rate, installment_count):
    principal = money(principal)
    interest_rate = Decimal(str(interest_rate or 0))
    total_interest = money(principal * interest_rate / Decimal("100"))
    total_payable = money(principal + total_interest)
    installment_amount = money(total_payable / Decimal(installment_count))
    return total_interest, total_payable, installment_amount
