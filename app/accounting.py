from __future__ import annotations

import csv
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from io import StringIO

from flask import current_app
from sqlalchemy import func, text

from .extensions import db
from .models import (
    AccountingAccount,
    AccountingAuditLog,
    AccountingJournalEntry,
    AccountingJournalLine,
    AccountingSetting,
    Customer,
    Loan,
    LoanLedger,
    Payment,
    LoanApplication,
    User,
)

CENT = Decimal("0.01")
ACCOUNT_TYPES = {"ASSET", "LIABILITY", "EQUITY", "INCOME", "EXPENSE"}
NORMAL_BALANCES = {"DEBIT", "CREDIT"}
ACCOUNT_SUBTYPES = {"CASH", "BANK", "LOAN_RECEIVABLE", "INTEREST_RECEIVABLE", "PENALTY_RECEIVABLE", "OTHER_CURRENT_ASSET", "FIXED_ASSET", "ACCOUNTS_PAYABLE", "BORROWING", "CAPITAL", "RETAINED_EARNINGS", "INTEREST_INCOME", "PENALTY_INCOME", "FEE_INCOME", "OPERATING_EXPENSE", "WRITE_OFF_EXPENSE", "SUSPENSE", "OTHER"}
SYSTEM_MAPPINGS = {
    "DEFAULT_DISBURSEMENT_ACCOUNT": "1010",
    "DEFAULT_CASH_COLLECTION_ACCOUNT": "1000",
    "DEFAULT_BANK_COLLECTION_ACCOUNT": "1010",
    "CASH_ACCOUNT": "1000",
    "BANK_ACCOUNT": "1010",
    "LOAN_RECEIVABLE_ACCOUNT": "1100",
    "INTEREST_RECEIVABLE_ACCOUNT": "1110",
    "PENALTY_RECEIVABLE_ACCOUNT": "1120",
    "INTEREST_INCOME_ACCOUNT": "4000",
    "PENALTY_INCOME_ACCOUNT": "4010",
    "PROCESSING_FEE_INCOME_ACCOUNT": "4020",
    "OTHER_FEE_INCOME_ACCOUNT": "4020",
    "LOAN_WRITE_OFF_EXPENSE_ACCOUNT": "5050",
    "SUSPENSE_ACCOUNT": "1990",
    "RETAINED_EARNINGS_ACCOUNT": "3100",
}
DEFAULT_ACCOUNTS = [
    ("1000", "Cash on Hand", "ASSET", "DEBIT", True, "CASH"),
    ("1010", "Main Bank Account", "ASSET", "DEBIT", True, "BANK"),
    ("1100", "Loan Principal Receivable", "ASSET", "DEBIT", True, "LOAN_RECEIVABLE"),
    ("1110", "Interest Receivable", "ASSET", "DEBIT", True, "INTEREST_RECEIVABLE"),
    ("1120", "Penalty Receivable", "ASSET", "DEBIT", True, "PENALTY_RECEIVABLE"),
    ("2000", "Accounts Payable", "LIABILITY", "CREDIT", False, "ACCOUNTS_PAYABLE"),
    ("2100", "Borrowings", "LIABILITY", "CREDIT", False, "BORROWING"),
    ("3000", "Owner's Capital", "EQUITY", "CREDIT", False, "CAPITAL"),
    ("3100", "Retained Earnings", "EQUITY", "CREDIT", True, "RETAINED_EARNINGS"),
    ("4000", "Interest Income", "INCOME", "CREDIT", True, "INTEREST_INCOME"),
    ("4010", "Penalty Income", "INCOME", "CREDIT", True, "PENALTY_INCOME"),
    ("4020", "Processing Fee Income", "INCOME", "CREDIT", True, "FEE_INCOME"),
    ("5000", "Salary Expense", "EXPENSE", "DEBIT", False, "OPERATING_EXPENSE"),
    ("5010", "Rent Expense", "EXPENSE", "DEBIT", False, "OPERATING_EXPENSE"),
    ("5020", "Utilities Expense", "EXPENSE", "DEBIT", False, "OPERATING_EXPENSE"),
    ("5030", "Transport Expense", "EXPENSE", "DEBIT", False, "OPERATING_EXPENSE"),
    ("5040", "Office Expense", "EXPENSE", "DEBIT", False, "OPERATING_EXPENSE"),
    ("5050", "Loan Write-off Expense", "EXPENSE", "DEBIT", True, "WRITE_OFF_EXPENSE"),
    ("1990", "Suspense Account", "ASSET", "DEBIT", True, "SUSPENSE"),
]
SETTING_VALIDATION = {
    "DEFAULT_DISBURSEMENT_ACCOUNT": ({"ASSET"}, {"CASH", "BANK"}),
    "DEFAULT_CASH_COLLECTION_ACCOUNT": ({"ASSET"}, {"CASH"}),
    "DEFAULT_BANK_COLLECTION_ACCOUNT": ({"ASSET"}, {"BANK"}),
    "LOAN_RECEIVABLE_ACCOUNT": ({"ASSET"}, {"LOAN_RECEIVABLE"}),
    "INTEREST_RECEIVABLE_ACCOUNT": ({"ASSET"}, {"INTEREST_RECEIVABLE"}),
    "PENALTY_RECEIVABLE_ACCOUNT": ({"ASSET"}, {"PENALTY_RECEIVABLE"}),
    "INTEREST_INCOME_ACCOUNT": ({"INCOME"}, {"INTEREST_INCOME", "FEE_INCOME", "OTHER"}),
    "PENALTY_INCOME_ACCOUNT": ({"INCOME"}, {"PENALTY_INCOME", "FEE_INCOME", "OTHER"}),
    "PROCESSING_FEE_INCOME_ACCOUNT": ({"INCOME"}, {"FEE_INCOME", "OTHER"}),
    "LOAN_WRITE_OFF_EXPENSE_ACCOUNT": ({"EXPENSE"}, {"WRITE_OFF_EXPENSE", "OPERATING_EXPENSE", "OTHER"}),
    "SUSPENSE_ACCOUNT": ({"ASSET", "LIABILITY"}, {"SUSPENSE", "OTHER_CURRENT_ASSET", "OTHER"}),
    "RETAINED_EARNINGS_ACCOUNT": ({"EQUITY"}, {"RETAINED_EARNINGS"}),
}

class AccountingError(ValueError):
    pass

def money(value) -> Decimal:
    return Decimal(str(value or "0")).quantize(CENT, rounding=ROUND_HALF_UP)

def log_audit(action, entity_type, entity_id=None, user_id=None, details=None):
    db.session.add(AccountingAuditLog(action=action, entity_type=entity_type, entity_id=str(entity_id) if entity_id else None, user_id=user_id, details=str(details) if details is not None else None))

def seed_default_accounts():
    for code, name, typ, normal, system, category in DEFAULT_ACCOUNTS:
        acct = AccountingAccount.query.filter_by(account_code=code).first()
        if not acct:
            db.session.add(AccountingAccount(account_code=code, account_name=name, account_type=typ, normal_balance=normal, is_system_account=system, cash_flow_category=("RECEIVABLE" if "RECEIVABLE" in category else category), account_subtype=category))
        else:
            acct.is_system_account = bool(system) or acct.is_system_account
            acct.account_type = typ
            acct.normal_balance = normal
            if hasattr(acct, "account_subtype"):
                acct.account_subtype = category
            if getattr(acct, "cash_flow_category", None) in (None, "NONE") and category != "NONE":
                acct.cash_flow_category = "RECEIVABLE" if "RECEIVABLE" in category else category
    db.session.flush()
    for key, code in SYSTEM_MAPPINGS.items():
        setting = AccountingSetting.query.filter_by(setting_key=key).first()
        if not setting:
            db.session.add(AccountingSetting(setting_key=key, setting_value=code))

def resolve_system_account(key):
    seed_default_accounts()
    setting = AccountingSetting.query.filter_by(setting_key=key).first()
    code = setting.setting_value if setting else SYSTEM_MAPPINGS[key]
    account = AccountingAccount.query.filter_by(account_code=code).first()
    if not account:
        raise AccountingError(f"System account {key} is not configured")
    return account

def generate_journal_number(journal_date):
    prefix = f"GROW-JV-{journal_date:%Y%m%d}-"
    try:
        db.session.execute(text("select pg_advisory_xact_lock(hashtext(:p))"), {"p": prefix})
    except Exception:
        pass
    last = db.session.query(func.max(AccountingJournalEntry.journal_no)).filter(AccountingJournalEntry.journal_no.like(prefix + "%")).scalar()
    next_no = int(last.rsplit("-", 1)[1]) + 1 if last else 1
    return f"{prefix}{next_no:04d}"

def create_account(data, user_id=None):
    typ = data.get("account_type")
    normal = data.get("normal_balance")
    if typ not in ACCOUNT_TYPES or normal not in NORMAL_BALANCES:
        raise AccountingError("Invalid account type or normal balance")
    parent_id = data.get("parent_id")
    if parent_id:
        parent = AccountingAccount.query.get(parent_id)
        if not parent or parent.account_type != typ:
            raise AccountingError("Parent account type is incompatible")
    acct = AccountingAccount(account_code=data["account_code"], account_name=data["account_name"], account_type=typ, normal_balance=normal, parent_id=parent_id, description=data.get("description"), is_active=data.get("is_active", True), allow_manual_posting=data.get("allow_manual_posting", True), cash_flow_category=data.get("cash_flow_category", "NONE"))
    db.session.add(acct); db.session.flush(); log_audit("ACCOUNT_CREATE", "AccountingAccount", acct.id, user_id); return acct

def update_account(acct, data, user_id=None):
    for field in ["account_name", "description", "is_active", "allow_manual_posting", "cash_flow_category"]:
        if field in data: setattr(acct, field, data[field])
    log_audit("ACCOUNT_UPDATE", "AccountingAccount", acct.id, user_id); return acct

def _line_from_payload(raw, line_no):
    return AccountingJournalLine(line_no=line_no, account_id=raw["account_id"], debit=money(raw.get("debit")), credit=money(raw.get("credit")), customer_id=raw.get("customer_id"), loan_id=raw.get("loan_id"), payment_id=raw.get("payment_id"), collection_id=raw.get("collection_id"), description=raw.get("description"))

def create_draft_journal(journal_date, description, lines, reference_type="MANUAL_JOURNAL", reference_id=None, source_module="ACCOUNTING", created_by_id=None, idempotency_key=None):
    if idempotency_key:
        existing = AccountingJournalEntry.query.filter_by(idempotency_key=idempotency_key).first()
        if existing: return existing
    entry = AccountingJournalEntry(journal_no=generate_journal_number(journal_date), journal_date=journal_date, description=description, reference_type=reference_type, reference_id=str(reference_id) if reference_id is not None else None, source_module=source_module, created_by_id=created_by_id, idempotency_key=idempotency_key, status="DRAFT")
    db.session.add(entry); db.session.flush()
    for i, raw in enumerate(lines, 1): entry.lines.append(_line_from_payload(raw, i))
    validate_journal(entry)
    return entry

def validate_journal(entry):
    if len(entry.lines) < 2: raise AccountingError("Posted journals must have at least two lines")
    debit = Decimal("0.00"); credit = Decimal("0.00")
    for line in entry.lines:
        line.debit = money(line.debit); line.credit = money(line.credit)
        if (line.debit > 0 and line.credit > 0) or (line.debit == 0 and line.credit == 0): raise AccountingError("Each line must have either debit or credit")
        acct = AccountingAccount.query.get(line.account_id)
        if not acct or not acct.is_active: raise AccountingError("Inactive or missing account cannot receive postings")
        if acct.children and not acct.allow_manual_posting: raise AccountingError("Parent account is not postable")
        debit += line.debit; credit += line.credit
    entry.total_debit = money(debit); entry.total_credit = money(credit)
    if entry.total_debit != entry.total_credit: raise AccountingError("Journal is not balanced")
    return True

def post_journal(entry, user_id=None):
    if entry.status == "POSTED": return entry
    if entry.status != "DRAFT": raise AccountingError("Only DRAFT journals can be posted")
    validate_journal(entry); entry.status="POSTED"; entry.posted_at=datetime.utcnow(); entry.posted_by_id=user_id; log_audit("JOURNAL_POST", "AccountingJournalEntry", entry.id, user_id); return entry

def reverse_journal(entry, journal_date, reason, user_id=None):
    if entry.status != "POSTED": raise AccountingError("Only POSTED journals can be reversed")
    reversal = create_draft_journal(journal_date, f"Reversal: {reason}", [{"account_id": l.account_id, "debit": l.credit, "credit": l.debit, "customer_id": l.customer_id, "loan_id": l.loan_id, "payment_id": l.payment_id, "collection_id": l.collection_id, "description": l.description} for l in entry.lines], "REVERSAL", entry.id, "ACCOUNTING", user_id, f"REVERSAL:{entry.id}")
    reversal.reversal_of_id = entry.id; post_journal(reversal, user_id); entry.status = "REVERSED"; log_audit("JOURNAL_REVERSE", "AccountingJournalEntry", entry.id, user_id, reason); return reversal

def validate_funding_account(account):
    if not account:
        raise AccountingError("Funding account not found")
    if not account.is_active:
        raise AccountingError("Funding account is inactive")
    if account.account_type != "ASSET":
        raise AccountingError("Funding account must be an ASSET account")
    if account.cash_flow_category not in ("CASH", "BANK"):
        raise AccountingError("Funding account must be configured as CASH or BANK")
    if not account.allow_manual_posting:
        raise AccountingError("Funding account does not allow posting")
    return account

def post_loan_disbursement(loan, user_id=None, funding_key="DEFAULT_DISBURSEMENT_ACCOUNT", funding_account=None, disbursement_date=None):
    amount = money(loan.principal_amount)
    funding_account = validate_funding_account(funding_account or resolve_system_account(funding_key))
    journal_date = disbursement_date or loan.start_date or date.today()
    return post_journal(create_draft_journal(journal_date, "Loan disbursement", [
        {"account_id": resolve_system_account("LOAN_RECEIVABLE_ACCOUNT").id, "debit": amount, "customer_id": loan.customer_id, "loan_id": loan.id},
        {"account_id": funding_account.id, "credit": amount, "customer_id": loan.customer_id, "loan_id": loan.id},
    ], "LOAN_DISBURSEMENT", loan.id, "LOANS", user_id, f"LOAN_DISBURSEMENT:{loan.id}"), user_id)

def allocate_payment(loan, amount, paid_date):
    remaining = money(amount); principal=interest=penalty=Decimal("0.00")
    for e in loan.ledger_entries:
        if remaining <= 0: break
        delay = max((paid_date - e.due_date).days, 0)
        e.delay_days = delay; e.delay_interest = money(Decimal(e.opening_balance) * (Decimal(loan.interest_rate)/Decimal("100")/Decimal("30")) * Decimal(delay))
        outstanding_interest = money(Decimal(e.interest_amount) - min(Decimal(e.paid_amount or 0), Decimal(e.interest_amount)))
        pay = min(remaining, outstanding_interest); interest += pay; remaining -= pay
        pay = min(remaining, Decimal(e.delay_interest or 0)); penalty += pay; remaining -= pay
        outstanding_principal = money(Decimal(e.principal_amount) - max(Decimal(e.paid_amount or 0) - Decimal(e.interest_amount), Decimal("0")))
        pay = min(remaining, outstanding_principal); principal += pay; remaining -= pay
        e.paid_amount = money(Decimal(e.paid_amount or 0) + interest + penalty + principal)
        payable = money(Decimal(e.installment_amount) + Decimal(e.delay_interest or 0))
        e.status = "PAID" if Decimal(e.paid_amount or 0) >= payable else "PARTIAL"
        e.paid_date = paid_date
    if remaining > 0: principal += remaining
    return money(principal), money(interest), money(penalty), Decimal("0.00")

def post_loan_payment(payment, user_id=None):
    if AccountingJournalEntry.query.filter_by(idempotency_key=f"LOAN_PAYMENT:{payment.id}").first():
        return AccountingJournalEntry.query.filter_by(idempotency_key=f"LOAN_PAYMENT:{payment.id}").first()
    total = money(payment.amount_collected); principal=money(payment.principal_paid); interest=money(payment.interest_paid); penalty=money(payment.penalty_paid); other=money(payment.other_fee_paid)
    if money(principal+interest+penalty+other) != total: raise AccountingError("Payment allocation does not match amount collected")
    loan = payment.loan
    lines=[{"account_id": resolve_system_account("CASH_ACCOUNT" if str(payment.payment_method).lower()=="cash" else "BANK_ACCOUNT").id, "debit": total, "customer_id": loan.customer_id, "loan_id": loan.id, "payment_id": payment.id}]
    for key, amt in [("LOAN_RECEIVABLE_ACCOUNT", principal), ("INTEREST_INCOME_ACCOUNT", interest), ("PENALTY_INCOME_ACCOUNT", penalty), ("OTHER_FEE_INCOME_ACCOUNT", other)]:
        if amt > 0: lines.append({"account_id": resolve_system_account(key).id, "credit": amt, "customer_id": loan.customer_id, "loan_id": loan.id, "payment_id": payment.id})
    return post_journal(create_draft_journal(payment.collection_date, "Loan payment", lines, "LOAN_PAYMENT", payment.id, "PAYMENTS", user_id, f"LOAN_PAYMENT:{payment.id}"), user_id)

def serialize_journal(entry):
    return {"id": entry.id, "journal_no": entry.journal_no, "journal_date": entry.journal_date.isoformat(), "description": entry.description, "reference_type": entry.reference_type, "reference_id": entry.reference_id, "status": entry.status, "total_debit": f"{money(entry.total_debit):.2f}", "total_credit": f"{money(entry.total_credit):.2f}", "lines": [{"id": l.id, "line_no": l.line_no, "account_id": l.account_id, "account_code": l.account.account_code, "account_name": l.account.account_name, "debit": f"{money(l.debit):.2f}", "credit": f"{money(l.credit):.2f}", "customer_id": l.customer_id, "loan_id": l.loan_id, "payment_id": l.payment_id, "description": l.description} for l in entry.lines]}

def _blank_to_none(value):
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return value

def _resolve_ledger_account(account_id=None, account_code=None):
    account_id = _blank_to_none(account_id)
    account_code = _blank_to_none(account_code)
    if account_id is not None:
        try:
            numeric_account_id = int(account_id)
        except (TypeError, ValueError) as exc:
            raise AccountingError("account_id must be a numeric accounting account ID") from exc
        account = AccountingAccount.query.get(numeric_account_id)
        if not account:
            raise AccountingError("Account not found")
        return account
    if account_code is not None:
        account = AccountingAccount.query.filter_by(account_code=str(account_code)).first()
        if not account:
            raise AccountingError("Account not found")
        return account
    raise AccountingError("account_id is required")

def _int_filter(value, name):
    value = _blank_to_none(value)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise AccountingError(f"{name} must be numeric") from exc

def general_ledger(account_id=None, date_from=None, date_to=None, customer_id=None, loan_id=None, account_code=None, query_params=None):
    account = _resolve_ledger_account(account_id, account_code)
    customer_id = _int_filter(customer_id, "customer_id")
    loan_id = _int_filter(loan_id, "loan_id")
    posted_statuses = ["POSTED", "REVERSED"]
    base_filters = [
        AccountingJournalLine.account_id == account.id,
        func.upper(AccountingJournalEntry.status).in_(posted_statuses),
    ]
    if customer_id is not None:
        base_filters.append(AccountingJournalLine.customer_id == customer_id)
    if loan_id is not None:
        base_filters.append(AccountingJournalLine.loan_id == loan_id)

    q = (
        AccountingJournalLine.query
        .join(AccountingJournalEntry, AccountingJournalLine.journal_entry_id == AccountingJournalEntry.id)
        .join(AccountingAccount, AccountingJournalLine.account_id == AccountingAccount.id)
        .filter(*base_filters)
    )
    before = q
    tx_query = q
    generated_filters = {"account_id": account.id, "statuses": posted_statuses}
    if customer_id is not None:
        generated_filters["customer_id"] = customer_id
    if loan_id is not None:
        generated_filters["loan_id"] = loan_id
    if date_from:
        before = before.filter(AccountingJournalEntry.journal_date < date_from)
        tx_query = tx_query.filter(AccountingJournalEntry.journal_date >= date_from)
        generated_filters["date_from"] = date_from.isoformat()
    if date_to:
        tx_query = tx_query.filter(AccountingJournalEntry.journal_date <= date_to)
        generated_filters["date_to"] = date_to.isoformat()

    def signed(line): return money(line.debit-line.credit) if account.normal_balance=="DEBIT" else money(line.credit-line.debit)
    opening=sum((signed(l) for l in before.all()), Decimal("0.00")) if date_from else Decimal("0.00")
    running=money(opening); tx=[]; td=tc=Decimal("0.00")
    rows = tx_query.order_by(AccountingJournalEntry.journal_date, AccountingJournalEntry.journal_no, AccountingJournalLine.line_no).all()
    current_app.logger.info("general_ledger query", extra={"query_params": query_params or {}, "resolved_account_id": account.id, "resolved_account_code": account.account_code, "generated_filters": generated_filters, "journal_lines_found": len(rows)})
    for l in rows:
        running=money(running+signed(l)); td+=money(l.debit); tc+=money(l.credit); e=l.journal_entry
        tx.append({"journal_date": e.journal_date.isoformat(), "journal_no": e.journal_no, "description": e.description, "reference_type": e.reference_type, "reference_id": e.reference_id, "debit": f"{money(l.debit):.2f}", "credit": f"{money(l.credit):.2f}", "running_balance": f"{running:.2f}", "customer_id": l.customer_id, "loan_id": l.loan_id})
    return {"account": {"id": account.id, "account_code": account.account_code, "account_name": account.account_name, "account_type": account.account_type, "normal_balance": account.normal_balance}, "opening_balance": f"{money(opening):.2f}", "transactions": tx, "running_balance": f"{running:.2f}", "closing_balance": f"{running:.2f}", "total_debit": f"{money(td):.2f}", "total_credit": f"{money(tc):.2f}"}

def ledger_csv(data):
    out=StringIO(); w=csv.DictWriter(out, fieldnames=["journal_date","journal_no","description","reference_type","reference_id","debit","credit","running_balance","customer_id","loan_id"]); w.writeheader(); w.writerows(data["transactions"]); return out.getvalue()

def reconciliation_issues():
    issues=[]
    for app in LoanApplication.query.filter_by(status="DISBURSED").all():
        loan = Loan.query.filter_by(customer_id=app.customer_id).order_by(Loan.id.desc()).first()
        if not loan or not AccountingJournalEntry.query.filter_by(reference_type="LOAN_DISBURSEMENT", reference_id=str(loan.id)).first():
            issues.append({"type":"DISBURSED_APPLICATION_WITHOUT_JOURNAL","application_id":app.id,"loan_id":loan.id if loan else None})
    for loan in Loan.query.filter(Loan.status.in_(["Active","ACTIVE"])).all():
        journals=AccountingJournalEntry.query.filter_by(reference_type="LOAN_DISBURSEMENT", reference_id=str(loan.id)).all()
        if not journals:
            issues.append({"type":"MISSING_LOAN_DISBURSEMENT_JOURNAL","loan_id":loan.id})
        if len(journals)>1:
            issues.append({"type":"DUPLICATE_LOAN_DISBURSEMENT_JOURNALS","loan_id":loan.id,"count":len(journals)})
        for j in journals:
            if money(j.total_debit) != money(loan.principal_amount) or money(j.total_credit) != money(loan.principal_amount):
                issues.append({"type":"WRONG_LOAN_DISBURSEMENT_AMOUNT","loan_id":loan.id,"journal_id":j.id})
            credits=[l for l in j.lines if money(l.credit)>0]
            if len(credits)!=1 or credits[0].account.cash_flow_category not in ("CASH","BANK") or credits[0].account.account_type != "ASSET" or not credits[0].account.is_active or not credits[0].account.allow_manual_posting:
                issues.append({"type":"INVALID_LOAN_DISBURSEMENT_FUNDING_ACCOUNT","loan_id":loan.id,"journal_id":j.id})
    for p in Payment.query.all():
        if not AccountingJournalEntry.query.filter_by(reference_type="LOAN_PAYMENT", reference_id=str(p.id)).first(): issues.append({"type":"MISSING_LOAN_PAYMENT_JOURNAL","payment_id":p.id})
    rows=db.session.query(AccountingJournalEntry.reference_type, AccountingJournalEntry.reference_id, func.count(AccountingJournalEntry.id)).filter(AccountingJournalEntry.reference_type.in_(["LOAN_DISBURSEMENT","LOAN_PAYMENT"])).group_by(AccountingJournalEntry.reference_type, AccountingJournalEntry.reference_id).having(func.count(AccountingJournalEntry.id)>1).all()
    for r in rows: issues.append({"type":"DUPLICATE_SOURCE_JOURNALS","reference_type":r[0],"reference_id":r[1],"count":r[2]})
    for e in AccountingJournalEntry.query.all():
        td=sum((money(l.debit) for l in e.lines), Decimal("0.00")); tc=sum((money(l.credit) for l in e.lines), Decimal("0.00"))
        if td != tc: issues.append({"type":"UNBALANCED_JOURNAL","journal_id":e.id})
        if money(e.total_debit)!=td or money(e.total_credit)!=tc: issues.append({"type":"JOURNAL_TOTAL_MISMATCH","journal_id":e.id})
    return issues

# Accounting improvement package helpers (phase 1.5)
def account_subtype(account):
    return getattr(account, "account_subtype", None) or ({"CASH": "CASH", "BANK": "BANK", "RECEIVABLE": "LOAN_RECEIVABLE"}.get(getattr(account, "cash_flow_category", None), "OTHER"))

def serialize_account(account):
    return None if not account else {"account_id": account.id, "account_code": account.account_code, "account_name": account.account_name, "account_type": account.account_type, "account_subtype": account_subtype(account), "is_active": account.is_active}

def validate_setting_account(key, account):
    if key not in SETTING_VALIDATION:
        raise AccountingError(f"Unsupported accounting setting: {key}")
    if not account:
        raise AccountingError("Account not found")
    if not account.is_active:
        raise AccountingError("Account is inactive")
    if not account.allow_manual_posting:
        raise AccountingError("Account does not allow posting")
    valid_types, valid_subtypes = SETTING_VALIDATION[key]
    if account.account_type not in valid_types:
        raise AccountingError(f"Account type must be one of {sorted(valid_types)}")
    if account_subtype(account) not in valid_subtypes:
        raise AccountingError(f"Account subtype must be one of {sorted(valid_subtypes)}")
    return account

def _account_from_setting_value(value):
    if value is None:
        return None
    text_value = str(value)
    account = AccountingAccount.query.get(int(text_value)) if text_value.isdigit() else None
    return account or AccountingAccount.query.filter_by(account_code=text_value).first()

def resolve_system_account(key):
    seed_default_accounts()
    setting = AccountingSetting.query.filter_by(setting_key=key).first()
    account = _account_from_setting_value(setting.setting_value) if setting else None
    if not account and key in SYSTEM_MAPPINGS:
        current_app.logger.warning("Accounting setting %s used fallback account code %s", key, SYSTEM_MAPPINGS[key])
        account = AccountingAccount.query.filter_by(account_code=SYSTEM_MAPPINGS[key]).first()
        log_audit("ACCOUNTING_SETTING_FALLBACK_USED", "AccountingSetting", key, None, {"fallback_code": SYSTEM_MAPPINGS[key]})
    if not account:
        raise AccountingError(f"System account {key} is not configured")
    return account

def accounting_settings_payload():
    seed_default_accounts()
    payload = {}
    for key in SETTING_VALIDATION:
        try:
            payload[key] = serialize_account(resolve_system_account(key))
        except AccountingError:
            payload[key] = None
    return payload

def update_accounting_settings(data, user_id=None):
    errors = {}
    updates = {}
    for key, raw_id in (data or {}).items():
        if key not in SETTING_VALIDATION:
            errors[key] = "Unsupported setting"
            continue
        try:
            account = AccountingAccount.query.get(int(raw_id))
            validate_setting_account(key, account)
            updates[key] = account
        except Exception as exc:
            errors[key] = str(exc)
    if errors:
        raise AccountingError(errors)
    for key, account in updates.items():
        setting = AccountingSetting.query.filter_by(setting_key=key).first()
        old = setting.setting_value if setting else None
        if not setting:
            setting = AccountingSetting(setting_key=key, setting_value=str(account.id))
            db.session.add(setting)
        else:
            setting.setting_value = str(account.id)
        log_audit("ACCOUNTING_SETTING_CHANGED", "AccountingSetting", key, user_id, {"old_value": old, "new_value": account.id, "account_code": account.account_code})
    return accounting_settings_payload()

def account_has_activity(acct):
    return db.session.query(AccountingJournalLine.id).filter_by(account_id=acct.id).first() is not None

def account_is_mapped(acct):
    for setting in AccountingSetting.query.all():
        if _account_from_setting_value(setting.setting_value) and _account_from_setting_value(setting.setting_value).id == acct.id:
            return True
    return False

def create_account(data, user_id=None):
    typ = data.get("account_type"); normal = data.get("normal_balance"); subtype = data.get("account_subtype", "OTHER")
    if typ not in ACCOUNT_TYPES or normal not in NORMAL_BALANCES or subtype not in ACCOUNT_SUBTYPES:
        raise AccountingError("Invalid account type, normal balance, or subtype")
    acct = AccountingAccount(account_code=data["account_code"], account_name=data["account_name"], account_type=typ, normal_balance=normal, parent_id=data.get("parent_id"), description=data.get("description"), is_active=data.get("is_active", True), allow_manual_posting=data.get("allow_manual_posting", True), cash_flow_category=data.get("cash_flow_category", subtype if subtype in ("CASH", "BANK") else "NONE"), account_subtype=subtype, financial_statement_group=data.get("financial_statement_group"), financial_statement_order=data.get("financial_statement_order"), cash_flow_group=data.get("cash_flow_group"))
    db.session.add(acct); db.session.flush(); log_audit("ACCOUNT_CREATE", "AccountingAccount", acct.id, user_id); return acct

def update_account(acct, data, user_id=None):
    if acct.is_system_account and data.get("_delete"):
        log_audit("PROTECTED_ACCOUNT_UPDATE_ATTEMPTED", "AccountingAccount", acct.id, user_id, data); raise AccountingError("System accounts cannot be deleted")
    if account_has_activity(acct):
        for f in ("account_code", "account_type", "normal_balance"):
            if f in data and data[f] != getattr(acct, f):
                log_audit("PROTECTED_ACCOUNT_UPDATE_ATTEMPTED", "AccountingAccount", acct.id, user_id, {"field": f}); raise AccountingError(f"{f} cannot be changed after journal activity exists")
    if "is_active" in data and data["is_active"] is False and account_is_mapped(acct):
        log_audit("PROTECTED_ACCOUNT_UPDATE_ATTEMPTED", "AccountingAccount", acct.id, user_id, {"field": "is_active"}); raise AccountingError("Mapped accounts cannot be deactivated")
    for field in ["account_name", "description", "is_active", "allow_manual_posting", "cash_flow_category", "account_subtype", "financial_statement_group", "financial_statement_order", "cash_flow_group"]:
        if field in data: setattr(acct, field, data[field])
    log_audit("ACCOUNT_ACTIVATION_CHANGED" if "is_active" in data else "ACCOUNT_UPDATE", "AccountingAccount", acct.id, user_id, data); return acct

def validate_funding_account(account, method=None):
    if not account: raise AccountingError("Funding account not found")
    if not account.is_active: raise AccountingError("Funding account is inactive")
    if account.account_type != "ASSET": raise AccountingError("Funding account must be an ASSET account")
    if account_subtype(account) not in ("CASH", "BANK"): raise AccountingError("Funding account must be CASH or BANK")
    if method in ("CASH", "Cash") and account_subtype(account) != "CASH": raise AccountingError("Cash transactions require a CASH account")
    if str(method).upper() in ("BANK_TRANSFER", "CHEQUE") and account_subtype(account) != "BANK": raise AccountingError("Bank and cheque transactions require a BANK account")
    if not account.allow_manual_posting: raise AccountingError("Funding account does not allow posting")
    return account

def _method_key(method):
    method = str(method or "CASH").upper().replace(" ", "_")
    return "DEFAULT_CASH_COLLECTION_ACCOUNT" if method == "CASH" else "DEFAULT_BANK_COLLECTION_ACCOUNT" if method in ("BANK_TRANSFER", "BANK", "DEPOSIT", "CHEQUE") else None

def post_loan_disbursement(loan, user_id=None, funding_key="DEFAULT_DISBURSEMENT_ACCOUNT", funding_account=None, disbursement_date=None):
    amount = money(loan.principal_amount)
    if funding_account is None:
        funding_account = resolve_system_account(funding_key); log_audit("DEFAULT_DISBURSEMENT_ACCOUNT_USED", "Loan", loan.id, user_id, {"account_id": funding_account.id, "account_code": funding_account.account_code})
    else:
        log_audit("EXPLICIT_FUNDING_ACCOUNT_SELECTED", "Loan", loan.id, user_id, {"account_id": funding_account.id, "account_code": funding_account.account_code})
    funding_account = validate_funding_account(funding_account)
    journal_date = disbursement_date or loan.start_date or date.today()
    return post_journal(create_draft_journal(journal_date, "Loan disbursement", [{"account_id": resolve_system_account("LOAN_RECEIVABLE_ACCOUNT").id, "debit": amount, "customer_id": loan.customer_id, "loan_id": loan.id}, {"account_id": funding_account.id, "credit": amount, "customer_id": loan.customer_id, "loan_id": loan.id}], "LOAN_DISBURSEMENT", loan.id, "LOANS", user_id, f"LOAN_DISBURSEMENT:{loan.id}"), user_id)

def post_loan_payment(payment, user_id=None, receipt_account=None):
    existing = AccountingJournalEntry.query.filter_by(idempotency_key=f"LOAN_PAYMENT:{payment.id}").first()
    if existing: return existing
    total = money(payment.amount_collected); principal=money(payment.principal_paid); interest=money(payment.interest_paid); penalty=money(payment.penalty_paid); other=money(payment.other_fee_paid)
    if money(principal+interest+penalty+other) != total: raise AccountingError("Payment allocation does not match amount collected")
    key = _method_key(payment.payment_method)
    if receipt_account is None:
        if not key: raise AccountingError("receipt_account_id is required for OTHER payment methods")
        receipt_account = resolve_system_account(key); log_audit("DEFAULT_RECEIPT_ACCOUNT_USED", "Payment", payment.id, user_id, {"account_id": receipt_account.id, "account_code": receipt_account.account_code})
    else:
        log_audit("EXPLICIT_RECEIPT_ACCOUNT_SELECTED", "Payment", payment.id, user_id, {"account_id": receipt_account.id, "account_code": receipt_account.account_code})
    receipt_account = validate_funding_account(receipt_account, payment.payment_method)
    loan = payment.loan
    lines=[{"account_id": receipt_account.id, "debit": total, "customer_id": loan.customer_id, "loan_id": loan.id, "payment_id": payment.id}]
    for key, amt in [("LOAN_RECEIVABLE_ACCOUNT", principal), ("INTEREST_INCOME_ACCOUNT", interest), ("PENALTY_INCOME_ACCOUNT", penalty), ("PROCESSING_FEE_INCOME_ACCOUNT", other)]:
        if amt > 0: lines.append({"account_id": resolve_system_account(key).id, "credit": amt, "customer_id": loan.customer_id, "loan_id": loan.id, "payment_id": payment.id})
    return post_journal(create_draft_journal(payment.collection_date, "Loan payment", lines, "LOAN_PAYMENT", payment.id, "PAYMENTS", user_id, f"LOAN_PAYMENT:{payment.id}"), user_id)

def _line_context(line):
    return {"customer_id": line.customer_id, "customer_number": line.customer.customer_code if line.customer else None, "customer_name": line.customer.full_name if line.customer else None, "loan_id": line.loan_id, "loan_number": line.loan.loan_number if line.loan else None, "payment_id": line.payment_id, "collection_id": line.collection_id}

def serialize_journal(entry):
    source_line = next((l for l in entry.lines if l.customer_id or l.loan_id or l.payment_id), None)
    ctx = _line_context(source_line) if source_line else {}
    reversal = entry.reversal_journals[0] if getattr(entry, "reversal_journals", None) else None
    return {"id": entry.id, "journal_no": entry.journal_no, "journal_date": entry.journal_date.isoformat(), "description": entry.description, "reference_type": entry.reference_type, "reference_id": entry.reference_id, "source_module": entry.source_module, "status": entry.status, "total_debit": f"{money(entry.total_debit):.2f}", "total_credit": f"{money(entry.total_credit):.2f}", "posted_at": entry.posted_at.isoformat() if entry.posted_at else None, "created_by_name": entry.created_by.name if entry.created_by else None, "posted_by_name": entry.posted_by.name if entry.posted_by else None, "customer_id": ctx.get("customer_id"), "customer_number": ctx.get("customer_number"), "customer_name": ctx.get("customer_name"), "loan_id": ctx.get("loan_id"), "loan_number": ctx.get("loan_number"), "payment_id": ctx.get("payment_id"), "collection_id": ctx.get("collection_id"), "original_journal_no": entry.reversal_of.journal_no if entry.reversal_of else None, "reversal_journal_no": reversal.journal_no if reversal else None, "is_reversal": bool(entry.reversal_of_id), "lines": [{"id": l.id, "line_no": l.line_no, "account_id": l.account_id, "account_code": l.account.account_code, "account_name": l.account.account_name, "account_type": l.account.account_type, "account_subtype": account_subtype(l.account), "debit": f"{money(l.debit):.2f}", "credit": f"{money(l.credit):.2f}", **_line_context(l), "description": l.description} for l in entry.lines]}

def general_ledger(account_id=None, date_from=None, date_to=None, customer_id=None, loan_id=None, account_code=None, query_params=None):
    account = _resolve_ledger_account(account_id, account_code)
    customer_id = _int_filter(customer_id, "customer_id"); loan_id = _int_filter(loan_id, "loan_id")
    filters=[AccountingJournalLine.account_id == account.id, func.upper(AccountingJournalEntry.status).in_(["POSTED", "REVERSED"])]
    if customer_id is not None: filters.append(AccountingJournalLine.customer_id == customer_id)
    if loan_id is not None: filters.append(AccountingJournalLine.loan_id == loan_id)
    q=(AccountingJournalLine.query.join(AccountingJournalEntry).outerjoin(Customer, AccountingJournalLine.customer_id == Customer.id).outerjoin(Loan, AccountingJournalLine.loan_id == Loan.id).outerjoin(Payment, AccountingJournalLine.payment_id == Payment.id).filter(*filters))
    before=q; tx_query=q
    if date_from: before=before.filter(AccountingJournalEntry.journal_date < date_from); tx_query=tx_query.filter(AccountingJournalEntry.journal_date >= date_from)
    if date_to: tx_query=tx_query.filter(AccountingJournalEntry.journal_date <= date_to)
    def signed(line): return money(line.debit-line.credit) if account.normal_balance=="DEBIT" else money(line.credit-line.debit)
    opening=sum((signed(l) for l in before.all()), Decimal("0.00")) if date_from else Decimal("0.00")
    running=money(opening); tx=[]; td=tc=Decimal("0.00")
    rows=tx_query.order_by(AccountingJournalEntry.journal_date, AccountingJournalEntry.journal_no, AccountingJournalLine.line_no).all()
    current_app.logger.info("general_ledger query", extra={"query_params": query_params or {}, "resolved_account_id": account.id, "journal_lines_found": len(rows)})
    for l in rows:
        running=money(running+signed(l)); td+=money(l.debit); tc+=money(l.credit); e=l.journal_entry; ctx=_line_context(l)
        tx.append({"journal_entry_id": e.id, "journal_date": e.journal_date.isoformat(), "journal_no": e.journal_no, "description": e.description, "reference_type": e.reference_type, "reference_id": e.reference_id, "source_module": e.source_module, "debit": f"{money(l.debit):.2f}", "credit": f"{money(l.credit):.2f}", "running_balance": f"{running:.2f}", **ctx})
    return {"account": {"id": account.id, "account_code": account.account_code, "account_name": account.account_name, "account_type": account.account_type, "account_subtype": account_subtype(account), "normal_balance": account.normal_balance}, "opening_balance": f"{money(opening):.2f}", "transactions": tx, "running_balance": f"{running:.2f}", "closing_balance": f"{running:.2f}", "total_debit": f"{money(td):.2f}", "total_credit": f"{money(tc):.2f}"}

def ledger_csv(data):
    fields=["journal_entry_id","journal_date","journal_no","description","reference_type","reference_id","source_module","debit","credit","running_balance","customer_id","customer_number","customer_name","loan_id","loan_number","payment_id","collection_id"]
    out=StringIO(); w=csv.DictWriter(out, fieldnames=fields, extrasaction="ignore"); w.writeheader(); w.writerows(data["transactions"]); return out.getvalue()

# Phase 2 financial reporting
FINANCIAL_STATEMENT_GROUPS = {
    "OPERATING_INCOME", "OTHER_INCOME", "STAFF_EXPENSE", "ADMIN_EXPENSE",
    "TRANSPORT_EXPENSE", "FINANCE_COST", "IMPAIRMENT_EXPENSE", "TAX_EXPENSE",
    "OTHER_EXPENSE", "CURRENT_ASSET", "NON_CURRENT_ASSET", "CURRENT_LIABILITY",
    "NON_CURRENT_LIABILITY", "EQUITY",
}
DEFAULT_FINANCIAL_CLASSIFICATIONS = {
    "1000": ("CURRENT_ASSET", 10), "1010": ("CURRENT_ASSET", 20),
    "1100": ("CURRENT_ASSET", 30), "1110": ("CURRENT_ASSET", 40),
    "1120": ("CURRENT_ASSET", 50), "1990": ("CURRENT_ASSET", 90),
    "2000": ("CURRENT_LIABILITY", 10), "2100": ("CURRENT_LIABILITY", 20),
    "3000": ("EQUITY", 10), "3100": ("EQUITY", 20),
    "4000": ("OPERATING_INCOME", 10), "4010": ("OPERATING_INCOME", 20),
    "4020": ("OPERATING_INCOME", 30), "5000": ("STAFF_EXPENSE", 10),
    "5010": ("ADMIN_EXPENSE", 20), "5020": ("ADMIN_EXPENSE", 30),
    "5030": ("TRANSPORT_EXPENSE", 40), "5040": ("ADMIN_EXPENSE", 50),
    "5050": ("IMPAIRMENT_EXPENSE", 60),
}
INCOME_SECTION_NAMES = {"OPERATING_INCOME": "Operating Income", "OTHER_INCOME": "Other Income", None: "UNCLASSIFIED INCOME"}
EXPENSE_SECTION_NAMES = {"STAFF_EXPENSE":"Staff Costs","ADMIN_EXPENSE":"Administrative Expenses","TRANSPORT_EXPENSE":"Transport and Collection Expenses","FINANCE_COST":"Finance Costs","IMPAIRMENT_EXPENSE":"Loan Impairment / Write-off Expense","TAX_EXPENSE":"Tax Expense","OTHER_EXPENSE":"Other Expenses", None:"UNCLASSIFIED EXPENSE"}


def seed_default_report_classifications():
    seed_default_accounts()
    for code, (group, order) in DEFAULT_FINANCIAL_CLASSIFICATIONS.items():
        acct = AccountingAccount.query.filter_by(account_code=code).first()
        if acct:
            if not getattr(acct, "financial_statement_group", None):
                acct.financial_statement_group = group
            if getattr(acct, "financial_statement_order", None) is None:
                acct.financial_statement_order = order
    db.session.flush()


def _fmt(value): return f"{money(value):.2f}"
def _today(): return date.today()
def _posted_filter(): return func.upper(AccountingJournalEntry.status).in_(["POSTED", "REVERSED"])

def _account_depth(account):
    depth=0; p=account.parent
    while p: depth += 1; p = p.parent
    return depth

def _signed_balance(account, debit, credit):
    debit=money(debit); credit=money(credit)
    return money(debit-credit) if account.normal_balance == "DEBIT" else money(credit-debit)

def _side_amounts(account, signed):
    signed=money(signed)
    side = account.normal_balance if signed >= 0 else ("CREDIT" if account.normal_balance == "DEBIT" else "DEBIT")
    return (abs(signed), Decimal("0.00"), side) if side == "DEBIT" else (Decimal("0.00"), abs(signed), side)

def _sum_by_account(date_to=None, date_from=None, account_types=None):
    q = db.session.query(AccountingJournalLine.account_id, func.coalesce(func.sum(AccountingJournalLine.debit), 0), func.coalesce(func.sum(AccountingJournalLine.credit), 0)).join(AccountingJournalEntry).filter(_posted_filter())
    if date_from: q = q.filter(AccountingJournalEntry.journal_date >= date_from)
    if date_to: q = q.filter(AccountingJournalEntry.journal_date <= date_to)
    if account_types: q = q.join(AccountingAccount).filter(AccountingAccount.account_type.in_(account_types))
    return {r[0]: (money(r[1]), money(r[2])) for r in q.group_by(AccountingJournalLine.account_id).all()}

def _warnings():
    issues = reconciliation_issues()
    return [{"code":"INCOMPLETE_ACCOUNTING_HISTORY","message":"Some operational transactions have no accounting journals."}] if any("MISSING" in i.get("type", "") for i in issues) else []

def _validate_posted_journals():
    issues=[]
    for e in AccountingJournalEntry.query.filter(_posted_filter()).all():
        td=sum((money(l.debit) for l in e.lines), Decimal("0.00")); tc=sum((money(l.credit) for l in e.lines), Decimal("0.00"))
        if td != tc or money(e.total_debit) != td or money(e.total_credit) != tc:
            issues.append({"type":"POSTED_JOURNAL_TOTAL_MISMATCH","journal_id":e.id})
    return issues

def trial_balance_report(as_of_date=None, date_from=None, include_zero_balances=False, account_type=None, account_id=None, comparative_as_of_date=None):
    seed_default_report_classifications(); as_of_date = as_of_date or _today()
    accounts_q = AccountingAccount.query
    if account_type: accounts_q=accounts_q.filter_by(account_type=account_type)
    if account_id: accounts_q=accounts_q.filter_by(id=int(account_id))
    accounts=accounts_q.order_by(AccountingAccount.account_code).all()
    opening_map = _sum_by_account(date_to=(date_from - timedelta(days=1)) if date_from else None) if date_from else {}
    period_map = _sum_by_account(date_from=date_from, date_to=as_of_date) if date_from else _sum_by_account(date_to=as_of_date)
    comp_map = _sum_by_account(date_to=comparative_as_of_date) if comparative_as_of_date else {}
    rows=[]; totals={k:Decimal('0.00') for k in ['opening_debit','opening_credit','period_debit','period_credit','closing_debit','closing_credit']}
    for a in accounts:
        od,oc = opening_map.get(a.id, (Decimal('0.00'), Decimal('0.00'))); pd,pc = period_map.get(a.id, (Decimal('0.00'), Decimal('0.00')))
        opening_signed=_signed_balance(a, od, oc); closing_signed=_signed_balance(a, od+pd, oc+pc)
        opening_debit, opening_credit, _ = _side_amounts(a, opening_signed); closing_debit, closing_credit, side = _side_amounts(a, closing_signed)
        if not include_zero_balances and not any([opening_debit, opening_credit, pd, pc, closing_debit, closing_credit]): continue
        for k,v in [('opening_debit',opening_debit),('opening_credit',opening_credit),('period_debit',pd),('period_credit',pc),('closing_debit',closing_debit),('closing_credit',closing_credit)]: totals[k]+=money(v)
        cd,cc = comp_map.get(a.id, (Decimal('0.00'), Decimal('0.00'))); comp=_signed_balance(a, cd, cc) if comparative_as_of_date else None
        rows.append({"account_id":a.id,"account_code":a.account_code,"account_name":a.account_name,"account_type":a.account_type,"account_subtype":account_subtype(a),"normal_balance":a.normal_balance,"opening_debit":_fmt(opening_debit),"opening_credit":_fmt(opening_credit),"period_debit":_fmt(pd),"period_credit":_fmt(pc),"closing_debit":_fmt(closing_debit),"closing_credit":_fmt(closing_credit),"net_balance":_fmt(closing_signed),"balance_side":side,"comparative_net_balance": _fmt(comp) if comparative_as_of_date else None,"is_parent":bool(a.children),"depth":_account_depth(a),"parent_id":a.parent_id,"display_order":a.financial_statement_order,"parent_totals_included":False,"drilldown":{"account_id":a.id,"date_from":date_from.isoformat() if date_from else None,"date_to":as_of_date.isoformat()}})
    diff=money(totals['closing_debit']-totals['closing_credit']); issues=_validate_posted_journals()
    if diff != 0: issues.append({"type":"UNBALANCED_TRIAL_BALANCE","difference":_fmt(diff)})
    return {"report":"TRIAL_BALANCE","as_of_date":as_of_date.isoformat(),"date_from":date_from.isoformat() if date_from else None,"accounts":rows,"totals":{f"total_{k}":_fmt(v) for k,v in totals.items()} | {"difference":_fmt(diff),"is_balanced":diff==0},"validation":{"is_valid":not issues,"difference":_fmt(diff),"issues":issues},"warnings":_warnings()}

def _account_amounts(types, date_from=None, date_to=None): return _sum_by_account(date_from=date_from, date_to=date_to, account_types=types)

def _variance(amount, comp):
    var=money(amount-comp); pct=None if comp==0 else money((var/abs(comp))*Decimal('100'))
    return _fmt(var), (_fmt(pct) if pct is not None else None)

def income_statement_report(date_from, date_to, comparative_date_from=None, comparative_date_to=None, include_zero_balances=False):
    seed_default_report_classifications(); data=_account_amounts(['INCOME','EXPENSE'], date_from, date_to); comp=_account_amounts(['INCOME','EXPENSE'], comparative_date_from, comparative_date_to) if comparative_date_from and comparative_date_to else {}
    sections_i={}; sections_e={}; total_i=total_e=tax=Decimal('0.00'); issues=[]
    for a in AccountingAccount.query.filter(AccountingAccount.account_type.in_(['INCOME','EXPENSE'])).order_by(AccountingAccount.financial_statement_order, AccountingAccount.account_code).all():
        d,c=data.get(a.id,(Decimal('0.00'),Decimal('0.00'))); amount=money(c-d) if a.account_type=='INCOME' else money(d-c)
        cd,cc=comp.get(a.id,(Decimal('0.00'),Decimal('0.00'))); camount=money(cc-cd) if a.account_type=='INCOME' else money(cd-cc)
        if not include_zero_balances and amount==0 and camount==0: continue
        group=a.financial_statement_group if a.financial_statement_group in FINANCIAL_STATEMENT_GROUPS else None
        if group is None: issues.append({"type":"UNCLASSIFIED_ACCOUNT","account_id":a.id})
        var,pct=_variance(amount,camount); row={"account_id":a.id,"account_code":a.account_code,"account_name":a.account_name,"amount":_fmt(amount),"comparative_amount":_fmt(camount) if comp else None,"variance":var if comp else None,"variance_percent":pct if comp else None,"drilldown":{"account_id":a.id,"date_from":date_from.isoformat(),"date_to":date_to.isoformat()}}
        bucket=sections_i if a.account_type=='INCOME' else sections_e; name=(INCOME_SECTION_NAMES if a.account_type=='INCOME' else EXPENSE_SECTION_NAMES).get(group, (INCOME_SECTION_NAMES if a.account_type=='INCOME' else EXPENSE_SECTION_NAMES)[None])
        bucket.setdefault(name, []).append(row)
        if a.account_type=='INCOME': total_i += amount
        else:
            total_e += amount
            if group == 'TAX_EXPENSE': tax += amount
    def pack(sections): return [{"section_name":k,"accounts":v,"total":_fmt(sum(Decimal(x['amount']) for x in v))} for k,v in sections.items()]
    net=money(total_i-total_e); diff=money(total_i-total_e-net)
    return {"report":"INCOME_STATEMENT","date_from":date_from.isoformat(),"date_to":date_to.isoformat(),"income":{"sections":pack(sections_i),"total_income":_fmt(total_i)},"expenses":{"sections":pack(sections_e),"total_expenses":_fmt(total_e)},"profit_before_tax":_fmt(total_i-total_e+tax),"tax_expense":_fmt(tax),"net_profit":_fmt(net),"validation":{"is_valid":diff==0 and not issues,"difference":_fmt(diff),"issues":issues},"warnings":_warnings()}

def _balance_sheet_account_rows(types, as_of_date, comparative_as_of_date=None, include_zero_balances=False):
    data=_sum_by_account(date_to=as_of_date, account_types=types); comp=_sum_by_account(date_to=comparative_as_of_date, account_types=types) if comparative_as_of_date else {}; rows=[]
    for a in AccountingAccount.query.filter(AccountingAccount.account_type.in_(types)).order_by(AccountingAccount.financial_statement_order, AccountingAccount.account_code).all():
        d,c=data.get(a.id,(Decimal('0.00'),Decimal('0.00'))); amount=_signed_balance(a,d,c)
        cd,cc=comp.get(a.id,(Decimal('0.00'),Decimal('0.00'))); camount=_signed_balance(a,cd,cc)
        if not include_zero_balances and amount==0 and camount==0: continue
        rows.append({"account_id":a.id,"account_code":a.account_code,"account_name":a.account_name,"account_type":a.account_type,"financial_statement_group":a.financial_statement_group,"amount":_fmt(amount),"comparative_amount":_fmt(camount) if comparative_as_of_date else None,"drilldown":{"account_id":a.id,"date_from":None,"date_to":as_of_date.isoformat()}})
    return rows

def statement_of_financial_position_report(as_of_date=None, comparative_as_of_date=None, include_zero_balances=False):
    seed_default_report_classifications(); as_of_date=as_of_date or _today(); rows=_balance_sheet_account_rows(['ASSET','LIABILITY','EQUITY'], as_of_date, comparative_as_of_date, include_zero_balances)
    ca=[r for r in rows if r['financial_statement_group'] in (None,'CURRENT_ASSET') and r['account_type']=='ASSET']; nca=[r for r in rows if r['financial_statement_group']=='NON_CURRENT_ASSET']
    cl=[r for r in rows if r['financial_statement_group'] in (None,'CURRENT_LIABILITY') and r['account_type']=='LIABILITY']; ncl=[r for r in rows if r['financial_statement_group']=='NON_CURRENT_LIABILITY']
    eq=[r for r in rows if r['account_type']=='EQUITY']
    pl=income_statement_report(date(1900,1,1), as_of_date)['net_profit']; pl_dec=Decimal(pl)
    ta=sum(Decimal(r['amount']) for r in ca+nca); tl=sum(Decimal(r['amount']) for r in cl+ncl); eq_accounts=sum(Decimal(r['amount']) for r in eq); te=money(eq_accounts+pl_dec); diff=money(ta-(tl+te)); issues=[]
    if diff != 0: issues.append({"type":"UNBALANCED_STATEMENT_OF_FINANCIAL_POSITION","difference":_fmt(diff)})
    return {"report":"STATEMENT_OF_FINANCIAL_POSITION","as_of_date":as_of_date.isoformat(),"assets":{"current_assets":{"accounts":ca,"total":_fmt(sum(Decimal(r['amount']) for r in ca))},"non_current_assets":{"accounts":nca,"total":_fmt(sum(Decimal(r['amount']) for r in nca))},"total_assets":_fmt(ta)},"liabilities":{"current_liabilities":{"accounts":cl,"total":_fmt(sum(Decimal(r['amount']) for r in cl))},"non_current_liabilities":{"accounts":ncl,"total":_fmt(sum(Decimal(r['amount']) for r in ncl))},"total_liabilities":_fmt(tl)},"equity":{"accounts":eq,"retained_earnings":"0.00","current_period_profit_loss":_fmt(pl_dec),"total_equity":_fmt(te),"policy":"Current period profit/loss is cumulative posted income less expenses through the as-of date until year-end closing is implemented; retained earnings account balance remains in equity accounts."},"total_liabilities_and_equity":_fmt(tl+te),"difference":_fmt(diff),"is_balanced":diff==0,"validation":{"is_valid":not issues,"difference":_fmt(diff),"issues":issues},"warnings":_warnings()}

def reports_summary(date_from=None, date_to=None, as_of_date=None):
    as_of_date=as_of_date or date_to or _today(); date_from=date_from or date(as_of_date.year,1,1); date_to=date_to or as_of_date
    isr=income_statement_report(date_from,date_to); sfp=statement_of_financial_position_report(as_of_date); tb=trial_balance_report(as_of_date)
    unclassified=AccountingAccount.query.filter(AccountingAccount.account_type.in_(['INCOME','EXPENSE','ASSET','LIABILITY','EQUITY']), AccountingAccount.financial_statement_group.is_(None)).count()
    return {"total_assets":sfp['assets']['total_assets'],"total_liabilities":sfp['liabilities']['total_liabilities'],"total_equity":sfp['equity']['total_equity'],"total_income":isr['income']['total_income'],"total_expenses":isr['expenses']['total_expenses'],"net_profit":isr['net_profit'],"trial_balance_difference":tb['totals']['difference'],"statement_of_financial_position_difference":sfp['difference'],"unclassified_account_count":unclassified}

def report_csv(data, generated_by=None):
    out=StringIO(); w=csv.writer(out); w.writerow([data.get('report','REPORT')]); w.writerow(['Generated At', datetime.utcnow().isoformat(), 'Generated By', generated_by or 'system'])
    if data['report']=='TRIAL_BALANCE':
        w.writerow(['As Of', data['as_of_date']]); w.writerow(['Section','Account Code','Account Name','Debit','Credit','Amount'])
        for r in data['accounts']: w.writerow(['Trial Balance',r['account_code'],r['account_name'],r['closing_debit'],r['closing_credit'],r['net_balance']])
        w.writerow(['Totals','','',data['totals']['total_closing_debit'],data['totals']['total_closing_credit'],data['totals']['difference']])
    elif data['report']=='INCOME_STATEMENT':
        w.writerow(['Period', data['date_from'], data['date_to']]); w.writerow(['Section','Account Code','Account Name','Debit','Credit','Amount'])
        for part in ['income','expenses']:
            for sec in data[part]['sections']:
                for r in sec['accounts']: w.writerow([sec['section_name'],r['account_code'],r['account_name'],'','',f"Rs. {Decimal(r['amount']):,.2f}"])
                w.writerow([sec['section_name']+' Total','','','','',sec['total']])
        w.writerow(['Net Profit','','','','',data['net_profit']])
    else:
        w.writerow(['As Of', data['as_of_date']]); w.writerow(['Section','Account Code','Account Name','Debit','Credit','Amount'])
        for top in [('Current Assets',data['assets']['current_assets']),('Non-current Assets',data['assets']['non_current_assets']),('Current Liabilities',data['liabilities']['current_liabilities']),('Non-current Liabilities',data['liabilities']['non_current_liabilities'])]:
            for r in top[1]['accounts']: w.writerow([top[0],r['account_code'],r['account_name'],'','',f"Rs. {Decimal(r['amount']):,.2f}"])
            w.writerow([top[0]+' Total','','','','',top[1]['total']])
        for r in data['equity']['accounts']: w.writerow(['Equity',r['account_code'],r['account_name'],'','',f"Rs. {Decimal(r['amount']):,.2f}"])
        w.writerow(['Total Liabilities and Equity','','','','',data['total_liabilities_and_equity']])
    return out.getvalue()
