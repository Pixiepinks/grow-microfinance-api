from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func

from .extensions import db
from .models import AccountingAccount, AccountingJournalEntry, Investor, InvestorFundingAgreement, InvestorFundingTransaction, InvestorInterestAccrual
from .accounting import AccountingError, ValidationError, create_draft_journal, post_journal, reverse_journal, require_open_accounting_period, get_setting, log_audit

CENT = Decimal("0.01")
INCREASE_TYPES = {"INITIAL_FUNDING", "ADDITIONAL_FUNDING", "ADJUSTMENT_INCREASE", "INTEREST_CAPITALIZATION"}
DECREASE_TYPES = {"PRINCIPAL_WITHDRAWAL", "PRINCIPAL_REPAYMENT", "ADJUSTMENT_DECREASE"}
MONTHLY_METHODS = {"MONTHLY_AVERAGE_DAILY_BALANCE", "MONTHLY_OPENING_BALANCE", "MONTHLY_CLOSING_BALANCE", "FIXED_PRINCIPAL_MONTHLY"}


def money(value):
    return Decimal(str(value or "0")).quantize(CENT, rounding=ROUND_HALF_UP)


def _seq(model, field, prefix, width=6):
    last = db.session.query(func.max(getattr(model, field))).filter(getattr(model, field).like(prefix + "%")).with_for_update().scalar()
    next_no = int(str(last).rsplit("-", 1)[1]) + 1 if last else 1
    return f"{prefix}{next_no:0{width}d}"


def generate_investor_number():
    return _seq(Investor, "investor_number", "GROW-INV-", 6)


def generate_agreement_number(agreement_date):
    prefix = f"GROW-IFA-{agreement_date:%Y%m%d}-"
    return _seq(InvestorFundingAgreement, "agreement_number", prefix, 4)


def generate_transaction_number(transaction_date):
    prefix = f"GROW-IFT-{transaction_date:%Y%m%d}-"
    return _seq(InvestorFundingTransaction, "transaction_number", prefix, 4)


def resolve_account(setting_key, default_code=None):
    value = get_setting(setting_key, default_code)
    acct = None
    if value:
        acct = AccountingAccount.query.get(int(value)) if str(value).isdigit() else AccountingAccount.query.filter_by(account_code=str(value)).first()
    if not acct and default_code:
        acct = AccountingAccount.query.filter_by(account_code=default_code).first()
    if not acct:
        raise ValidationError(f"Missing account mapping: {setting_key}")
    return acct


def seed_investor_accounts():
    accounts = [
        ("2300", "Investor Borrowings – Control", "LIABILITY", "CREDIT", "INVESTOR_BORROWINGS_CONTROL"),
        ("2310", "Accrued Investor Interest Payable", "LIABILITY", "CREDIT", "INVESTOR_INTEREST_PAYABLE"),
        ("2320", "Withholding Tax Payable", "LIABILITY", "CREDIT", "WITHHOLDING_TAX_PAYABLE"),
        ("5100", "Investor Interest Expense", "EXPENSE", "DEBIT", "INVESTOR_INTEREST_EXPENSE"),
    ]
    for code, name, typ, normal, role in accounts:
        acct = AccountingAccount.query.filter_by(account_code=code).first()
        if not acct:
            db.session.add(AccountingAccount(account_code=code, account_name=name, account_type=typ, normal_balance=normal, account_subtype="BORROWING" if typ == "LIABILITY" else "OPERATING_EXPENSE", cash_flow_category="FINANCING" if typ == "LIABILITY" else "OPERATING", is_system_account=True, is_active=True, allow_manual_posting=True, account_role=role))
    db.session.flush()
    defaults = {
        "investor_borrowings_control_account": "2300", "INVESTOR_BORROWINGS_CONTROL": "2300", "INVESTOR_BORROWINGS": "2300",
        "investor_interest_expense_account": "5100", "INVESTOR_INTEREST_EXPENSE": "5100",
        "investor_interest_payable_account": "2310", "INVESTOR_INTEREST_PAYABLE": "2310",
        "investor_withholding_tax_payable_account": "2320", "WITHHOLDING_TAX_PAYABLE": "2320",
        "default_investor_funding_bank_account": "1010", "default_investor_interest_calculation_method": "MONTHLY_AVERAGE_DAILY_BALANCE",
        "default_investor_interest_rate_period": "MONTHLY", "auto_post_investor_interest": "true", "investor_interest_accrual_day": "MONTH_END",
        "allow_historical_investor_transactions": "true", "allow_interest_capitalization": "false", "require_investor_agreement_document": "false",
        "require_withholding_tax_configuration": "false", "investor_balance_reconciliation_tolerance": "0.01",
    }
    from .models import AccountingSetting
    for k, v in defaults.items():
        if not AccountingSetting.query.filter_by(setting_key=k).first():
            db.session.add(AccountingSetting(setting_key=k, setting_value=v))
    db.session.flush()


def create_investor(data, user_id=None):
    inv = Investor(investor_number=generate_investor_number(), investor_type=data.get("investor_type", "INDIVIDUAL"), full_name=data["full_name"], company_name=data.get("company_name"), nic=data.get("nic"), company_registration_number=data.get("company_registration_number"), tax_identification_number=data.get("tax_identification_number"), mobile=data.get("mobile"), email=data.get("email"), address=data.get("address"), bank_name=data.get("bank_name"), bank_branch=data.get("bank_branch"), bank_account_name=data.get("bank_account_name"), bank_account_number=data.get("bank_account_number"), status=data.get("status", "ACTIVE"), notes=data.get("notes"), created_by=user_id)
    db.session.add(inv); db.session.flush(); log_audit("INVESTOR_CREATED", "Investor", inv.id, user_id); return inv


def create_agreement(data, user_id=None):
    agreement_date = date.fromisoformat(data.get("agreement_date") or date.today().isoformat())
    start = date.fromisoformat(data.get("start_date") or agreement_date.isoformat())
    liability = resolve_account("investor_borrowings_control_account", "2300")
    expense = resolve_account("investor_interest_expense_account", "5100")
    payable = resolve_account("investor_interest_payable_account", "2310")
    bank = resolve_account("default_investor_funding_bank_account", "1010")
    agr = InvestorFundingAgreement(agreement_number=generate_agreement_number(agreement_date), investor_id=int(data["investor_id"]), agreement_name=data.get("agreement_name"), agreement_date=agreement_date, start_date=start, maturity_date=date.fromisoformat(data["maturity_date"]) if data.get("maturity_date") else None, original_principal_amount=money(data.get("original_principal_amount")), current_principal_balance=money(data.get("current_principal_balance")), interest_rate=Decimal(str(data.get("interest_rate", "0"))), interest_rate_period=data.get("interest_rate_period", "MONTHLY"), calculation_method=data.get("calculation_method") or get_setting("default_investor_interest_calculation_method", "MONTHLY_AVERAGE_DAILY_BALANCE"), interest_payment_frequency=data.get("interest_payment_frequency", "MONTHLY"), compounding_method=data.get("compounding_method", "SIMPLE"), day_count_basis=data.get("day_count_basis", "ACTUAL"), interest_payment_method=data.get("interest_payment_method", "BANK_TRANSFER"), funding_account_id=data.get("funding_account_id") or bank.id, investor_liability_account_id=data.get("investor_liability_account_id") or liability.id, interest_expense_account_id=data.get("interest_expense_account_id") or expense.id, accrued_interest_payable_account_id=data.get("accrued_interest_payable_account_id") or payable.id, withholding_tax_account_id=data.get("withholding_tax_account_id"), withholding_tax_rate=Decimal(str(data["withholding_tax_rate"])) if data.get("withholding_tax_rate") is not None else None, status=data.get("status", "DRAFT"), created_by=user_id)
    db.session.add(agr); db.session.flush(); log_audit("INVESTOR_AGREEMENT_CREATED", "InvestorFundingAgreement", agr.id, user_id); return agr


def _post_two_line(date_, desc, debit_account_id, credit_account_id, amount, ref_type, ref_id, user_id, idem):
    entry = create_draft_journal(date_, desc, [{"account_id": debit_account_id, "debit": amount, "credit": 0, "description": desc}, {"account_id": credit_account_id, "debit": 0, "credit": amount, "description": desc}], ref_type, ref_id, "INVESTOR_FUNDING", user_id, idem)
    return post_journal(entry, user_id)


def record_funding(agreement_id, data, user_id=None, transaction_type=None):
    agr = InvestorFundingAgreement.query.get(agreement_id)
    if not agr: raise ValidationError("Agreement not found")
    amount = money(data.get("amount")); tx_date = date.fromisoformat(data["transaction_date"]); require_open_accounting_period(tx_date)
    if amount <= 0 or tx_date < agr.start_date: raise ValidationError("Invalid funding transaction")
    if agr.status not in {"DRAFT", "ACTIVE"}: raise ValidationError("Agreement is not fundable")
    bank_id = int(data.get("bank_account_id") or agr.funding_account_id)
    ttype = transaction_type or ("INITIAL_FUNDING" if money(agr.current_principal_balance) == 0 else "ADDITIONAL_FUNDING")
    tx = InvestorFundingTransaction(transaction_number=generate_transaction_number(tx_date), investor_id=agr.investor_id, agreement_id=agr.id, transaction_type=ttype, transaction_date=tx_date, accounting_date=tx_date, amount=amount, bank_account_id=bank_id, reference=data.get("reference"), remarks=data.get("remarks"), status="POSTED", created_by=user_id)
    db.session.add(tx); db.session.flush()
    journal = _post_two_line(tx_date, f"Investor funding – {agr.agreement_number}", bank_id, agr.investor_liability_account_id, amount, "INVESTOR_FUNDING", tx.id, user_id, f"INVESTOR_FUNDING:{tx.id}")
    tx.journal_entry_id = journal.id; agr.current_principal_balance = money(agr.current_principal_balance) + amount; agr.original_principal_amount = money(agr.original_principal_amount) + amount
    if agr.status == "DRAFT": agr.status = "ACTIVE"
    log_audit("INVESTOR_FUNDING_RECEIVED", "InvestorFundingTransaction", tx.id, user_id, {"amount": str(amount), "journal_id": journal.id}); return tx


def principal_repayment(agreement_id, data, user_id=None):
    agr = InvestorFundingAgreement.query.get(agreement_id); amount = money(data.get("amount")); tx_date = date.fromisoformat(data["transaction_date"]); require_open_accounting_period(tx_date)
    if not agr or amount <= 0 or amount > money(agr.current_principal_balance) or not agr.allow_partial_withdrawal: raise ValidationError("Invalid principal repayment")
    bank_id = int(data.get("bank_account_id") or agr.funding_account_id)
    tx = InvestorFundingTransaction(transaction_number=generate_transaction_number(tx_date), investor_id=agr.investor_id, agreement_id=agr.id, transaction_type="PRINCIPAL_REPAYMENT", transaction_date=tx_date, accounting_date=tx_date, amount=amount, bank_account_id=bank_id, reference=data.get("reference"), remarks=data.get("remarks"), status="POSTED", created_by=user_id)
    db.session.add(tx); db.session.flush()
    journal = _post_two_line(tx_date, f"Investor principal repayment – {agr.agreement_number}", agr.investor_liability_account_id, bank_id, amount, "INVESTOR_PRINCIPAL_REPAYMENT", tx.id, user_id, f"INVESTOR_REPAYMENT:{tx.id}")
    tx.journal_entry_id = journal.id; agr.current_principal_balance = money(agr.current_principal_balance) - amount
    log_audit("INVESTOR_PRINCIPAL_REPAID", "InvestorFundingTransaction", tx.id, user_id, {"amount": str(amount), "journal_id": journal.id}); return tx


def daily_balance_engine(agr, period_start, period_end):
    opening = balance_as_of(agr.id, period_start - timedelta(days=1)); current = opening; balances = []
    txs = InvestorFundingTransaction.query.filter_by(agreement_id=agr.id, status="POSTED").filter(InvestorFundingTransaction.transaction_date >= period_start, InvestorFundingTransaction.transaction_date <= period_end).order_by(InvestorFundingTransaction.transaction_date, InvestorFundingTransaction.id).all()
    by_date = {}
    for tx in txs: by_date.setdefault(tx.transaction_date, []).append(tx)
    d = period_start
    while d <= period_end:
        for tx in by_date.get(d, []):
            current += money(tx.amount) if tx.transaction_type in INCREASE_TYPES else -money(tx.amount) if tx.transaction_type in DECREASE_TYPES else Decimal("0.00")
        balances.append({"date": d, "closing_balance": money(current)})
        d += timedelta(days=1)
    total = sum((b["closing_balance"] for b in balances), Decimal("0.00")); days = len(balances) or 1
    return {"daily_balances": balances, "opening_balance": money(opening), "closing_balance": money(current), "sum_of_daily_balances": money(total), "average_daily_balance": money(total / Decimal(days)), "days_in_period": days}


def balance_as_of(agreement_id, as_of):
    txs = InvestorFundingTransaction.query.filter_by(agreement_id=agreement_id, status="POSTED").filter(InvestorFundingTransaction.transaction_date <= as_of).all()
    bal = Decimal("0.00")
    for tx in txs:
        bal += money(tx.amount) if tx.transaction_type in INCREASE_TYPES else -money(tx.amount) if tx.transaction_type in DECREASE_TYPES else Decimal("0.00")
    return money(bal)


def calculate_investor_interest(agreement_id, period_start, period_end):
    agr = InvestorFundingAgreement.query.get(agreement_id)
    if not agr: raise ValidationError("Agreement not found")
    engine = daily_balance_engine(agr, period_start, period_end)
    rate = Decimal(str(agr.interest_rate)); method = agr.calculation_method
    base = engine["average_daily_balance"] if method == "MONTHLY_AVERAGE_DAILY_BALANCE" else engine["opening_balance"] if method == "MONTHLY_OPENING_BALANCE" else engine["closing_balance"]
    if agr.interest_rate_period == "MONTHLY" or method in MONTHLY_METHODS:
        gross = money(base * rate / Decimal("100"))
    else:
        gross = money(engine["sum_of_daily_balances"] * rate / Decimal("100") / Decimal("365" if method != "ANNUAL_ACTUAL_366" else "366"))
    tax = money(gross * Decimal(str(agr.withholding_tax_rate or 0)) / Decimal("100")) if agr.withholding_tax_rate and agr.withholding_tax_account_id else Decimal("0.00")
    return {**engine, "interest_rate": rate, "interest_rate_period": agr.interest_rate_period, "calculation_method": method, "gross_interest_amount": gross, "withholding_tax_amount": tax, "net_interest_payable": money(gross - tax)}


def post_investor_interest_accrual(agreement_id, period_start, period_end, requested_by=None):
    agr = InvestorFundingAgreement.query.get(agreement_id); require_open_accounting_period(period_end)
    existing = InvestorInterestAccrual.query.filter_by(agreement_id=agreement_id, accrual_period_start=period_start, accrual_period_end=period_end).first()
    if existing and existing.status != "CALCULATED": return existing
    calc = calculate_investor_interest(agreement_id, period_start, period_end)
    accrual = existing or InvestorInterestAccrual(investor_id=agr.investor_id, agreement_id=agr.id, accrual_period_start=period_start, accrual_period_end=period_end, created_by=requested_by)
    accrual.days_in_period = calc["days_in_period"]
    accrual.opening_principal_balance = calc["opening_balance"]
    accrual.closing_principal_balance = calc["closing_balance"]
    accrual.average_daily_balance = calc["average_daily_balance"]
    accrual.interest_rate = calc["interest_rate"]
    accrual.interest_rate_period = calc["interest_rate_period"]
    accrual.calculation_method = calc["calculation_method"]
    accrual.gross_interest_amount = calc["gross_interest_amount"]
    accrual.withholding_tax_amount = calc["withholding_tax_amount"]
    accrual.net_interest_payable = calc["net_interest_payable"]
    db.session.add(accrual); db.session.flush()
    desc = f"Investor interest accrual – {agr.agreement_number} – {period_end:%Y-%m}"
    journal = _post_two_line(period_end, desc, agr.interest_expense_account_id, agr.accrued_interest_payable_account_id, accrual.gross_interest_amount, "INVESTOR_INTEREST_ACCRUAL", accrual.id, requested_by, f"INVESTOR_INTEREST_ACCRUAL:{agr.id}:{period_start}:{period_end}")
    accrual.journal_entry_id = journal.id; accrual.status = "POSTED"; accrual.posted_at = datetime.utcnow()
    log_audit("INVESTOR_INTEREST_ACCRUED", "InvestorInterestAccrual", accrual.id, requested_by, {"journal_id": journal.id}); return accrual


def pay_interest(accrual_id, data, user_id=None):
    accrual = InvestorInterestAccrual.query.get(accrual_id); amount = money(data.get("amount")); pay_date = date.fromisoformat(data["payment_date"]); require_open_accounting_period(pay_date)
    agr = accrual.agreement; bank_id = int(data.get("bank_account_id") or agr.funding_account_id); remaining = money(accrual.net_interest_payable) - money(accrual.payment_amount)
    if amount <= 0 or amount > remaining: raise ValidationError("Invalid interest payment amount")
    desc = f"Investor interest payment – {agr.agreement_number}"
    lines = [{"account_id": agr.accrued_interest_payable_account_id, "debit": amount, "credit": 0, "description": desc}, {"account_id": bank_id, "debit": 0, "credit": amount, "description": desc}]
    journal = create_draft_journal(pay_date, desc, lines, "INVESTOR_INTEREST_PAYMENT", accrual.id, "INVESTOR_FUNDING", user_id, f"INVESTOR_INTEREST_PAYMENT:{accrual.id}:{money(accrual.payment_amount)+amount}"); post_journal(journal, user_id)
    accrual.payment_amount = money(accrual.payment_amount) + amount; accrual.payment_journal_entry_id = journal.id; accrual.status = "PAID" if accrual.payment_amount >= accrual.net_interest_payable else "PARTIALLY_PAID"
    return accrual


def capitalize_interest(accrual_id, user_id=None):
    if str(get_setting("allow_interest_capitalization", "false")).lower() != "true": raise ValidationError("Interest capitalization is disabled")
    accrual = InvestorInterestAccrual.query.get(accrual_id); agr = accrual.agreement; require_open_accounting_period(accrual.accrual_period_end)
    amount = money(accrual.net_interest_payable) - money(accrual.capitalization_amount)
    journal = _post_two_line(accrual.accrual_period_end, f"Investor interest capitalization – {agr.agreement_number}", agr.interest_expense_account_id, agr.investor_liability_account_id, amount, "INVESTOR_INTEREST_CAPITALIZATION", accrual.id, user_id, f"INVESTOR_INTEREST_CAPITALIZATION:{accrual.id}")
    tx = InvestorFundingTransaction(transaction_number=generate_transaction_number(accrual.accrual_period_end), investor_id=agr.investor_id, agreement_id=agr.id, transaction_type="INTEREST_CAPITALIZATION", transaction_date=accrual.accrual_period_end, accounting_date=accrual.accrual_period_end, amount=amount, journal_entry_id=journal.id, status="POSTED", created_by=user_id)
    db.session.add(tx); accrual.capitalization_amount = money(accrual.capitalization_amount) + amount; accrual.capitalization_journal_entry_id = journal.id; accrual.status = "CAPITALIZED"; agr.current_principal_balance = money(agr.current_principal_balance) + amount; return accrual


def month_bounds(month):
    y, m = [int(x) for x in month.split("-")]; return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def completed_periods_for(agr, as_of):
    start = agr.start_date
    d = date(start.year, start.month, 1); periods = []
    while d <= as_of:
        end = date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])
        ps = max(start, d); pe = min(end, as_of, agr.closed_at.date() if agr.closed_at else end)
        if pe == end and pe < as_of:
            periods.append((ps, pe))
        d = end + timedelta(days=1)
    return periods


def reverse_investor_transaction(tx_id, reversal_date, reason, user_id=None):
    tx = InvestorFundingTransaction.query.get(tx_id); require_open_accounting_period(reversal_date)
    if not tx or tx.status != "POSTED": raise ValidationError("Transaction cannot be reversed")
    rev = reverse_journal(tx.journal_entry, reversal_date, reason, user_id)
    tx.status = "REVERSED"; tx.reversed_at = datetime.utcnow(); tx.reversal_reason = reason
    agr = tx.agreement
    agr.current_principal_balance = money(agr.current_principal_balance) - money(tx.amount) if tx.transaction_type in INCREASE_TYPES else money(agr.current_principal_balance) + money(tx.amount)
    return rev



def reverse_interest_accrual(accrual_id, reversal_date, reason, user_id=None):
    accrual = InvestorInterestAccrual.query.get(accrual_id)
    if not accrual or accrual.status not in {"POSTED", "PARTIALLY_PAID", "PAID", "CAPITALIZED"}:
        raise ValidationError("Accrual cannot be reversed")
    require_open_accounting_period(reversal_date)
    if accrual.journal_entry_id:
        original = AccountingJournalEntry.query.get(accrual.journal_entry_id)
        reversal = reverse_journal(original, reversal_date, reason, user_id)
        accrual.reversal_journal_id = reversal.id
    else:
        reversal = None
    accrual.status = "REVERSED"
    accrual.reversed_at = datetime.utcnow()
    log_audit("INVESTOR_INTEREST_ACCRUAL_REVERSED", "InvestorInterestAccrual", accrual.id, user_id, {"reason": reason, "reversal_journal_id": reversal.id if reversal else None})
    return reversal

def investor_reconciliation():
    principal = db.session.query(func.coalesce(func.sum(InvestorFundingAgreement.current_principal_balance), 0)).filter(InvestorFundingAgreement.status.in_(["ACTIVE", "MATURED"])).scalar()
    return {"principal_subledger_balance": str(money(principal)), "status": "OK", "tolerance": get_setting("investor_balance_reconciliation_tolerance", "0.01")}
