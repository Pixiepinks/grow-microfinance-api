from __future__ import annotations

import csv
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from io import StringIO

from sqlalchemy import func, text

from .extensions import db
from .models import (
    AccountingAccount,
    AccountingAuditLog,
    AccountingJournalEntry,
    AccountingJournalLine,
    AccountingSetting,
    Loan,
    LoanLedger,
    Payment,
    LoanApplication,
)

CENT = Decimal("0.01")
ACCOUNT_TYPES = {"ASSET", "LIABILITY", "EQUITY", "INCOME", "EXPENSE"}
NORMAL_BALANCES = {"DEBIT", "CREDIT"}
SYSTEM_MAPPINGS = {
    "DEFAULT_DISBURSEMENT_ACCOUNT": "1010",
    "CASH_ACCOUNT": "1000",
    "BANK_ACCOUNT": "1010",
    "LOAN_RECEIVABLE_ACCOUNT": "1100",
    "INTEREST_RECEIVABLE_ACCOUNT": "1110",
    "PENALTY_RECEIVABLE_ACCOUNT": "1120",
    "INTEREST_INCOME_ACCOUNT": "4000",
    "PENALTY_INCOME_ACCOUNT": "4010",
    "OTHER_FEE_INCOME_ACCOUNT": "4020",
}
DEFAULT_ACCOUNTS = [
    ("1000", "Cash on Hand", "ASSET", "DEBIT", True, "CASH"),
    ("1010", "Main Bank Account", "ASSET", "DEBIT", True, "BANK"),
    ("1100", "Loan Principal Receivable", "ASSET", "DEBIT", True, "RECEIVABLE"),
    ("1110", "Interest Receivable", "ASSET", "DEBIT", True, "RECEIVABLE"),
    ("1120", "Penalty Receivable", "ASSET", "DEBIT", True, "RECEIVABLE"),
    ("2000", "Accounts Payable", "LIABILITY", "CREDIT", False, "NONE"),
    ("2100", "Borrowings", "LIABILITY", "CREDIT", False, "NONE"),
    ("3000", "Owner's Capital", "EQUITY", "CREDIT", False, "NONE"),
    ("3100", "Retained Earnings", "EQUITY", "CREDIT", False, "NONE"),
    ("4000", "Interest Income", "INCOME", "CREDIT", True, "NONE"),
    ("4010", "Penalty Income", "INCOME", "CREDIT", True, "NONE"),
    ("4020", "Processing Fee Income", "INCOME", "CREDIT", False, "NONE"),
    ("5000", "Salary Expense", "EXPENSE", "DEBIT", False, "NONE"),
    ("5010", "Rent Expense", "EXPENSE", "DEBIT", False, "NONE"),
    ("5020", "Utilities Expense", "EXPENSE", "DEBIT", False, "NONE"),
    ("5030", "Transport Expense", "EXPENSE", "DEBIT", False, "NONE"),
    ("5040", "Office Expense", "EXPENSE", "DEBIT", False, "NONE"),
]

class AccountingError(ValueError):
    pass

def money(value) -> Decimal:
    return Decimal(str(value or "0")).quantize(CENT, rounding=ROUND_HALF_UP)

def log_audit(action, entity_type, entity_id=None, user_id=None, details=None):
    db.session.add(AccountingAuditLog(action=action, entity_type=entity_type, entity_id=str(entity_id) if entity_id else None, user_id=user_id, details=details))

def seed_default_accounts():
    for code, name, typ, normal, system, category in DEFAULT_ACCOUNTS:
        acct = AccountingAccount.query.filter_by(account_code=code).first()
        if not acct:
            db.session.add(AccountingAccount(account_code=code, account_name=name, account_type=typ, normal_balance=normal, is_system_account=system, cash_flow_category=category))
        else:
            acct.is_system_account = bool(system) or acct.is_system_account
            if getattr(acct, "cash_flow_category", None) in (None, "NONE") and category != "NONE":
                acct.cash_flow_category = category
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

def general_ledger(account_id, date_from=None, date_to=None, customer_id=None, loan_id=None):
    account = AccountingAccount.query.get(account_id)
    if not account: raise AccountingError("Account not found")
    q = AccountingJournalLine.query.join(AccountingJournalEntry).filter(AccountingJournalLine.account_id==account_id, AccountingJournalEntry.status.in_(["POSTED", "REVERSED"]))
    if customer_id: q=q.filter(AccountingJournalLine.customer_id==customer_id)
    if loan_id: q=q.filter(AccountingJournalLine.loan_id==loan_id)
    before=q
    if date_from: before=before.filter(AccountingJournalEntry.journal_date < date_from); q=q.filter(AccountingJournalEntry.journal_date >= date_from)
    if date_to: q=q.filter(AccountingJournalEntry.journal_date <= date_to)
    def signed(line): return money(line.debit-line.credit) if account.normal_balance=="DEBIT" else money(line.credit-line.debit)
    opening=sum((signed(l) for l in before.all()), Decimal("0.00")) if date_from else Decimal("0.00")
    running=money(opening); tx=[]; td=tc=Decimal("0.00")
    for l in q.order_by(AccountingJournalEntry.journal_date, AccountingJournalEntry.journal_no, AccountingJournalLine.line_no).all():
        running=money(running+signed(l)); td+=money(l.debit); tc+=money(l.credit); e=l.journal_entry
        tx.append({"journal_date": e.journal_date.isoformat(), "journal_no": e.journal_no, "description": e.description, "reference_type": e.reference_type, "reference_id": e.reference_id, "debit": f"{money(l.debit):.2f}", "credit": f"{money(l.credit):.2f}", "running_balance": f"{running:.2f}", "customer_id": l.customer_id, "loan_id": l.loan_id})
    return {"opening_balance": f"{money(opening):.2f}", "transactions": tx, "running_balance": f"{running:.2f}", "closing_balance": f"{running:.2f}", "total_debit": f"{money(td):.2f}", "total_credit": f"{money(tc):.2f}"}

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
