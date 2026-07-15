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
    PaymentAllocation,
    CollectionDepositAllocation,
    AccountingPeriod,
    LoanApplication,
    User,
    DisbursementChargeType,
    LoanDisbursementDeduction,
)

CENT = Decimal("0.01")
ACCOUNT_TYPES = {"ASSET", "LIABILITY", "EQUITY", "INCOME", "EXPENSE"}
NORMAL_BALANCES = {"DEBIT", "CREDIT"}
ACCOUNT_SUBTYPES = {"CASH", "BANK", "COLLECTION_CLEARING", "COLLECTION_CLEARING_CONTROL", "LOAN_RECEIVABLE", "INTEREST_RECEIVABLE", "PENALTY_RECEIVABLE", "OTHER_CURRENT_ASSET", "FIXED_ASSET", "ACCOUNTS_PAYABLE", "BORROWING", "CAPITAL", "RETAINED_EARNINGS", "INTEREST_INCOME", "PENALTY_INCOME", "FEE_INCOME", "OPERATING_EXPENSE", "WRITE_OFF_EXPENSE", "SUSPENSE", "OTHER"}
SYSTEM_MAPPINGS = {
    "DEFAULT_DISBURSEMENT_ACCOUNT": "1010",
    "DEFAULT_CASH_COLLECTION_ACCOUNT": "1000",
    "DEFAULT_BANK_COLLECTION_ACCOUNT": "1010",
    "default_cash_on_hand_account": "1000",
    "default_bank_account": "1010",
    "collector_clearing_control_account": "1050",
    "CASH_ACCOUNT": "1000",
    "BANK_ACCOUNT": "1010",
    "LOAN_RECEIVABLE_ACCOUNT": "1100",
    "LOAN_PRINCIPAL_RECEIVABLE": "1100",
    "INTEREST_RECEIVABLE_ACCOUNT": "1110",
    "INTEREST_RECEIVABLE": "1110",
    "PENALTY_RECEIVABLE_ACCOUNT": "1120",
    "DELAY_INTEREST_RECEIVABLE": "1120",
    "INTEREST_INCOME_ACCOUNT": "4000",
    "LOAN_INTEREST_INCOME": "4000",
    "PENALTY_INCOME_ACCOUNT": "4010",
    "DELAY_INTEREST_INCOME": "4010",
    "UNAPPLIED_CUSTOMER_FUNDS": "1990",
    "DOCUMENTATION_FEE_INCOME": "4030",
    "PROCESSING_FEE_INCOME_ACCOUNT": "4020",
    "PROCESSING_FEE_INCOME": "4020",
    "INSURANCE_PAYABLE": "2200",
    "STAMP_DUTY_PAYABLE": "2210",
    "OUTPUT_TAX_PAYABLE": "2220",
    "OTHER_FEE_INCOME_ACCOUNT": "4020",
    "LOAN_WRITE_OFF_EXPENSE_ACCOUNT": "5050",
    "SUSPENSE_ACCOUNT": "1990",
    "RETAINED_EARNINGS_ACCOUNT": "3100",
}
DEFAULT_ACCOUNTS = [
    ("1000", "Cash on Hand", "ASSET", "DEBIT", True, "CASH"),
    ("1010", "Main Bank Account", "ASSET", "DEBIT", True, "BANK"),
    ("1050", "Collector Cash Clearing – Control", "ASSET", "DEBIT", True, "COLLECTION_CLEARING_CONTROL"),
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
    ("4030", "Documentation Fee Income", "INCOME", "CREDIT", True, "FEE_INCOME"),
    ("4040", "Investigation Fee Income", "INCOME", "CREDIT", True, "FEE_INCOME"),
    ("2200", "Insurance Premium Payable", "LIABILITY", "CREDIT", True, "ACCOUNTS_PAYABLE"),
    ("2210", "Stamp Duty Payable", "LIABILITY", "CREDIT", True, "ACCOUNTS_PAYABLE"),
    ("2220", "VAT Payable", "LIABILITY", "CREDIT", True, "ACCOUNTS_PAYABLE"),
    ("2230", "Other Statutory Charges Payable", "LIABILITY", "CREDIT", True, "ACCOUNTS_PAYABLE"),
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
    "default_cash_on_hand_account": ({"ASSET"}, {"CASH"}),
    "default_bank_account": ({"ASSET"}, {"BANK"}),
    "collector_clearing_control_account": ({"ASSET"}, {"COLLECTION_CLEARING_CONTROL", "COLLECTION_CLEARING"}),
    "LOAN_RECEIVABLE_ACCOUNT": ({"ASSET"}, {"LOAN_RECEIVABLE"}),
    "INTEREST_RECEIVABLE_ACCOUNT": ({"ASSET"}, {"INTEREST_RECEIVABLE"}),
    "PENALTY_RECEIVABLE_ACCOUNT": ({"ASSET"}, {"PENALTY_RECEIVABLE"}),
    "INTEREST_INCOME_ACCOUNT": ({"INCOME"}, {"INTEREST_INCOME", "FEE_INCOME", "OTHER"}),
    "PENALTY_INCOME_ACCOUNT": ({"INCOME"}, {"PENALTY_INCOME", "FEE_INCOME", "OTHER"}),
    "default_documentation_fee_account": ({"INCOME"}, {"FEE_INCOME", "OTHER"}),
    "default_processing_fee_account": ({"INCOME"}, {"FEE_INCOME", "OTHER"}),
    "default_insurance_payable_account": ({"LIABILITY"}, {"ACCOUNTS_PAYABLE", "OTHER"}),
    "default_stamp_duty_payable_account": ({"LIABILITY"}, {"ACCOUNTS_PAYABLE", "OTHER"}),
    "default_tax_payable_account": ({"LIABILITY"}, {"ACCOUNTS_PAYABLE", "OTHER"}),
    "DOCUMENTATION_FEE_INCOME": ({"INCOME"}, {"FEE_INCOME", "OTHER"}),
    "PROCESSING_FEE_INCOME_ACCOUNT": ({"INCOME"}, {"FEE_INCOME", "OTHER"}),
    "LOAN_WRITE_OFF_EXPENSE_ACCOUNT": ({"EXPENSE"}, {"WRITE_OFF_EXPENSE", "OPERATING_EXPENSE", "OTHER"}),
    "SUSPENSE_ACCOUNT": ({"ASSET", "LIABILITY"}, {"SUSPENSE", "OTHER_CURRENT_ASSET", "OTHER"}),
    "LOAN_PRINCIPAL_RECEIVABLE": ({"ASSET"}, {"LOAN_RECEIVABLE"}),
    "INTEREST_RECEIVABLE": ({"ASSET"}, {"INTEREST_RECEIVABLE"}),
    "DELAY_INTEREST_RECEIVABLE": ({"ASSET"}, {"PENALTY_RECEIVABLE"}),
    "LOAN_INTEREST_INCOME": ({"INCOME"}, {"INTEREST_INCOME", "FEE_INCOME", "OTHER"}),
    "DELAY_INTEREST_INCOME": ({"INCOME"}, {"PENALTY_INCOME", "FEE_INCOME", "OTHER"}),
    "UNAPPLIED_CUSTOMER_FUNDS": ({"ASSET", "LIABILITY"}, {"SUSPENSE", "ACCOUNTS_PAYABLE", "OTHER"}),
    "RETAINED_EARNINGS_ACCOUNT": ({"EQUITY"}, {"RETAINED_EARNINGS"}),
}

class AccountingError(ValueError):
    pass


class ValidationError(AccountingError):
    def __init__(self, error, **payload):
        super().__init__(error)
        self.message = error
        self.payload = {"error": error, **payload}
        if "message" not in self.payload:
            self.payload["message"] = error


def parse_positive_int(value, field_name):
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{field_name} must be a valid integer")
    if result <= 0:
        raise ValidationError(f"{field_name} must be greater than zero")
    return result

def money(value) -> Decimal:
    return Decimal(str(value or "0")).quantize(CENT, rounding=ROUND_HALF_UP)

def log_audit(action, entity_type, entity_id=None, user_id=None, details=None):
    db.session.add(AccountingAuditLog(action=action, entity_type=entity_type, entity_id=str(entity_id) if entity_id else None, user_id=user_id, details=str(details) if details is not None else None))

def seed_default_accounts():
    try:
        for code, name, typ, normal, system, category in DEFAULT_ACCOUNTS:
            account_data = {
                "account_code": code,
                "account_name": name,
                "account_type": typ,
                "normal_balance": normal,
                "is_system_account": system,
                "cash_flow_category": "RECEIVABLE" if "RECEIVABLE" in category else category,
                "account_subtype": category,
                "allow_manual_posting": False if code == "1050" else True,
                "is_collection_account": False,
                "is_active": True,
            }
            with db.session.no_autoflush:
                acct = AccountingAccount.query.filter_by(account_code=code).first()
            if not acct:
                db.session.add(AccountingAccount(**account_data))
                continue

            acct.is_system_account = bool(system) or acct.is_system_account
            if not account_has_activity(acct):
                acct.account_name = name
                acct.account_type = typ
                acct.normal_balance = normal
                acct.account_subtype = category
            elif not getattr(acct, "account_subtype", None):
                acct.account_subtype = category
            if code == "1050":
                acct.account_name = "Collector Cash Clearing – Control"
                acct.account_type = "ASSET"
                acct.normal_balance = "DEBIT"
                acct.account_subtype = "COLLECTION_CLEARING_CONTROL"
                acct.cash_flow_category = "COLLECTION_CLEARING_CONTROL"
                acct.is_collection_account = False
                acct.is_system_account = True
                acct.allow_manual_posting = False
                acct.is_active = True
                acct.collector_id = None
            elif getattr(acct, "cash_flow_category", None) in (None, "NONE") and category != "NONE":
                acct.cash_flow_category = "RECEIVABLE" if "RECEIVABLE" in category else category
        db.session.flush()
        for key, code in SYSTEM_MAPPINGS.items():
            with db.session.no_autoflush:
                setting = AccountingSetting.query.filter_by(setting_key=key).first()
            if not setting:
                db.session.add(AccountingSetting(setting_key=key, setting_value=code))
        db.session.flush()
    except Exception:
        db.session.rollback()
        raise


DISBURSEMENT_SETTING_DEFAULTS = {"default_documentation_fee_account":"4030","default_processing_fee_account":"4020","default_insurance_payable_account":"2200","default_stamp_duty_payable_account":"2210","default_tax_payable_account":"2220","allow_manual_disbursement_charges":"true","require_disbursement_charge_approval":"false","allow_zero_net_disbursement":"false","allow_deductions_exceeding_principal":"false","default_charge_tax_method":"NO_TAX","show_charges_on_customer_receipt":"true"}

def setting_bool(key, default=False):
    return str(get_setting(key, str(default).lower())).lower() in {"1","true","yes","on"}

def seed_disbursement_settings():
    seed_default_accounts()
    for key, value in DISBURSEMENT_SETTING_DEFAULTS.items():
        if not AccountingSetting.query.filter_by(setting_key=key).first():
            db.session.add(AccountingSetting(setting_key=key, setting_value=value))
    db.session.flush()

def resolve_system_account(key):
    seed_default_accounts()
    setting = AccountingSetting.query.filter_by(setting_key=key).first()
    code = setting.setting_value if setting else SYSTEM_MAPPINGS.get(key)
    account = None
    if code:
        account = AccountingAccount.query.get(int(code)) if str(code).isdigit() else None
        account = account or AccountingAccount.query.filter_by(account_code=str(code)).first()
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
    subtype = data.get("account_subtype") or data.get("subtype") or "OTHER"
    is_collection = bool(data.get("is_collection_account")) or subtype == "COLLECTION_CLEARING"
    collector_id = data.get("collector_id")
    if is_collection:
        if typ != "ASSET" or normal != "DEBIT":
            raise AccountingError("Collector collection accounts must be debit ASSET accounts")
        subtype = "COLLECTION_CLEARING"
        if collector_id and AccountingAccount.query.filter_by(collector_id=int(collector_id), is_collection_account=True, is_active=True).first():
            raise AccountingError("Only one active default collection account is allowed per collector")
    acct = AccountingAccount(account_code=data["account_code"], account_name=data["account_name"], account_type=typ, normal_balance=normal, parent_id=parent_id, parent_account_id=data.get("parent_account_id"), collector_id=collector_id, is_collection_account=is_collection, account_role=data.get("account_role"), account_subtype=subtype, description=data.get("description"), is_active=data.get("is_active", True), allow_manual_posting=data.get("allow_manual_posting", True), cash_flow_category=data.get("cash_flow_category", "NONE"))
    db.session.add(acct); db.session.flush(); log_audit("ACCOUNT_CREATE", "AccountingAccount", acct.id, user_id); return acct

def update_account(acct, data, user_id=None):
    for field in ["account_name", "description", "is_active", "allow_manual_posting", "cash_flow_category", "account_role", "parent_account_id"]:
        if field in data: setattr(acct, field, data[field])
    if "collector_id" in data: acct.collector_id = data["collector_id"]
    if "is_collection_account" in data or data.get("account_subtype") == "COLLECTION_CLEARING":
        acct.is_collection_account = bool(data.get("is_collection_account", acct.is_collection_account)) or data.get("account_subtype") == "COLLECTION_CLEARING"
        if acct.is_collection_account:
            if acct.account_type != "ASSET" or acct.normal_balance != "DEBIT": raise AccountingError("Collector collection accounts must be debit ASSET accounts")
            acct.account_subtype = "COLLECTION_CLEARING"
    log_audit("ACCOUNT_UPDATE", "AccountingAccount", acct.id, user_id); return acct

def _line_from_payload(raw, line_no):
    return AccountingJournalLine(line_no=line_no, account_id=raw["account_id"], debit=money(raw.get("debit")), credit=money(raw.get("credit")), customer_id=raw.get("customer_id"), loan_id=raw.get("loan_id"), payment_id=raw.get("payment_id"), collection_id=raw.get("collection_id"), description=raw.get("description"))

def create_draft_journal(journal_date, description, lines, reference_type="MANUAL_JOURNAL", reference_id=None, source_module="ACCOUNTING", created_by_id=None, idempotency_key=None):
    if idempotency_key:
        existing = AccountingJournalEntry.query.filter_by(idempotency_key=idempotency_key).first()
        if existing: return existing
    entry = AccountingJournalEntry(journal_no=generate_journal_number(journal_date), journal_date=journal_date, accounting_date=journal_date, description=description, reference_type=reference_type, reference_id=str(reference_id) if reference_id is not None else None, source_type=reference_type, source_id=int(reference_id) if str(reference_id).isdigit() else None, source_module=source_module, created_by_id=created_by_id, idempotency_key=idempotency_key, status="DRAFT")
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
        acct = db.session.get(AccountingAccount, line.account_id)
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
    reversal.reversal_of_id = entry.id; reversal.reversal_of_journal_id = entry.id; reversal.is_reversal = True; post_journal(reversal, user_id); entry.status = "REVERSED"; log_audit("JOURNAL_REVERSE", "AccountingJournalEntry", entry.id, user_id, reason); return reversal

def validate_funding_account(account, *_args):
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

def post_loan_payment(payment, user_id=None, receipt_account=None):
    if AccountingJournalEntry.query.filter_by(idempotency_key=f"LOAN_PAYMENT:{payment.id}").first():
        return AccountingJournalEntry.query.filter_by(idempotency_key=f"LOAN_PAYMENT:{payment.id}").first()
    total = money(payment.amount_collected); principal=money(payment.principal_paid); interest=money(payment.interest_paid); penalty=money(payment.penalty_paid); other=money(payment.other_fee_paid)
    if money(principal+interest+penalty+other) != total: raise AccountingError("Payment allocation does not match amount collected")
    loan = payment.loan
    lines=[{"account_id": (receipt_account or resolve_system_account("CASH_ACCOUNT" if str(payment.payment_method).lower()=="cash" else "BANK_ACCOUNT")).id, "debit": total, "customer_id": loan.customer_id, "loan_id": loan.id, "payment_id": payment.id}]
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

def _issue(issue_type, severity, source_type, source_id, source_reference, description, route_type=None, route_id=None, **extra):
    if not issue_type or not severity or not source_type or source_id is None or not description:
        return None
    detected_at = datetime.utcnow().replace(microsecond=0).isoformat() + "+00:00"
    legacy_type = {"MISSING_PAYMENT_JOURNAL":"MISSING_LOAN_PAYMENT_JOURNAL", "MISSING_DISBURSEMENT_JOURNAL":"MISSING_LOAN_DISBURSEMENT_JOURNAL"}.get(issue_type, issue_type)
    payload = {
        "id": f"{issue_type}:{source_type}:{source_id}:{extra.get('journal_id') or extra.get('payment_id') or ''}",
        "type": legacy_type,
        "issue_type": issue_type,
        "severity": severity,
        "source_type": source_type,
        "source_id": source_id,
        "source_reference": source_reference,
        "description": description,
        "detected_at": detected_at,
        "action": {"route_type": route_type or source_type, "route_id": route_id if route_id is not None else source_id},
    }
    payload.update({k: v for k, v in extra.items() if v is not None})
    return payload

def _backfill_metadata(issue_type, loan=None, payment=None):
    can_backfill = issue_type in ("MISSING_DISBURSEMENT_JOURNAL", "MISSING_PAYMENT_JOURNAL")
    block_reason = None
    if issue_type == "MISSING_PAYMENT_JOURNAL" and payment:
        total = money(payment.amount_collected)
        allocated = money(money(payment.principal_paid) + money(payment.interest_paid) + money(payment.penalty_paid) + money(payment.other_fee_paid))
        if allocated != total:
            can_backfill = False
            block_reason = "Stored payment allocation does not match amount collected"
    return {
        "customer_name": getattr(getattr(loan, "customer", None), "full_name", None),
        "loan_number": getattr(loan, "loan_number", None),
        "payment_reference": getattr(payment, "transaction_reference", None) or (str(payment.id) if payment else None),
        "recommended_action": "Run the matching dry-run accounting backfill command, review the result, then rerun with --apply." if can_backfill else "Review source data before attempting backfill.",
        "can_backfill": can_backfill,
        "backfill_block_reason": block_reason,
    }

def reconciliation_issues():
    issues=[]
    for loan in Loan.query.filter(Loan.status.in_(["Active","ACTIVE"])).all():
        journals=AccountingJournalEntry.query.filter_by(reference_type="LOAN_DISBURSEMENT", reference_id=str(loan.id)).all()
        if not journals:
            item=_issue("MISSING_DISBURSEMENT_JOURNAL", "WARNING", "LOAN", loan.id, loan.loan_number, "Active loan has no posted disbursement journal.", "LOAN", loan.id, **_backfill_metadata("MISSING_DISBURSEMENT_JOURNAL", loan=loan))
            if item: issues.append(item)
        if len(journals)>1:
            item=_issue("DUPLICATE_DISBURSEMENT_JOURNAL", "ERROR", "LOAN", loan.id, loan.loan_number, "Loan has more than one disbursement journal.", "LOAN", loan.id, count=len(journals))
            if item: issues.append(item)
        for j in journals:
            if money(j.total_debit) != money(loan.principal_amount) or money(j.total_credit) != money(loan.principal_amount):
                item=_issue("JOURNAL_TOTAL_MISMATCH", "ERROR", "LOAN", loan.id, loan.loan_number, "Loan disbursement journal amount does not match loan principal.", "JOURNAL", j.id, journal_id=j.id)
                if item: issues.append(item)
            credits=[l for l in j.lines if money(l.credit)>0]
            if len(credits)!=1 or credits[0].account.cash_flow_category not in ("CASH","BANK") or credits[0].account.account_type != "ASSET" or not credits[0].account.is_active or not credits[0].account.allow_manual_posting:
                item=_issue("INVALID_DISBURSEMENT_FUNDING_ACCOUNT", "ERROR", "LOAN", loan.id, loan.loan_number, "Loan disbursement journal uses an invalid funding account.", "JOURNAL", j.id, journal_id=j.id)
                if item: issues.append(item)
    for p in Payment.query.all():
        if not AccountingJournalEntry.query.filter_by(reference_type="LOAN_PAYMENT", reference_id=str(p.id)).first():
            loan = p.loan
            item=_issue("MISSING_PAYMENT_JOURNAL", "WARNING", "PAYMENT", p.id, getattr(loan, "loan_number", None), "Payment has no posted accounting journal.", "PAYMENT", p.id, payment_id=p.id, **_backfill_metadata("MISSING_PAYMENT_JOURNAL", loan=loan, payment=p))
            if item: issues.append(item)
    for e in AccountingJournalEntry.query.all():
        td=sum((money(l.debit) for l in e.lines), Decimal("0.00")); tc=sum((money(l.credit) for l in e.lines), Decimal("0.00"))
        if td != tc or money(e.total_debit)!=td or money(e.total_credit)!=tc:
            item=_issue("JOURNAL_TOTAL_MISMATCH", "ERROR", "JOURNAL", e.id, e.journal_no, "Journal line totals do not match header totals or are unbalanced.", "JOURNAL", e.id, journal_id=e.id)
            if item: issues.append(item)
    return issues

def reconciliation_summary():
    issues = reconciliation_issues()
    counts_by_severity = {}
    counts_by_type = {}
    for issue in issues:
        counts_by_severity[issue["severity"]] = counts_by_severity.get(issue["severity"], 0) + 1
        counts_by_type[issue["issue_type"]] = counts_by_type.get(issue["issue_type"], 0) + 1
    return {"issues": issues, "total": len(issues), "counts_by_severity": counts_by_severity, "counts_by_type": counts_by_type}

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
    if not account.is_system_account:
        raise AccountingError("Mapped accounting settings must reference system accounts")
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
    """Return persisted accounting settings without mutating data during GET requests."""
    settings = {s.setting_key: s.setting_value for s in AccountingSetting.query.filter(AccountingSetting.setting_key.in_(list(SETTING_VALIDATION))).limit(len(SETTING_VALIDATION)).all()}
    accounts_by_id = {}
    accounts_by_code = {}
    if settings:
        ids = [int(v) for v in settings.values() if str(v).isdigit()]
        codes = [str(v) for v in settings.values() if not str(v).isdigit()]
        if ids:
            accounts_by_id = {a.id: a for a in AccountingAccount.query.filter(AccountingAccount.id.in_(ids)).all()}
        if codes:
            accounts_by_code = {a.account_code: a for a in AccountingAccount.query.filter(AccountingAccount.account_code.in_(codes)).all()}
    payload = {"configured": True, "missing_settings": []}
    for key in SETTING_VALIDATION:
        value = settings.get(key)
        account = accounts_by_id.get(int(value)) if value and str(value).isdigit() else accounts_by_code.get(str(value)) if value else None
        if account:
            payload[key] = serialize_account(account)
            payload[f"{key}_id"] = account.id
        else:
            payload[key] = None
            payload["configured"] = False
            payload["missing_settings"].append(key)
    for key in DISBURSEMENT_SETTING_DEFAULTS:
        setting = AccountingSetting.query.filter_by(setting_key=key).first()
        if setting is not None:
            payload[key] = setting.setting_value
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
    if "account_subtype" in data and data["account_subtype"] != account_subtype(acct) and account_is_mapped(acct):
        log_audit("PROTECTED_ACCOUNT_UPDATE_ATTEMPTED", "AccountingAccount", acct.id, user_id, {"field": "account_subtype"}); raise AccountingError("Mapped account subtype cannot be changed incompatibly")
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
    running=money(opening); all_tx=[]; td=tc=Decimal("0.00")
    rows=tx_query.order_by(AccountingJournalEntry.journal_date, AccountingJournalEntry.journal_no, AccountingJournalLine.line_no).all()
    current_app.logger.info("general_ledger query", extra={"query_params": query_params or {}, "resolved_account_id": account.id, "journal_lines_found": len(rows)})
    for l in rows:
        running=money(running+signed(l)); td+=money(l.debit); tc+=money(l.credit); e=l.journal_entry; ctx=_line_context(l)
        all_tx.append({"journal_entry_id": e.id, "journal_date": e.journal_date.isoformat(), "journal_no": e.journal_no, "description": e.description, "reference_type": e.reference_type, "reference_id": e.reference_id, "source_module": e.source_module, "debit": f"{money(l.debit):.2f}", "credit": f"{money(l.credit):.2f}", "running_balance": f"{running:.2f}", **ctx})
    expected = money(opening + td - tc) if account.normal_balance == "DEBIT" else money(opening + tc - td)
    if expected != money(running):
        raise AccountingError("General ledger invariant failed: closing balance does not match account-normal movement")
    page = _int_filter((query_params or {}).get("page"), "page") if query_params else None
    per_page = _int_filter((query_params or {}).get("per_page"), "per_page") if query_params else None
    tx = all_tx
    pagination = None
    if page and per_page:
        start = max(page - 1, 0) * per_page
        tx = all_tx[start:start + per_page]
        pagination = {"page": page, "per_page": per_page, "total": len(all_tx)}
    result = {"account": {"id": account.id, "account_code": account.account_code, "account_name": account.account_name, "account_type": account.account_type, "account_subtype": account_subtype(account), "normal_balance": account.normal_balance}, "opening_balance": f"{money(opening):.2f}", "transactions": tx, "running_balance": f"{running:.2f}", "closing_balance": f"{running:.2f}", "total_debit": f"{money(td):.2f}", "total_credit": f"{money(tc):.2f}"}
    if pagination:
        result["pagination"] = pagination
    return result

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
def _normal_account_type(value):
    typ = (value or "").strip().upper()
    return {"REVENUE": "INCOME", "CAPITAL": "EQUITY"}.get(typ, typ)


def _normal_account_subtype(account):
    return (account_subtype(account) or "OTHER").strip().upper()


def _normal_balance_for(account):
    normal = (account.normal_balance or "").strip().upper()
    if normal in NORMAL_BALANCES:
        return normal
    return "DEBIT" if _normal_account_type(account.account_type) in ("ASSET", "EXPENSE") else "CREDIT"


def _posted_filter():
    # Include posted reversal pairs so reversed activity nets to zero in shared balances.
    return func.upper(AccountingJournalEntry.status).in_(["POSTED", "REVERSED"])


def _journal_accounting_date():
    return func.coalesce(AccountingJournalEntry.accounting_date, AccountingJournalEntry.journal_date)


def _account_depth(account):
    depth=0; p=account.parent
    while p: depth += 1; p = p.parent
    return depth


def _signed_balance(account, debit, credit):
    debit=money(debit); credit=money(credit)
    return money(debit-credit) if _normal_balance_for(account) == "DEBIT" else money(credit-debit)


def _side_amounts(account, signed):
    signed=money(signed); normal=_normal_balance_for(account)
    side = normal if signed >= 0 else ("CREDIT" if normal == "DEBIT" else "DEBIT")
    return (abs(signed), Decimal("0.00"), side) if side == "DEBIT" else (Decimal("0.00"), abs(signed), side)


def _sum_by_account(date_to=None, date_from=None, account_types=None):
    q = db.session.query(AccountingJournalLine.account_id, func.coalesce(func.sum(AccountingJournalLine.debit), 0), func.coalesce(func.sum(AccountingJournalLine.credit), 0)).join(AccountingJournalEntry).filter(_posted_filter())
    acct_date = _journal_accounting_date()
    if date_from: q = q.filter(acct_date >= date_from)
    if date_to: q = q.filter(acct_date <= date_to)
    if account_types:
        normalized = [_normal_account_type(t) for t in account_types]
        q = q.join(AccountingAccount).filter(func.upper(func.trim(AccountingAccount.account_type)).in_(normalized))
    return {r[0]: (money(r[1]), money(r[2])) for r in q.group_by(AccountingJournalLine.account_id).all()}


def get_account_balances(date_from=None, date_to=None, as_of_date=None, include_zero=False):
    """Authoritative account balance engine for all financial reports."""
    if as_of_date is None:
        as_of_date = date_to or _today()
    opening_to = (date_from - timedelta(days=1)) if date_from else None
    opening_map = _sum_by_account(date_to=opening_to) if date_from else {}
    period_map = _sum_by_account(date_from=date_from, date_to=date_to or as_of_date)
    accounts = AccountingAccount.query.filter_by(is_active=True).order_by(AccountingAccount.account_code).all()
    balances=[]
    zero = Decimal("0.00")
    for a in accounts:
        od, oc = opening_map.get(a.id, (zero, zero))
        pd, pc = period_map.get(a.id, (zero, zero))
        closing_signed = _signed_balance(a, od + pd, oc + pc)
        opening_signed = _signed_balance(a, od, oc)
        opening_debit, opening_credit, _ = _side_amounts(a, opening_signed)
        closing_debit, closing_credit, side = _side_amounts(a, closing_signed)
        if not include_zero and all(money(v) == zero for v in (opening_debit, opening_credit, pd, pc, closing_debit, closing_credit)):
            continue
        balances.append({
            "account_id": a.id,
            "account": a,
            "account_code": a.account_code,
            "account_name": a.account_name,
            "account_type": _normal_account_type(a.account_type),
            "account_subtype": _normal_account_subtype(a),
            "normal_balance": _normal_balance_for(a),
            "opening_debit": money(opening_debit),
            "opening_credit": money(opening_credit),
            "period_debit": money(pd),
            "period_credit": money(pc),
            "closing_debit": money(closing_debit),
            "closing_credit": money(closing_credit),
            "signed_closing_balance": money(closing_signed),
            "balance_side": side,
            "financial_statement_group": a.financial_statement_group,
            "display_order": a.financial_statement_order,
            "is_parent": bool(a.children),
            "depth": _account_depth(a),
            "parent_id": a.parent_id,
        })
    return balances


def _warnings(extra_issues=None):
    issues = reconciliation_issues()
    missing_types = {"MISSING_DISBURSEMENT_JOURNAL", "MISSING_PAYMENT_JOURNAL", "MISSING_DEPOSIT_JOURNAL", "MISSING_ACCRUAL_JOURNAL"}
    missing = [i for i in issues if i.get("issue_type") in missing_types]
    warnings=[]
    if missing:
        warning={"code":"UNPOSTED_OPERATIONAL_TRANSACTIONS","count":len(missing),"message":"Some operational transactions have no accounting journals.","reconciliation_filter":{"issue_types":sorted(missing_types)}}
        warnings.append(warning)
        warnings.append({**warning, "code":"INCOMPLETE_ACCOUNTING_HISTORY"})
    for issue in extra_issues or []:
        code = issue.get("type") or issue.get("issue_type")
        if code in ("UNCLASSIFIED_ACCOUNT", "UNBALANCED_TRIAL_BALANCE", "UNBALANCED_FINANCIAL_POSITION", "JOURNAL_TOTAL_MISMATCH"):
            warnings.append({"code": code, "message": code.replace("_", " ").title()})
    seen=set(); out=[]
    for w in warnings:
        if w["code"] not in seen:
            seen.add(w["code"]); out.append(w)
    return out


def _validate_posted_journals():
    issues=[]
    for e in AccountingJournalEntry.query.filter(_posted_filter()).all():
        td=sum((money(l.debit) for l in e.lines), Decimal("0.00")); tc=sum((money(l.credit) for l in e.lines), Decimal("0.00"))
        if td != tc or money(e.total_debit) != td or money(e.total_credit) != tc:
            issues.append({"type":"POSTED_JOURNAL_TOTAL_MISMATCH","journal_id":e.id})
    return issues


def trial_balance_report(as_of_date=None, date_from=None, include_zero_balances=False, account_type=None, account_id=None, comparative_as_of_date=None):
    seed_default_report_classifications(); as_of_date = as_of_date or _today()
    balances = get_account_balances(date_from=date_from, as_of_date=as_of_date, include_zero=include_zero_balances)
    if account_type:
        balances=[b for b in balances if b["account_type"] == _normal_account_type(account_type)]
    if account_id:
        balances=[b for b in balances if b["account_id"] == int(account_id)]
    comp_map = {b["account_id"]: b["signed_closing_balance"] for b in get_account_balances(as_of_date=comparative_as_of_date, include_zero=True)} if comparative_as_of_date else {}
    rows=[]; totals={k:Decimal('0.00') for k in ['opening_debit','opening_credit','period_debit','period_credit','closing_debit','closing_credit']}
    for b in balances:
        for k in totals: totals[k] += money(b[k])
        rows.append({k: (_fmt(v) if isinstance(v, Decimal) else v) for k,v in b.items() if k != "account"} | {"net_balance":_fmt(b["signed_closing_balance"]),"comparative_net_balance": _fmt(comp_map.get(b["account_id"], Decimal("0.00"))) if comparative_as_of_date else None,"parent_totals_included":False,"drilldown":{"account_id":b["account_id"],"date_from":date_from.isoformat() if date_from else None,"date_to":as_of_date.isoformat()}})
    diff=money(totals['closing_debit']-totals['closing_credit']); issues=_validate_posted_journals()
    if diff != 0: issues.append({"type":"UNBALANCED_TRIAL_BALANCE","difference":_fmt(diff)})
    return {"report":"TRIAL_BALANCE","as_of_date":as_of_date.isoformat(),"date_from":date_from.isoformat() if date_from else None,"accounts":rows,"totals":{f"total_{k}":_fmt(v) for k,v in totals.items()} | {"difference":_fmt(diff),"is_balanced":abs(diff)<=Decimal("0.01")},"validation":{"is_valid":not issues,"difference":_fmt(diff),"issues":issues},"warnings":_warnings(issues),"has_activity":bool(rows),"is_empty":not bool(rows)}


def _variance(amount, comp):
    var=money(amount-comp); pct=None if comp==0 else money((var/abs(comp))*Decimal('100'))
    return _fmt(var), (_fmt(pct) if pct is not None else None)


def _income_group(account_type, group, subtype):
    if group in FINANCIAL_STATEMENT_GROUPS:
        return group
    return None


def income_statement_report(date_from, date_to, comparative_date_from=None, comparative_date_to=None, include_zero_balances=False):
    seed_default_report_classifications(); balances=get_account_balances(date_from=date_from, date_to=date_to, include_zero=True)
    comp={b["account_id"]: b for b in get_account_balances(date_from=comparative_date_from, date_to=comparative_date_to, include_zero=True)} if comparative_date_from and comparative_date_to else {}
    sections_i={}; sections_e={}; total_i=total_e=tax=Decimal('0.00'); issues=[]; income_rows=[]; expense_rows=[]
    for b in balances:
        typ=b["account_type"]
        if typ not in ("INCOME", "EXPENSE"): continue
        amount=money(b["period_credit"]-b["period_debit"]) if typ=='INCOME' else money(b["period_debit"]-b["period_credit"])
        cb=comp.get(b["account_id"]); camount=Decimal("0.00")
        if cb: camount=money(cb["period_credit"]-cb["period_debit"]) if typ=='INCOME' else money(cb["period_debit"]-cb["period_credit"])
        if not include_zero_balances and amount == Decimal("0.00") and camount == Decimal("0.00"): continue
        group=_income_group(typ, b.get("financial_statement_group"), b["account_subtype"])
        var,pct=_variance(amount,camount); row={"account_id":b["account_id"],"account_code":b["account_code"],"account_name":b["account_name"],"amount":_fmt(amount),"comparative_amount":_fmt(camount) if comp else None,"variance":var if comp else None,"variance_percent":pct if comp else None,"drilldown":{"account_id":b["account_id"],"date_from":date_from.isoformat(),"date_to":date_to.isoformat()}}
        if typ=='INCOME':
            name=INCOME_SECTION_NAMES.get(group, INCOME_SECTION_NAMES[None]); sections_i.setdefault(name, []).append(row); total_i += amount; income_rows.append(row)
        else:
            name=EXPENSE_SECTION_NAMES.get(group, EXPENSE_SECTION_NAMES[None]); sections_e.setdefault(name, []).append(row); total_e += amount; expense_rows.append(row)
            if group == 'TAX_EXPENSE': tax += amount
    def pack(sections): return [{"section_name":k,"name":k,"accounts":v,"total":_fmt(sum(Decimal(x['amount']) for x in v))} for k,v in sections.items()]
    income_sections=pack(sections_i); expense_sections=pack(sections_e); net=money(total_i-total_e); diff=money(total_i-total_e-net)
    has_activity = total_i != 0 or total_e != 0 or len(income_rows) > 0 or len(expense_rows) > 0
    return {"report":"INCOME_STATEMENT","date_from":date_from.isoformat(),"date_to":date_to.isoformat(),"income_sections":income_sections,"expense_sections":expense_sections,"income":{"sections":income_sections,"total_income":_fmt(total_i)},"expenses":{"sections":expense_sections,"total_expenses":_fmt(total_e)},"total_income":_fmt(total_i),"total_expenses":_fmt(total_e),"profit_before_tax":_fmt(total_i-total_e+tax),"tax_expense":_fmt(tax),"net_profit":_fmt(net),"net_profit_loss":_fmt(net),"validation":{"is_valid":diff==0 and not issues,"difference":_fmt(diff),"issues":issues},"warnings":_warnings(issues),"has_activity":has_activity,"is_empty":not has_activity}


CURRENT_ASSET_SUBTYPES={"CASH","BANK","COLLECTION_CLEARING","COLLECTION_CLEARING_CONTROL","LOAN_RECEIVABLE","INTEREST_RECEIVABLE","PENALTY_RECEIVABLE","ACCOUNTS_RECEIVABLE","OTHER_CURRENT_ASSET","SUSPENSE"}
NON_CURRENT_ASSET_SUBTYPES={"FIXED_ASSET","INTANGIBLE_ASSET","LONG_TERM_RECEIVABLE"}
NON_CURRENT_LIABILITY_SUBTYPES={"LONG_TERM_BORROWING"}


def _balance_row(b, amount=None):
    amt = b["signed_closing_balance"] if amount is None else money(amount)
    return {"account_id":b["account_id"],"account_code":b["account_code"],"account_name":b["account_name"],"account_type":b["account_type"],"account_subtype":b["account_subtype"],"financial_statement_group":b.get("financial_statement_group"),"amount":_fmt(amt),"debit":_fmt(b["closing_debit"]),"credit":_fmt(b["closing_credit"]),"has_activity": any(money(b[k]) != Decimal("0.00") for k in ("period_debit","period_credit","closing_debit","closing_credit")),"drilldown":{"account_id":b["account_id"],"date_from":None}}


def statement_of_financial_position_report(as_of_date=None, comparative_as_of_date=None, include_zero_balances=False, reclassify_credit_bank_balances=True):
    seed_default_report_classifications(); as_of_date=as_of_date or _today(); balances=get_account_balances(as_of_date=as_of_date, include_zero=include_zero_balances)
    ca=[]; nca=[]; cl=[]; ncl=[]; eq=[]
    for b in balances:
        typ=b["account_type"]; subtype=b["account_subtype"]; amount=b["signed_closing_balance"]
        if typ not in ("ASSET","LIABILITY","EQUITY"): continue
        if not include_zero_balances and amount == Decimal("0.00"): continue
        row=_balance_row(b, abs(amount) if typ in ("LIABILITY","EQUITY") else amount); row["drilldown"]["date_to"] = as_of_date.isoformat()
        group=b.get("financial_statement_group")
        if typ=='ASSET' and reclassify_credit_bank_balances and subtype=='BANK' and b["closing_credit"] > Decimal("0.00") and b["closing_debit"] == Decimal("0.00"):
            adj=dict(row); adj['account_name']=f"Bank Overdraft – {b['account_name']}"; adj['amount']=_fmt(b["closing_credit"]); adj['original_account_type']='ASSET'; adj['presentation_section']='CURRENT_LIABILITY'; adj['presentation_adjustment']='BANK_OVERDRAFT_RECLASSIFICATION'; adj['financial_statement_group']='CURRENT_LIABILITY'; cl.append(adj)
        elif typ=='ASSET' and (group=='NON_CURRENT_ASSET' or subtype in NON_CURRENT_ASSET_SUBTYPES): nca.append(row)
        elif typ=='ASSET': ca.append(row)
        elif typ=='LIABILITY' and (group=='NON_CURRENT_LIABILITY' or subtype in NON_CURRENT_LIABILITY_SUBTYPES): ncl.append(row)
        elif typ=='LIABILITY': cl.append(row)
        elif typ=='EQUITY': eq.append(row)
    earnings_balances=get_account_balances(as_of_date=as_of_date, include_zero=True)
    current_earnings=sum((money(b["period_credit"]-b["period_debit"]) for b in earnings_balances if b["account_type"]=='INCOME'), Decimal("0.00")) - sum((money(b["period_debit"]-b["period_credit"]) for b in earnings_balances if b["account_type"]=='EXPENSE'), Decimal("0.00"))
    if include_zero_balances or current_earnings != Decimal("0.00"):
        eq.append({"account_code":"CURRENT_EARNINGS","account_name":"Current Period Earnings","amount":_fmt(current_earnings)})
    ta=sum(Decimal(r['amount']) for r in ca+nca); tl=sum(Decimal(r['amount']) for r in cl+ncl); te=sum(Decimal(r['amount']) for r in eq); diff=money(ta-(tl+te)); issues=[]
    if abs(diff) > Decimal("0.01"): issues.append({"type":"UNBALANCED_FINANCIAL_POSITION","difference":_fmt(diff)})
    has_activity=bool(ca or nca or cl or ncl or eq)
    return {"report":"STATEMENT_OF_FINANCIAL_POSITION","as_of_date":as_of_date.isoformat(),"assets":{"current_assets":{"accounts":ca,"total":_fmt(sum(Decimal(r['amount']) for r in ca))},"non_current_assets":{"accounts":nca,"total":_fmt(sum(Decimal(r['amount']) for r in nca))},"total_assets":_fmt(ta)},"liabilities":{"current_liabilities":{"accounts":cl,"total":_fmt(sum(Decimal(r['amount']) for r in cl))},"non_current_liabilities":{"accounts":ncl,"total":_fmt(sum(Decimal(r['amount']) for r in ncl))},"total_liabilities":_fmt(tl)},"equity":{"accounts":eq,"retained_earnings":"0.00","current_period_profit_loss":_fmt(current_earnings),"total_equity":_fmt(te),"policy":"BANK accounts with credit balances are presented as current liability bank overdrafts when reclassify_credit_bank_balances is true; current period earnings are cumulative posted income less expenses through the as-of date until year-end closing is implemented."},"total_liabilities_and_equity":_fmt(tl+te),"difference":_fmt(diff),"balanced":abs(diff)<=Decimal("0.01"),"is_balanced":abs(diff)<=Decimal("0.01"),"financial_position_balanced":abs(diff)<=Decimal("0.01"),"validation":{"is_valid":not issues,"difference":_fmt(diff),"issues":issues},"warnings":_warnings(issues),"has_activity":has_activity,"is_empty":not has_activity}

def reports_summary(date_from=None, date_to=None, as_of_date=None):
    as_of_date=as_of_date or date_to or _today(); date_from=date_from or date(as_of_date.year,1,1); date_to=date_to or as_of_date
    try:
        isr=income_statement_report(date_from,date_to); sfp=statement_of_financial_position_report(as_of_date); tb=trial_balance_report(as_of_date)
    except Exception as exc:
        current_app.logger.exception("accounting reports summary failed")
        return {"success":False,"message":"Financial reports summary is unavailable.","error_code":"ACCOUNTING_REPORT_SUMMARY_UNAVAILABLE"}
    unclassified=AccountingAccount.query.filter(AccountingAccount.account_type.in_(['INCOME','EXPENSE','ASSET','LIABILITY','EQUITY']), AccountingAccount.financial_statement_group.is_(None)).count()
    warnings=_warnings((tb.get('validation') or {}).get('issues', []) + (sfp.get('validation') or {}).get('issues', []) + (isr.get('validation') or {}).get('issues', []))
    return {"date_from":date_from.isoformat(),"date_to":date_to.isoformat(),"as_of_date":as_of_date.isoformat(),"total_assets":sfp['assets']['total_assets'],"total_liabilities":sfp['liabilities']['total_liabilities'],"total_equity":sfp['equity']['total_equity'],"total_income":isr['income']['total_income'],"total_expenses":isr['expenses']['total_expenses'],"net_profit_loss":isr['net_profit_loss'],"net_profit":isr['net_profit'],"trial_balance_difference":tb['totals']['difference'],"trial_balance_balanced":Decimal(tb['totals']['difference']) == Decimal('0.00'),"financial_position_difference":sfp['difference'],"statement_of_financial_position_difference":sfp['difference'],"financial_position_balanced":Decimal(sfp['difference']) == Decimal('0.00'),"unclassified_account_count":unclassified,"incomplete_accounting_history":any(w['code']=='INCOMPLETE_ACCOUNTING_HISTORY' for w in warnings),"warnings":warnings,"has_activity":bool(tb.get('accounts')),"is_empty":not bool(tb.get('accounts')), "validation":{"is_valid":tb['validation']['is_valid'] and sfp['validation']['is_valid'] and isr['validation']['is_valid'],"issues":tb['validation']['issues']+sfp['validation']['issues']+isr['validation']['issues']}}

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

# Loan accrual accounting extensions
LOAN_ACCRUAL_METHOD = "ACCRUAL_BY_INSTALLMENT"
CASH_BASIS_METHOD = "CASH_BASIS"

def get_setting(key, default=None):
    setting = AccountingSetting.query.filter_by(setting_key=key).first()
    return setting.setting_value if setting else default

def is_accounting_period_open(accounting_date):
    period = AccountingPeriod.query.filter(
        AccountingPeriod.start_date <= accounting_date,
        AccountingPeriod.end_date >= accounting_date,
    ).first()
    return not (period and period.is_locked), period

def require_open_accounting_period(accounting_date):
    allow = str(get_setting("allow_posting_to_locked_period", "false")).lower() == "true"
    open_, period = is_accounting_period_open(accounting_date)
    if not open_ and not allow:
        raise AccountingError({"error":"Accounting period is locked", "accounting_date": accounting_date.isoformat(), "period": period.period})
    return True

def _loan_active_for_accrual(loan):
    return str(loan.status).upper() in {"ACTIVE", "APPROVED", "STAFF_APPROVED"}

def accrue_due_loan_interest(as_of_date, loan_id=None, historical=False, requested_by=None):
    if isinstance(as_of_date, str):
        as_of_date = date.fromisoformat(as_of_date)
    summary = {"processed_installments": 0, "total_interest_accrued": Decimal("0.00"), "journal_ids": [], "skipped": [], "errors": []}
    query = LoanLedger.query.join(Loan).filter(
        LoanLedger.due_date <= as_of_date,
        LoanLedger.interest_amount > 0,
        LoanLedger.interest_accrued.is_(False),
        Loan.interest_accounting_method == LOAN_ACCRUAL_METHOD,
    )
    if loan_id:
        query = query.filter(LoanLedger.loan_id == loan_id)
    for ledger in query.order_by(LoanLedger.due_date, LoanLedger.id).all():
        loan = ledger.loan
        if not _loan_active_for_accrual(loan):
            summary["skipped"].append({"ledger_id": ledger.id, "reason": "loan_status"}); continue
        existing = AccountingJournalEntry.query.filter_by(source_type="LOAN_INTEREST_ACCRUAL", source_id=ledger.id).first()
        if existing:
            ledger.interest_accrued = True; ledger.interest_accrual_journal_id = existing.id
            summary["skipped"].append({"ledger_id": ledger.id, "reason": "existing_journal"}); continue
        try:
            require_open_accounting_period(ledger.due_date)
            amount = money(ledger.interest_amount)
            entry = create_draft_journal(ledger.due_date, f"Interest accrual – Loan {loan.loan_number} – Installment {ledger.installment_no}", [
                {"account_id": resolve_system_account("INTEREST_RECEIVABLE").id, "debit": amount, "customer_id": loan.customer_id, "loan_id": loan.id},
                {"account_id": resolve_system_account("LOAN_INTEREST_INCOME").id, "credit": amount, "customer_id": loan.customer_id, "loan_id": loan.id},
            ], "LOAN_INTEREST_ACCRUAL", ledger.id, "LOANS", requested_by, f"LOAN_INTEREST_ACCRUAL:{ledger.id}")
            entry.loan_id = loan.id; entry.customer_id = loan.customer_id; entry.accounting_date = ledger.due_date
            post_journal(entry, requested_by)
            ledger.interest_accrued = True; ledger.interest_accrued_at = datetime.utcnow(); ledger.interest_accrual_journal_id = entry.id
            loan.accrual_processed_through = max(loan.accrual_processed_through or ledger.due_date, ledger.due_date)
            summary["processed_installments"] += 1; summary["total_interest_accrued"] = money(summary["total_interest_accrued"] + amount); summary["journal_ids"].append(entry.id)
        except Exception as exc:
            summary["errors"].append({"ledger_id": ledger.id, "error": str(exc)})
            if not historical:
                raise
    return summary

def allocate_payment(loan, amount, paid_date):
    if str(getattr(loan, "interest_accounting_method", LOAN_ACCRUAL_METHOD)) == LOAN_ACCRUAL_METHOD:
        accrue_due_loan_interest(paid_date, loan.id, historical=True)
    remaining = money(amount); principal=interest=penalty=unapplied=Decimal("0.00")
    allocations=[]
    for e in sorted(loan.ledger_entries, key=lambda x: (x.due_date, x.installment_no)):
        if remaining <= 0: break
        delay_due = money(Decimal(e.delay_interest_accrued or 0) - Decimal(e.delay_interest_paid or 0))
        pay = min(remaining, delay_due); e.delay_interest_paid = money(Decimal(e.delay_interest_paid or 0)+pay); penalty += pay; remaining -= pay
        if pay: allocations.append((e, "DELAY_INTEREST", pay))
        interest_base = Decimal(e.interest_amount) if str(loan.interest_accounting_method) == CASH_BASIS_METHOD or e.interest_accrued else Decimal("0.00")
        interest_due = money(interest_base - Decimal(e.interest_paid or 0))
        pay = min(remaining, interest_due); e.interest_paid = money(Decimal(e.interest_paid or 0)+pay); interest += pay; remaining -= pay
        if pay: allocations.append((e, "INTEREST", pay))
        principal_due = money(Decimal(e.principal_amount) - Decimal(e.principal_paid or 0))
        pay = min(remaining, principal_due); e.principal_paid = money(Decimal(e.principal_paid or 0)+pay); principal += pay; remaining -= pay
        if pay: allocations.append((e, "PRINCIPAL", pay))
        e.paid_amount = money(Decimal(e.principal_paid or 0)+Decimal(e.interest_paid or 0)+Decimal(e.delay_interest_paid or 0))
        payable = money(Decimal(e.principal_amount)+Decimal(e.interest_amount)+Decimal(e.delay_interest_accrued or 0))
        e.status = "PAID" if e.paid_amount >= payable else ("PARTIAL" if e.paid_amount > 0 else e.status)
        if e.paid_amount > 0: e.paid_date = paid_date
    if remaining > 0: unapplied = remaining
    loan._pending_allocations = allocations + ([(None, "UNAPPLIED", unapplied)] if unapplied else [])
    return money(principal), money(interest), money(penalty), money(unapplied)

def post_loan_payment(payment, user_id=None, receipt_account=None):
    existing = AccountingJournalEntry.query.filter_by(idempotency_key=f"LOAN_PAYMENT:{payment.id}").first()
    if existing: return existing
    total = money(payment.amount_collected); principal=money(payment.principal_paid); interest=money(payment.interest_paid); penalty=money(payment.penalty_paid); other=money(payment.other_fee_paid)
    if money(principal+interest+penalty+other) != total: raise AccountingError("Payment allocation does not match amount collected")
    receipt_account = validate_funding_account(receipt_account or resolve_system_account(_method_key(payment.payment_method) or "DEFAULT_CASH_COLLECTION_ACCOUNT"), payment.payment_method)
    loan = payment.loan
    lines=[{"account_id": receipt_account.id, "debit": total, "customer_id": loan.customer_id, "loan_id": loan.id, "payment_id": payment.id}]
    method = getattr(loan, "interest_accounting_method", LOAN_ACCRUAL_METHOD)
    credit_keys = [("DELAY_INTEREST_RECEIVABLE" if method == LOAN_ACCRUAL_METHOD else "DELAY_INTEREST_INCOME", penalty), ("INTEREST_RECEIVABLE" if method == LOAN_ACCRUAL_METHOD else "LOAN_INTEREST_INCOME", interest), ("LOAN_PRINCIPAL_RECEIVABLE", principal), ("UNAPPLIED_CUSTOMER_FUNDS", other)]
    for key, amt in credit_keys:
        if amt > 0: lines.append({"account_id": resolve_system_account(key).id, "credit": amt, "customer_id": loan.customer_id, "loan_id": loan.id, "payment_id": payment.id})
    entry = create_draft_journal(payment.collection_date, "Loan payment", lines, "LOAN_PAYMENT", payment.id, "PAYMENTS", user_id, f"LOAN_PAYMENT:{payment.id}")
    entry.loan_id = loan.id; entry.customer_id = loan.customer_id; entry.accounting_date = payment.collection_date
    posted = post_journal(entry, user_id); payment.journal_id = posted.id
    for ledger, typ, amt in getattr(loan, "_pending_allocations", []):
        db.session.add(PaymentAllocation(payment_id=payment.id, loan_id=loan.id, ledger_id=ledger.id if ledger else None, allocation_type=typ, amount=money(amt)))
    return posted

def reverse_payment(payment, reversal_date, reason, user_id=None):
    if not reason: raise AccountingError("Reversal reason is required")
    if isinstance(reversal_date, str): reversal_date = date.fromisoformat(reversal_date)
    require_open_accounting_period(reversal_date)
    entry = AccountingJournalEntry.query.get(payment.journal_id) if payment.journal_id else AccountingJournalEntry.query.filter_by(reference_type="LOAN_PAYMENT", reference_id=str(payment.id)).first()
    if not entry: raise AccountingError("Payment journal not found")
    rev = reverse_journal(entry, reversal_date, reason, user_id)
    for alloc in list(payment.allocations):
        ledger = alloc.ledger
        if ledger:
            if alloc.allocation_type == "PRINCIPAL": ledger.principal_paid = money(Decimal(ledger.principal_paid or 0)-Decimal(alloc.amount))
            elif alloc.allocation_type == "INTEREST": ledger.interest_paid = money(Decimal(ledger.interest_paid or 0)-Decimal(alloc.amount))
            elif alloc.allocation_type == "DELAY_INTEREST": ledger.delay_interest_paid = money(Decimal(ledger.delay_interest_paid or 0)-Decimal(alloc.amount))
            ledger.paid_amount = money(Decimal(ledger.principal_paid or 0)+Decimal(ledger.interest_paid or 0)+Decimal(ledger.delay_interest_paid or 0)); ledger.status = "PARTIAL" if ledger.paid_amount > 0 else "PENDING"
    payment.reversed_at = datetime.utcnow(); payment.reversal_journal_id = rev.id; payment.reversal_reason = reason
    log_audit("PAYMENT_REVERSE", "Payment", payment.id, user_id, reason)
    return rev


CALCULATION_METHODS = {"FIXED_AMOUNT", "PERCENTAGE_OF_PRINCIPAL", "MANUAL_AMOUNT"}
ACCOUNTING_TREATMENTS = {"INCOME", "PAYABLE", "EXPENSE_RECOVERY", "TAX", "OTHER"}
TAX_METHODS = {"NO_TAX", "TAX_EXCLUSIVE", "TAX_INCLUSIVE"}

def _destination_account_for_charge(charge_type):
    acct = charge_type.income_account if charge_type.accounting_treatment == "INCOME" else (charge_type.payable_account or charge_type.tax_payable_account if charge_type.accounting_treatment in {"PAYABLE", "TAX"} else (charge_type.expense_account or charge_type.income_account or charge_type.payable_account))
    if not acct or not acct.is_active: raise AccountingError(f"Destination account is required for charge type {charge_type.code}")
    return acct

def _charge_to_dict(line):
    acct=line["destination_account"]; tax=line.get("tax_account"); ct=line["charge_type"]
    return {"charge_type_id":ct.id,"code":ct.code,"name":ct.name,"description":line["description"],"gross_amount":float(line["gross_amount"]),"tax_amount":float(line["tax_amount"]),"net_charge_amount":float(line["net_charge_amount"]),"calculation_method":line["calculation_method"],"rate":float(line["rate"]) if line.get("rate") is not None else None,"accounting_treatment":line["accounting_treatment"],"destination_account":{"id":acct.id,"code":acct.account_code,"name":acct.account_name},"tax_account":{"id":tax.id,"code":tax.account_code,"name":tax.account_name} if tax else None}

def calculate_disbursement_charges(principal_amount, selected_charges, allow_exceeding_principal=None, allow_zero_net=None):
    seed_disbursement_settings(); principal=money(principal_amount)
    if principal <= 0: raise AccountingError("gross principal must be greater than zero")
    allow_exceeding_principal = setting_bool("allow_deductions_exceeding_principal", False) if allow_exceeding_principal is None else allow_exceeding_principal
    allow_zero_net = setting_bool("allow_zero_net_disbursement", False) if allow_zero_net is None else allow_zero_net
    lines=[]; subtotal=tax_total=total=Decimal("0.00")
    for raw in selected_charges or []:
        ct=DisbursementChargeType.query.get(raw.get("charge_type_id"))
        if not ct or not ct.active: raise AccountingError("unsupported charge type")
        if ct.included_in_principal: raise AccountingError("capitalized disbursement charges are not enabled for this workflow")
        if not ct.deducted_from_disbursement: continue
        rate=raw.get("rate", ct.default_rate)
        if ct.calculation_method=="PERCENTAGE_OF_PRINCIPAL":
            if rate is None or Decimal(str(rate)) < 0: raise AccountingError("invalid rate")
            gross=money(principal*Decimal(str(rate))/Decimal("100"))
        elif ct.calculation_method=="FIXED_AMOUNT": gross=money(raw.get("amount", ct.default_amount))
        elif ct.calculation_method=="MANUAL_AMOUNT": gross=money(raw.get("amount"))
        else: raise AccountingError("unsupported charge type")
        if gross < 0: raise AccountingError("negative charge amounts are not allowed")
        dest=_destination_account_for_charge(ct); tax_method=raw.get("tax_method") or ct.tax_method or get_setting("default_charge_tax_method", "NO_TAX")
        if tax_method not in TAX_METHODS: raise AccountingError("unsupported tax method")
        tax_rate=Decimal(str(raw.get("tax_rate", ct.tax_rate or 0)))
        if tax_rate < 0: raise AccountingError("invalid tax rate")
        tax_acct=ct.tax_payable_account or (resolve_system_account("default_tax_payable_account") if tax_method != "NO_TAX" and tax_rate > 0 else None)
        if tax_method=="TAX_INCLUSIVE" and tax_rate>0: net=money(gross/(Decimal("1")+tax_rate/Decimal("100"))); tax=money(gross-net)
        elif tax_method=="TAX_EXCLUSIVE" and tax_rate>0: net=gross; tax=money(gross*tax_rate/Decimal("100")); gross=money(net+tax)
        else: net=gross; tax=Decimal("0.00")
        subtotal+=net; tax_total+=tax; total+=gross
        lines.append({"charge_type":ct,"description":raw.get("description") or ct.name,"gross_amount":gross,"tax_amount":tax,"net_charge_amount":net,"calculation_method":ct.calculation_method,"rate":Decimal(str(rate)) if rate is not None else None,"accounting_treatment":ct.accounting_treatment,"destination_account":dest,"tax_account":tax_acct})
    net_disb=money(principal-total)
    if total > principal and not allow_exceeding_principal: raise AccountingError("total deductions cannot exceed gross principal")
    if net_disb <= 0 and not allow_zero_net: raise AccountingError("net disbursed amount must be greater than zero")
    return {"charge_lines":lines,"charges":[_charge_to_dict(l) for l in lines],"subtotal_before_tax":money(subtotal),"tax_amount":money(tax_total),"total_deductions":money(total),"total_disbursement_deductions":money(total),"net_disbursement":net_disb,"net_disbursed_amount":net_disb}

def preview_loan_disbursement(loan, charges=None, funding_account=None, disbursement_date=None):
    principal=money(getattr(loan,"gross_principal_amount",None) or loan.principal_amount); funding_account=validate_funding_account(funding_account or resolve_system_account("DEFAULT_DISBURSEMENT_ACCOUNT")); result=calculate_disbursement_charges(principal, charges or [])
    credits=[{"account":funding_account.account_name,"account_id":funding_account.id,"amount":result["net_disbursed_amount"]}]; grouped={}
    for line in result["charge_lines"]:
        grouped[line["destination_account"].id]=grouped.get(line["destination_account"].id,{"account":line["destination_account"].account_name,"account_id":line["destination_account"].id,"amount":Decimal("0.00")}); grouped[line["destination_account"].id]["amount"]+=line["net_charge_amount"]
        if line["tax_amount"]>0: grouped[line["tax_account"].id]=grouped.get(line["tax_account"].id,{"account":line["tax_account"].account_name,"account_id":line["tax_account"].id,"amount":Decimal("0.00")}); grouped[line["tax_account"].id]["amount"]+=line["tax_amount"]
    credits += list(grouped.values())
    for c in credits: c["amount"]=money(c["amount"])
    return {"gross_principal_amount":principal,"charges":result["charges"],"total_disbursement_deductions":result["total_deductions"],"net_disbursed_amount":result["net_disbursed_amount"],"journal_preview":{"debits":[{"account":resolve_system_account("LOAN_PRINCIPAL_RECEIVABLE").account_name,"amount":principal}],"credits":credits,"total_debit":principal,"total_credit":money(sum(c["amount"] for c in credits))},"charge_lines":result["charge_lines"]}

def reverse_loan_disbursement(loan, reversal_date, reason, user_id=None):
    if not reason: raise AccountingError("Reversal reason is required")
    if Payment.query.filter_by(loan_id=loan.id).filter(Payment.reversed_at.is_(None)).count():
        raise AccountingError("Cannot reverse disbursement while unreversed payments exist")
    require_open_accounting_period(reversal_date)
    reversed_ids=[]
    for ledger in sorted(loan.ledger_entries, key=lambda l: l.installment_no, reverse=True):
        if ledger.interest_accrual_journal_id:
            entry = AccountingJournalEntry.query.get(ledger.interest_accrual_journal_id)
            if entry and entry.status == "POSTED":
                rev=reverse_journal(entry, reversal_date, reason, user_id); reversed_ids.append(rev.id)
            ledger.interest_accrued=False; ledger.interest_accrual_journal_id=None
    entry = AccountingJournalEntry.query.get(loan.disbursement_journal_id) if loan.disbursement_journal_id else AccountingJournalEntry.query.filter_by(reference_type="LOAN_DISBURSEMENT", reference_id=str(loan.id)).first()
    if entry and entry.status == "POSTED":
        rev=reverse_journal(entry, reversal_date, reason, user_id); loan.reversal_journal_id=rev.id; reversed_ids.append(rev.id)
        for deduction in LoanDisbursementDeduction.query.filter_by(loan_id=loan.id, status="POSTED").all():
            deduction.status="REVERSED"; deduction.reversed_at=datetime.utcnow(); deduction.reversal_journal_id=rev.id
    loan.reversed_at=datetime.utcnow(); loan.status="APPROVED"
    log_audit("LOAN_DISBURSEMENT_REVERSE", "Loan", loan.id, user_id, reason)
    return {"reversal_journal_ids": reversed_ids}

def post_loan_disbursement(loan, user_id=None, funding_key="DEFAULT_DISBURSEMENT_ACCOUNT", funding_account=None, disbursement_date=None, charges=None, loan_application_id=None, transaction_method=None, reference=None, remarks=None):
    existing = AccountingJournalEntry.query.filter_by(idempotency_key=f"LOAN_DISBURSEMENT:{loan.id}").first()
    if existing:
        loan.disbursement_journal_id = existing.id
        return existing
    gross = money(getattr(loan, "gross_principal_amount", None) or loan.principal_amount)
    loan.gross_principal_amount = gross; loan.principal_amount = gross
    funding_account = validate_funding_account(funding_account or resolve_system_account(funding_key))
    journal_date = disbursement_date or loan.start_date or date.today(); require_open_accounting_period(journal_date)
    preview = preview_loan_disbursement(loan, charges or [], funding_account, journal_date)
    net = money(preview["net_disbursed_amount"]); deductions = money(preview["total_disbursement_deductions"])
    lines = [{"account_id": resolve_system_account("LOAN_PRINCIPAL_RECEIVABLE").id, "debit": gross, "customer_id": loan.customer_id, "loan_id": loan.id}]
    if net > 0: lines.append({"account_id": funding_account.id, "credit": net, "customer_id": loan.customer_id, "loan_id": loan.id})
    grouped={}
    for c in preview["charge_lines"]:
        grouped[c["destination_account"].id]=grouped.get(c["destination_account"].id, Decimal("0.00"))+c["net_charge_amount"]
        if c["tax_amount"]>0: grouped[c["tax_account"].id]=grouped.get(c["tax_account"].id, Decimal("0.00"))+c["tax_amount"]
    for account_id, amount in grouped.items():
        if money(amount)>0: lines.append({"account_id":account_id,"credit":money(amount),"customer_id":loan.customer_id,"loan_id":loan.id})
    entry = create_draft_journal(journal_date, "Loan disbursement", lines, "LOAN_DISBURSEMENT", loan.id, "LOANS", user_id, f"LOAN_DISBURSEMENT:{loan.id}")
    entry.loan_id = loan.id; entry.customer_id = loan.customer_id; entry.accounting_date = journal_date
    posted = post_journal(entry, user_id); loan.disbursement_journal_id = posted.id
    for c in preview["charge_lines"]:
        db.session.add(LoanDisbursementDeduction(loan_id=loan.id, loan_application_id=loan_application_id, charge_type_id=c["charge_type"].id, description=c["description"], gross_amount=c["gross_amount"], tax_amount=c["tax_amount"], net_charge_amount=c["net_charge_amount"], calculation_method=c["calculation_method"], rate=c["rate"], accounting_treatment=c["accounting_treatment"], destination_account_id=c["destination_account"].id, tax_account_id=c["tax_account"].id if c.get("tax_account") else None, status="POSTED", journal_entry_id=posted.id, created_by=user_id))
    loan.total_disbursement_deductions=deductions; loan.net_disbursed_amount=net; loan.disbursement_charge_count=len(preview["charge_lines"]); loan.disbursement_deductions_posted=bool(preview["charge_lines"])
    log_audit("DISBURSEMENT_CHARGES_POSTED", "Loan", loan.id, user_id, {"gross_principal_amount": str(gross), "total_deductions": str(deductions), "net_disbursed_amount": str(net), "transaction_method": transaction_method, "reference": reference, "remarks": remarks})
    mode = getattr(loan, "historical_accrual_mode", None) or get_setting("backdated_loan_accounting_mode", "AUTO")
    if journal_date < date.today() and mode == "AUTO":
        as_of = min(date.today(), loan.maturity_date or loan.end_date or date.today())
        accrue_due_loan_interest(as_of, loan.id, historical=True, requested_by=user_id)
    return posted

# Collector collection accounting
COLLECTION_METHODS = {"CASH_COLLECTOR", "BANK_TRANSFER", "CASH_OFFICE", "CHEQUE", "MOBILE_TRANSFER", "OTHER"}
COLLECTOR_DEPOSIT_STATUSES = {"NOT_APPLICABLE", "UNDEPOSITED", "PARTIALLY_DEPOSITED", "DEPOSITED", "REVERSED"}


def _number(prefix, model, field, for_date):
    stem = f"{prefix}-{for_date:%Y%m%d}-"
    try:
        db.session.execute(text("select pg_advisory_xact_lock(hashtext(:p))"), {"p": stem})
    except Exception:
        pass
    last = db.session.query(func.max(getattr(model, field))).filter(getattr(model, field).like(stem + "%")).scalar()
    return f"{stem}{(int(last.rsplit('-', 1)[1]) + 1 if last else 1):04d}"


def generate_receipt_number(payment_date):
    return _number("GROW-RCPT", Payment, "receipt_number", payment_date)


def generate_deposit_number(deposit_date):
    from .models import CollectionDepositBatch
    return _number("GROW-DEP", CollectionDepositBatch, "deposit_number", deposit_date)



def generate_next_collection_account_code():
    control = resolve_system_account("collector_clearing_control_account")
    try:
        db.session.execute(text("select pg_advisory_xact_lock(hashtext(:p))"), {"p": "collection_account_code"})
    except Exception:
        pass
    q = AccountingAccount.query.filter(
        AccountingAccount.parent_account_id == control.id,
        AccountingAccount.account_subtype == "COLLECTION_CLEARING",
    )
    max_code = int(control.account_code)
    for acct in q.all():
        if str(acct.account_code).isdigit():
            max_code = max(max_code, int(acct.account_code))
    while AccountingAccount.query.filter_by(account_code=str(max_code + 1)).first():
        max_code += 1
    return str(max_code + 1)

def create_collector_collection_account(collector):
    control = resolve_system_account("collector_clearing_control_account")
    if (
        not control.is_active
        or control.allow_manual_posting
        or account_subtype(control) != "COLLECTION_CLEARING_CONTROL"
        or control.is_collection_account
        or control.collector_id is not None
    ):
        raise AccountingError("Collector clearing control account is not configured correctly")
    existing = AccountingAccount.query.filter_by(collector_id=collector.id, is_collection_account=True, is_active=True).first()
    if existing:
        if collector.default_collection_account_id == existing.id:
            raise AccountingError("Only one active default collection account is allowed per collector")
        collector.default_collection_account_id = existing.id
        log_audit("COLLECTOR_ACCOUNT_LINK", "User", collector.id, None, {"account_id": existing.id})
        return existing
    acct = AccountingAccount(
        account_code=generate_next_collection_account_code(),
        account_name=f"Collection Account – {collector.name}",
        account_type="ASSET", normal_balance="DEBIT",
        parent_id=control.id, parent_account_id=control.id,
        account_subtype="COLLECTION_CLEARING", account_role="COLLECTOR_CASH",
        allow_manual_posting=True, is_active=True,
        is_collection_account=True, collector_id=collector.id,
        cash_flow_category="NONE",
    )
    db.session.add(acct)
    db.session.flush()
    collector.default_collection_account_id = acct.id
    log_audit("COLLECTOR_ACCOUNT_CREATE", "User", collector.id, None, {"account_id": acct.id, "account_code": acct.account_code})
    return acct

def validate_collection_account(account, method, collector_id=None):
    if not account or not account.is_active:
        raise AccountingError("Collection account is inactive or missing")
    subtype = account_subtype(account)
    if method == "BANK_TRANSFER" and subtype != "BANK":
        raise AccountingError("BANK_TRANSFER requires a bank collection account")
    if method == "CASH_OFFICE" and subtype != "CASH":
        raise AccountingError("CASH_OFFICE requires a Cash on Hand account")
    if method == "CASH_COLLECTOR":
        if not collector_id:
            raise AccountingError("Collector setup incomplete: collector_id is required for CASH_COLLECTOR")
        collector = User.query.get(int(collector_id))
        if not collector or not collector.is_active or not collector.is_collector or collector.collector_status != "ACTIVE" or not collector.can_collect_cash:
            raise AccountingError("Collector setup incomplete: The selected collector is not active or cannot collect cash.")
        if not collector.default_collection_account_id:
            raise AccountingError("The selected collector has no active posting collection account.")
        if account.id == resolve_system_account("collector_clearing_control_account").id:
            raise AccountingError("Collector setup incomplete: control account 1050 cannot receive collector payments.")
        if not account.allow_manual_posting or account.account_type != "ASSET" or account.normal_balance != "DEBIT" or subtype != "COLLECTION_CLEARING" or not account.is_collection_account:
            raise AccountingError("Collector setup incomplete: The selected collector has no active posting collection account.")
        if account.collector_id != int(collector_id) or collector.default_collection_account_id != account.id:
            raise AccountingError("Collection account is not linked to the collector")
    return account


def validate_funding_account(account, *_args):
    if not account:
        raise AccountingError("Funding account not found")
    if getattr(account, "is_collection_account", False) or account_subtype(account) == "COLLECTION_CLEARING":
        raise AccountingError("Collection clearing accounts cannot fund loans")
    if not account.is_active:
        raise AccountingError("Funding account is inactive")
    if account.account_type != "ASSET":
        raise AccountingError("Funding account must be an ASSET account")
    if account_subtype(account) not in ("CASH", "BANK"):
        raise AccountingError("Funding account must be configured as CASH or BANK")
    if not account.allow_manual_posting:
        raise AccountingError("Funding account does not allow posting")
    return account


def post_loan_payment(payment, user_id=None, receipt_account=None):
    existing = AccountingJournalEntry.query.filter_by(idempotency_key=f"LOAN_PAYMENT:{payment.id}").first()
    if existing: return existing
    total = money(payment.amount_collected); principal=money(payment.principal_paid); interest=money(payment.interest_paid); penalty=money(payment.penalty_paid); other=money(payment.other_fee_paid)
    if money(principal+interest+penalty+other) != total: raise AccountingError("Payment allocation does not match amount collected")
    method = (payment.collection_method or payment.payment_method or "CASH_OFFICE").upper()
    if method == "CASH": method = "CASH_OFFICE"
    if method == "BANK": method = "BANK_TRANSFER"
    pay_date = payment.accounting_date or payment.payment_date or payment.collection_date
    require_open_accounting_period(pay_date)
    loan = payment.loan
    if pay_date < loan.start_date:
        raise AccountingError("Payment date cannot be before loan disbursement date")
    receipt_account = validate_collection_account(receipt_account or payment.collection_account or resolve_system_account("DEFAULT_CASH_COLLECTION_ACCOUNT" if method == "CASH_OFFICE" else "DEFAULT_BANK_COLLECTION_ACCOUNT"), method, payment.collector_id)
    lines=[{"account_id": receipt_account.id, "debit": total, "customer_id": loan.customer_id, "loan_id": loan.id, "payment_id": payment.id}]
    acct_method = getattr(loan, "interest_accounting_method", LOAN_ACCRUAL_METHOD)
    for key, amt in [("DELAY_INTEREST_RECEIVABLE" if acct_method == LOAN_ACCRUAL_METHOD else "DELAY_INTEREST_INCOME", penalty), ("INTEREST_RECEIVABLE" if acct_method == LOAN_ACCRUAL_METHOD else "LOAN_INTEREST_INCOME", interest), ("LOAN_PRINCIPAL_RECEIVABLE", principal), ("UNAPPLIED_CUSTOMER_FUNDS", other)]:
        if amt > 0: lines.append({"account_id": resolve_system_account(key).id, "credit": amt, "customer_id": loan.customer_id, "loan_id": loan.id, "payment_id": payment.id})
    customer_name = loan.customer.full_name if loan.customer else "Customer"
    entry = create_draft_journal(pay_date, f"Loan payment – {loan.loan_number} – {customer_name}", lines, "LOAN_PAYMENT", payment.id, "PAYMENTS", user_id, f"LOAN_PAYMENT:{payment.id}")
    entry.loan_id = loan.id; entry.customer_id = loan.customer_id; entry.accounting_date = pay_date
    posted = post_journal(entry, user_id)
    log_audit("PAYMENT_JOURNAL_CREATE", "Payment", payment.id, user_id, {"journal_id": posted.id, "amount": f"{total:.2f}"})
    payment.journal_id = posted.id; payment.payment_date = pay_date; payment.accounting_date = pay_date
    payment.collection_method = method; payment.collection_account_id = receipt_account.id
    payment.receipt_number = payment.receipt_number or generate_receipt_number(pay_date)
    payment.bank_reference = payment.bank_reference or payment.transaction_reference
    payment.deposit_status = "UNDEPOSITED" if method == "CASH_COLLECTOR" else "NOT_APPLICABLE"
    for ledger, typ, amt in getattr(loan, "_pending_allocations", []):
        db.session.add(PaymentAllocation(payment_id=payment.id, loan_id=loan.id, ledger_id=ledger.id if ledger else None, allocation_type=typ, amount=money(amt)))
    return posted



def repair_unposted_payment(payment_id, requested_by=None):
    payment = Payment.query.get(payment_id)
    if not payment:
        raise AccountingError("Payment not found")
    if payment.journal_id:
        journal = AccountingJournalEntry.query.get(payment.journal_id)
        return {"payment_id": payment.id, "journal_created": False, "journal_id": payment.journal_id, "journal_number": journal.journal_no if journal else None, "repaired": False, "message": "already repaired"}
    if payment.reversed_at:
        raise AccountingError("Cannot repair a reversed payment")
    if money(payment.deposited_amount) > 0 or CollectionDepositAllocation.query.filter_by(payment_id=payment.id).first():
        raise AccountingError("Cannot repair a payment that has deposit allocations")
    loan = payment.loan
    total = money(payment.amount_collected)
    allocated = money(Decimal(payment.principal_paid or 0) + Decimal(payment.interest_paid or 0) + Decimal(payment.penalty_paid or 0) + Decimal(payment.other_fee_paid or 0))
    if allocated != total:
        raise AccountingError("Existing payment allocation is inconsistent; reverse and re-enter the payment")
    if (payment.collection_method or payment.payment_method or "").upper() == "CASH_COLLECTOR":
        validate_collection_account(payment.collection_account, "CASH_COLLECTOR", payment.collector_id)
    if not payment.allocations:
        raise AccountingError("Existing payment has no saved allocation rows; reverse and re-enter the payment")
    if str(getattr(loan, "interest_accounting_method", LOAN_ACCRUAL_METHOD)) == LOAN_ACCRUAL_METHOD:
        accrue_due_loan_interest(payment.accounting_date or payment.payment_date or payment.collection_date, loan.id, historical=True, requested_by=requested_by)
    receipt_account = payment.collection_account or AccountingAccount.query.get(payment.receipt_account_id)
    journal = post_loan_payment(payment, requested_by, receipt_account=receipt_account)
    if not payment.journal_id:
        raise AccountingError("Payment journal was not created")
    log_audit("PAYMENT_ACCOUNTING_REPAIR", "Payment", payment.id, requested_by, {"journal_id": journal.id, "amount": f"{total:.2f}"})
    return {"payment_id": payment.id, "journal_created": True, "journal_id": journal.id, "journal_number": journal.journal_no, "repaired": True}

def _payment_deposit_status(payment):
    if payment.reversed_at: return "REVERSED"
    if (payment.collection_method or "").upper() != "CASH_COLLECTOR": return "NOT_APPLICABLE"
    dep = money(payment.deposited_amount)
    amt = money(payment.amount_collected)
    return "UNDEPOSITED" if dep == 0 else "DEPOSITED" if dep >= amt else "PARTIALLY_DEPOSITED"


def collector_cash_position(collector_id, as_of_date=None):
    q = Payment.query.filter(Payment.collector_id == collector_id, Payment.reversed_at.is_(None), Payment.deposit_status != "NOT_APPLICABLE")
    if as_of_date: q = q.filter(Payment.collection_date <= as_of_date)
    payments = q.all(); collections=sum((money(p.amount_collected) for p in payments), Decimal("0.00")); deposits=sum((money(p.deposited_amount) for p in payments), Decimal("0.00"))
    return {"collections": money(collections), "deposits": money(deposits), "adjustments": Decimal("0.00"), "closing_balance": money(collections-deposits), "undeposited_payments": [p for p in payments if money(p.undeposited_amount) > 0]}


def _validation_payload(exc):
    if isinstance(exc, ValidationError):
        return exc.payload
    if exc.args and isinstance(exc.args[0], dict):
        return exc.args[0]
    return {"error": str(exc), "message": str(exc)}


def _account_dict(account):
    return {
        "id": account.id,
        "code": account.account_code,
        "name": account.account_name,
    }


def _collector_dict(collector):
    return {"id": collector.id, "name": collector.name}


def _parse_deposit_date(value):
    try:
        parsed = date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise ValidationError("deposit_date must be a valid ISO date")
    if parsed > date.today():
        raise ValidationError("Unsupported future deposit dates are not allowed")
    try:
        require_open_accounting_period(parsed)
    except AccountingError as exc:
        payload = _validation_payload(exc)
        raise ValidationError(payload.get("error") or payload.get("message") or "Accounting period is not open", **{k: v for k, v in payload.items() if k not in {"error", "message"}})
    return parsed


def validate_collection_deposit_payload(data):
    data = data or {}
    required_fields = ["collector_id", "collector_account_id", "bank_account_id", "deposit_date", "allocations"]
    missing = [field for field in required_fields if data.get(field) in (None, "", [])]
    if missing:
        message = "Collector collection account is required." if "collector_account_id" in missing else "Required collection deposit fields are missing."
        raise ValidationError("Collection deposit validation failed", missing_fields=missing, message=message)

    collector_id = parse_positive_int(data.get("collector_id"), "collector_id")
    collector_account_id = parse_positive_int(data.get("collector_account_id"), "collector_account_id")
    bank_account_id = parse_positive_int(data.get("bank_account_id"), "bank_account_id")
    deposit_date = _parse_deposit_date(data.get("deposit_date"))

    collector = db.session.get(User, collector_id)
    if not collector:
        raise ValidationError("Collector not found")
    if not collector.is_active or collector.collector_status != "ACTIVE":
        raise ValidationError("Collector must be active")
    if not collector.can_collect_cash:
        raise ValidationError("Collector is not permitted to collect cash")
    if not collector.default_collection_account_id:
        raise ValidationError("Collector default collection account is required")

    collector_account = db.session.get(AccountingAccount, collector_account_id)
    if not collector_account:
        raise ValidationError("Collector collection account not found")
    control = resolve_system_account("collector_clearing_control_account")
    if collector_account.id == control.id or collector_account.account_code == "1050":
        raise ValidationError("Control account 1050 cannot be used as a collector deposit account")
    if not collector_account.is_active:
        raise ValidationError("Collector collection account must be active")
    if collector_account.account_type != "ASSET":
        raise ValidationError("Collector collection account must be an ASSET account")
    if account_subtype(collector_account) != "COLLECTION_CLEARING":
        raise ValidationError("Collector collection account subtype must be COLLECTION_CLEARING")
    if not collector_account.is_collection_account:
        raise ValidationError("Collector collection account must be flagged as a collection account")
    if not collector_account.allow_manual_posting:
        raise ValidationError("Collector collection account must allow posting")
    if collector_account.collector_id != collector.id:
        raise ValidationError("Collection account is not linked to the collector")
    if collector.default_collection_account_id != collector_account.id and not data.get("allow_non_default_collector_account"):
        raise ValidationError("Submitted collector account must match the collector's default collection account")

    bank = db.session.get(AccountingAccount, bank_account_id)
    if not bank:
        raise ValidationError("Bank account not found")
    if not bank.is_active:
        raise ValidationError("Bank account must be active")
    if bank.account_type != "ASSET":
        raise ValidationError("Bank account must be an ASSET account")
    if account_subtype(bank) != "BANK":
        raise ValidationError("Bank account subtype must be BANK")
    if not bank.allow_manual_posting:
        raise ValidationError("Bank account must allow posting")

    allocations = data.get("allocations") or []
    if not isinstance(allocations, list):
        raise ValidationError("allocations must be a list")
    seen = set(); total = Decimal("0.00"); rows = []
    earliest_payment_date = None
    for idx, raw in enumerate(allocations):
        if not isinstance(raw, dict):
            raise ValidationError("Each allocation must be an object", allocation_index=idx)
        payment_id = parse_positive_int(raw.get("payment_id"), "payment_id")
        if payment_id in seen:
            raise ValidationError("Duplicate payment allocation submitted", payment_id=payment_id)
        seen.add(payment_id)
        amt = money(raw.get("amount"))
        if amt <= 0:
            raise ValidationError("Allocation amount must be greater than zero", payment_id=payment_id)
        payment = db.session.get(Payment, payment_id)
        if not payment:
            raise ValidationError("Payment not found", payment_id=payment_id)
        payment_date = payment.accounting_date or payment.payment_date or payment.collection_date
        earliest_payment_date = payment_date if earliest_payment_date is None else min(earliest_payment_date, payment_date)
        if payment.collector_id != collector.id:
            raise ValidationError("Payment does not belong to selected collector", payment_id=payment_id)
        if payment.collection_account_id != collector_account.id:
            raise ValidationError("Payment collection account does not match selected collector account", payment_id=payment_id)
        if (payment.collection_method or "").upper() != "CASH_COLLECTOR":
            raise ValidationError("Payment collection method must be CASH_COLLECTOR", payment_id=payment_id)
        if payment.status != "POSTED" or not payment.journal_id:
            raise ValidationError("Payment must be posted with a journal before deposit", payment_id=payment_id)
        if payment.reversed_at or payment.deposit_status in ("NOT_APPLICABLE", "REVERSED"):
            raise ValidationError("Payment is not depositable", payment_id=payment_id)
        if payment_date and payment_date > deposit_date:
            raise ValidationError("Deposit date cannot be earlier than any selected payment date", payment_id=payment_id)
        if amt > money(payment.undeposited_amount):
            raise ValidationError("Allocation exceeds undeposited amount", payment_id=payment_id, undeposited_amount=f"{money(payment.undeposited_amount):.2f}")
        total += amt; rows.append({"payment": payment, "amount": amt})
    if total <= 0:
        raise ValidationError("Deposit allocations are required")
    balance = collector_cash_position(collector.id, deposit_date)["closing_balance"]
    if total > money(balance):
        raise ValidationError("Historical collector balance is insufficient as of deposit date", collector_balance=f"{money(balance):.2f}")
    return {"collector": collector, "collector_account": collector_account, "bank_account": bank, "deposit_date": deposit_date, "total_amount": money(total), "rows": rows, "remaining_collector_balance": money(balance - total), "raw": data}


def build_collection_deposit_preview(validated_data):
    total = money(validated_data["total_amount"])
    bank = validated_data["bank_account"]
    collector_account = validated_data["collector_account"]
    return {
        "collector": _collector_dict(validated_data["collector"]),
        "collector_account": _account_dict(collector_account),
        "bank_account": _account_dict(bank),
        "deposit_date": validated_data["deposit_date"].isoformat(),
        "total_amount": float(total) if total % 1 else int(total),
        "remaining_collector_balance": float(money(validated_data["remaining_collector_balance"])),
        "journal_preview": {
            "debits": [{"account_id": bank.id, "account_code": bank.account_code, "account_name": bank.account_name, "amount": float(total) if total % 1 else int(total)}],
            "credits": [{"account_id": collector_account.id, "account_code": collector_account.account_code, "account_name": collector_account.account_name, "amount": float(total) if total % 1 else int(total)}],
        },
        "validation_errors": [],
    }


def preview_collection_deposit(data):
    return build_collection_deposit_preview(validate_collection_deposit_payload(data))


def create_collection_deposit(data, user_id=None):
    from .models import CollectionDepositBatch, CollectionDepositAllocation
    validated = validate_collection_deposit_payload(data)
    total = validated["total_amount"]
    batch = CollectionDepositBatch(
        deposit_number=generate_deposit_number(validated["deposit_date"]), collector_id=validated["collector"].id,
        collector_account_id=validated["collector_account"].id, bank_account_id=validated["bank_account"].id,
        deposit_date=validated["deposit_date"], accounting_date=validated["deposit_date"], total_amount=total,
        bank_reference=data.get("bank_reference"), deposit_slip_reference=data.get("deposit_slip_reference"), remarks=data.get("remarks"),
        created_by=user_id, status="POSTED")
    db.session.add(batch); db.session.flush()
    for row in validated["rows"]:
        p = row["payment"]; amt = row["amount"]
        db.session.add(CollectionDepositAllocation(deposit_batch_id=batch.id, payment_id=p.id, allocated_amount=amt))
        p.deposited_amount = money(Decimal(p.deposited_amount or 0) + amt)
        p.deposit_status = _payment_deposit_status(p)
    entry = create_draft_journal(
        batch.accounting_date, f"Collector deposit {batch.deposit_number}",
        [{"account_id": batch.bank_account_id, "debit": total}, {"account_id": batch.collector_account_id, "credit": total}],
        "COLLECTION_DEPOSIT", batch.id, "COLLECTIONS", user_id, f"COLLECTION_DEPOSIT:{batch.id}")
    posted = post_journal(entry, user_id)
    batch.journal_entry_id = posted.id
    log_audit("COLLECTION_DEPOSIT_POST", "CollectionDepositBatch", batch.id, user_id)
    batch._collector_account_balance_after = validated["remaining_collector_balance"]
    return batch


def reverse_collection_deposit(batch, reversal_date, reason, user_id=None):
    if not reason: raise AccountingError("Reversal reason is required")
    if batch.status != "POSTED": raise AccountingError("Only POSTED collection deposits can be reversed")
    require_open_accounting_period(reversal_date)
    entry=AccountingJournalEntry.query.get(batch.journal_entry_id)
    rev=reverse_journal(entry, reversal_date, reason, user_id)
    for alloc in batch.allocations:
        p=alloc.payment; p.deposited_amount=money(Decimal(p.deposited_amount or 0)-Decimal(alloc.allocated_amount)); p.deposit_status=_payment_deposit_status(p)
    batch.status="REVERSED"; batch.reversed_at=datetime.utcnow(); batch.reversal_reason=reason; batch.reversal_journal_id=rev.id; log_audit("COLLECTION_DEPOSIT_REVERSE", "CollectionDepositBatch", batch.id, user_id, reason)
    return rev

_old_reverse_payment_impl = reverse_payment

def reverse_payment(payment, reversal_date, reason, user_id=None):
    if money(getattr(payment, "deposited_amount", 0)) > 0:
        raise AccountingError("Cannot reverse a payment already included in a posted deposit batch; reverse the deposit first")
    rev = _old_reverse_payment_impl(payment, reversal_date, reason, user_id)
    payment.status = "REVERSED"; payment.deposit_status = "REVERSED"; payment.reversed_by = user_id
    return rev
