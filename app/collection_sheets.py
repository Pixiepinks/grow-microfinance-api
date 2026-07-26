"""Atomic collection-sheet workflow and accounting orchestration."""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, or_, text

from .extensions import db
from .models import (AccountingAccount, AccountingJournalEntry, AccountingJournalLine, CollectionSheet,
                     CollectionSheetExpense, CollectionSheetItem, Customer, Loan,
                     Payment, User, CollectionDepositAllocation)
from .accounting import (AccountingError, account_subtype, allocate_payment,
                         create_draft_journal, is_active_account, is_posting_account,
                         log_audit, money, post_journal, post_loan_payment,
                         reverse_journal, reverse_payment, validate_collection_account)

EDITABLE = {"DRAFT"}
POSTED = {"POSTED", "RECONCILED", "REVERSED"}
ELIGIBLE_LOANS = {"ACTIVE", "DISBURSED", "OVERDUE"}


class SheetError(ValueError):
    def __init__(self, message, status=422, **details):
        super().__init__(message); self.status = status; self.details = details


def decimal_amount(value, field="amount", allow_zero=False):
    try:
        result = money(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError):
        raise SheetError(f"{field} must be a valid decimal")
    if result < 0 or (result == 0 and not allow_zero):
        raise SheetError(f"{field} must be greater than zero")
    return result


def sheet_number(for_date):
    stem = f"CS-{for_date:%Y%m%d}-"
    try:
        db.session.execute(text("select pg_advisory_xact_lock(hashtext(:key))"), {"key": stem})
    except Exception:  # SQLite test environments; uniqueness is still DB-enforced.
        pass
    last = db.session.query(func.max(CollectionSheet.sheet_number)).filter(CollectionSheet.sheet_number.like(stem + "%")).scalar()
    return f"{stem}{int(last.rsplit('-', 1)[1]) + 1 if last else 1:04d}"


def recalculate(sheet):
    sheet.gross_collection = money(sum((Decimal(i.amount) for i in sheet.items), Decimal("0")))
    sheet.total_expenses = money(sum((Decimal(e.amount) for e in sheet.expenses), Decimal("0")))
    sheet.expected_deposit = money(sheet.gross_collection - sheet.total_expenses)
    sheet.difference = money(Decimal(sheet.actual_deposit or 0) - sheet.expected_deposit)
    return sheet


def ensure_draft(sheet):
    if sheet.status not in EDITABLE:
        raise SheetError("Only DRAFT collection sheets can be edited", 409, sheet_status=sheet.status)


def valid_expense_account(account):
    return bool(account and is_active_account(account) and is_posting_account(account)
                and account.account_type == "EXPENSE"
                and account_subtype(account) not in {"COLLECTION_CLEARING", "COLLECTION_CLEARING_CONTROL",
                                                     "LOAN_RECEIVABLE", "CUSTOMER_ADVANCE", "BANK", "CASH"})


def valid_bank(account):
    return bool(account and is_active_account(account) and is_posting_account(account)
                and account.account_type == "ASSET" and account_subtype(account) in {"BANK", "CASH"}
                and not account.is_collection_account)


def validate(sheet, submitted=False):
    recalculate(sheet)
    errors = []
    collector = sheet.collector
    if not collector or not collector.is_active or not collector.is_collector or not collector.can_collect_cash:
        errors.append("Collector must be active and permitted to collect cash")
    elif not collector.default_collection_account_id:
        errors.append("Collector clearing account is required")
    if not sheet.collection_date: errors.append("Collection date is required")
    if not sheet.items: errors.append("At least one collection item is required")
    for item in sheet.items:
        if Decimal(item.amount or 0) <= 0: errors.append(f"Item {item.id}: amount must be greater than zero")
        if not item.loan or not item.customer or item.loan.customer_id != item.customer_id: errors.append(f"Item {item.id}: invalid loan/customer")
        elif (item.loan.status or "").upper() not in ELIGIBLE_LOANS: errors.append(f"Item {item.id}: loan is not eligible for collection")
    for expense in sheet.expenses:
        if not valid_expense_account(expense.expense_account): errors.append(f"Expense {expense.id}: account is not a posting expense account")
    if submitted:
        if sheet.actual_deposit is None: errors.append("Actual deposit is required")
        if sheet.expected_deposit > 0 and not valid_bank(sheet.bank_account): errors.append("A valid posting bank/cash account is required")
        if not sheet.deposit_date: errors.append("Deposit date is required")
    if errors: raise SheetError("Collection sheet validation failed", errors=errors)
    if collector:
        validate_collection_account(db.session.get(AccountingAccount, collector.default_collection_account_id), "CASH_COLLECTOR", collector.id)
    return sheet


def totals(sheet):
    recalculate(sheet)
    def value(v): return {"raw": f"{money(v):.2f}", "formatted": f"{money(v):,.2f}"}
    return {k: value(getattr(sheet, k)) for k in ("gross_collection", "total_expenses", "expected_deposit", "actual_deposit", "difference")}


def serialize(sheet, detail=False):
    recalculate(sheet)
    data = {"id": sheet.id, "sheet_number": sheet.sheet_number, "status": sheet.status,
            "collection_date": sheet.collection_date.isoformat(), "collector": {"id": sheet.collector_id, "name": sheet.collector.name if sheet.collector else None},
            "gross_collection": f"{sheet.gross_collection:.2f}", "total_expenses": f"{sheet.total_expenses:.2f}",
            "expected_deposit": f"{sheet.expected_deposit:.2f}", "actual_deposit": f"{money(sheet.actual_deposit):.2f}" if sheet.actual_deposit is not None else None,
            "difference": f"{sheet.difference:.2f}", "totals": totals(sheet), "notes": sheet.notes,
            "bank_account_id": sheet.bank_account_id, "deposit_date": sheet.deposit_date.isoformat() if sheet.deposit_date else None,
            "deposit_reference": sheet.deposit_reference, "created_by": sheet.created_by.name if sheet.created_by else None,
            "approved_by": sheet.approved_by.name if sheet.approved_by else None}
    if detail:
        data.update({"items": [{"id": i.id, "loan_id": i.loan_id, "loan_number": i.loan.loan_number if i.loan else None,
                                "customer_id": i.customer_id, "customer_name": i.customer.full_name if i.customer else None,
                                "amount": f"{money(i.amount):.2f}", "payment_id": i.payment_id, "posting_status": i.posting_status,
                                "posting_error": i.posting_error} for i in sheet.items],
                     "expenses": [{"id": e.id, "expense_account_id": e.expense_account_id,
                                    "account_code": e.expense_account.account_code if e.expense_account else None,
                                    "account_name": e.expense_account.account_name if e.expense_account else None,
                                    "amount": f"{money(e.amount):.2f}", "description": e.description,
                                    "reference": e.reference, "journal_entry_id": e.journal_entry_id} for e in sheet.expenses],
                     "posting_references": {"bank_journal_id": sheet.bank_journal_id,
                                            "payment_ids": [i.payment_id for i in sheet.items if i.payment_id],
                                            "expense_journal_ids": [e.journal_entry_id for e in sheet.expenses if e.journal_entry_id]},
                     "audit": {k: getattr(sheet, k).isoformat() if getattr(sheet, k) else None for k in
                               ("created_at", "submitted_at", "approved_at", "posted_at", "reconciled_at", "reversed_at")}})
    return data


def preview(sheet):
    warnings = []
    try: validate(sheet, submitted=True)
    except SheetError as exc: warnings = exc.details.get("errors", [str(exc)])
    clearing = sheet.collector.default_collection_account_id if sheet.collector else None
    journals = [{"type": "EXPENSE", "debit_account_id": e.expense_account_id, "credit_account_id": clearing, "amount": f"{money(e.amount):.2f}"} for e in sheet.expenses]
    if Decimal(sheet.actual_deposit or 0) > 0:
        journals.append({"type": "BANK_DEPOSIT", "debit_account_id": sheet.bank_account_id, "credit_account_id": clearing, "amount": f"{money(sheet.actual_deposit):.2f}"})
    return {**serialize(sheet, True), "customer_payments": [{"item_id": i.id, "loan_id": i.loan_id, "amount": f"{money(i.amount):.2f}", "payment_date": sheet.collection_date.isoformat()} for i in sheet.items],
            "journals": journals, "validation_warnings": warnings,
            "proposed_final_status": "RECONCILED" if money(sheet.difference) == 0 else "POSTED"}


def approve_and_post(sheet_id, user_id):
    sheet = CollectionSheet.query.filter_by(id=sheet_id).with_for_update().first()
    if not sheet: raise SheetError("Collection sheet not found", 404)
    if sheet.status in {"POSTED", "RECONCILED"}: return sheet.posting_result or serialize(sheet, True)
    if sheet.status != "SUBMITTED": raise SheetError("Only SUBMITTED sheets may be approved", 409)
    validate(sheet, submitted=True)
    clearing = db.session.get(AccountingAccount, sheet.collector.default_collection_account_id)
    try:
        for item in sheet.items:
            if item.payment_id: continue
            principal, interest, penalty, excess = allocate_payment(item.loan, item.amount, sheet.collection_date)
            payment = Payment(loan_id=item.loan_id, collection_date=sheet.collection_date, payment_date=sheet.collection_date,
                              accounting_date=sheet.collection_date, amount_collected=item.amount, principal_paid=principal,
                              interest_paid=interest, penalty_paid=penalty, other_fee_paid=excess, collected_by_id=sheet.collector_id,
                              collector_id=sheet.collector_id, payment_method="CASH_COLLECTOR", collection_method="CASH_COLLECTOR",
                              collection_account_id=clearing.id, receipt_account_id=clearing.id,
                              transaction_reference=sheet.sheet_number, remarks=f"Collection sheet {sheet.sheet_number}",
                              idempotency_key=f"COLLECTION_SHEET:{sheet.id}:ITEM:{item.id}",
                              collection_sheet_id=sheet.id, collection_clearance_status="UNDEPOSITED")
            db.session.add(payment); db.session.flush(); post_loan_payment(payment, user_id, clearing)
            item.payment_id = payment.id; item.posting_status = "POSTED"; item.posting_error = None
        for expense in sheet.expenses:
            entry = create_draft_journal(sheet.collection_date, expense.description,
                [{"account_id": expense.expense_account_id, "debit": expense.amount, "collector_id": sheet.collector_id},
                 {"account_id": clearing.id, "credit": expense.amount, "collector_id": sheet.collector_id}],
                "COLLECTION_SHEET_EXPENSE", expense.id, "COLLECTION_SHEETS", user_id,
                f"COLLECTION_SHEET:{sheet.id}:EXPENSE:{expense.id}", expense.reference)
            expense.journal_entry_id = post_journal(entry, user_id).id
        if money(sheet.actual_deposit) > 0:
            entry = create_draft_journal(sheet.deposit_date, f"Collection sheet deposit {sheet.sheet_number}",
                [{"account_id": sheet.bank_account_id, "debit": sheet.actual_deposit},
                 {"account_id": clearing.id, "credit": sheet.actual_deposit, "collector_id": sheet.collector_id}],
                "COLLECTION_SHEET_DEPOSIT", sheet.id, "COLLECTION_SHEETS", user_id,
                f"COLLECTION_SHEET:{sheet.id}:DEPOSIT", sheet.deposit_reference)
            sheet.bank_journal_id = post_journal(entry, user_id).id
        now = datetime.utcnow(); sheet.approved_by_id = user_id; sheet.approved_at = now; sheet.posted_at = now
        sheet.status = "RECONCILED" if sheet.difference == 0 else "POSTED"
        if sheet.status == "RECONCILED":
            sheet.reconciled_at = now
            clear_reconciled_payments(sheet)
        sheet.posting_key = f"COLLECTION_SHEET:{sheet.id}:POST"
        result = serialize(sheet, True); result["collector_clearing_impact"] = f"{money(sheet.expected_deposit - Decimal(sheet.actual_deposit or 0)):.2f}"
        sheet.posting_result = result
        log_audit("COLLECTION_SHEET_POST", "CollectionSheet", sheet.id, user_id, result)
        db.session.commit(); return result
    except Exception as exc:
        db.session.rollback()
        raise SheetError("Collection sheet posting failed", row=getattr(locals().get("item"), "id", None), reason=str(exc))


def clear_reconciled_payments(sheet):
    """Attach receipt metadata to the existing sheet bank journal; never post accounting."""
    if sheet.status != "RECONCILED":
        return
    payments = [db.session.get(Payment, item.payment_id) for item in sorted(sheet.items, key=lambda row: row.id) if item.payment_id]
    remaining = money(sheet.actual_deposit or 0)
    remaining_gross = sum((money(p.amount_collected) for p in payments), Decimal("0.00"))
    for index, payment in enumerate(payments):
        amount = money(payment.amount_collected)
        # Allocate only real bank cash.  Expense-funded clearance is represented by
        # collection_clearance_status rather than inflating deposited_amount.
        if remaining <= 0:
            bank_share = Decimal("0.00")
        elif index == len(payments) - 1 or remaining_gross == amount:
            bank_share = min(amount, remaining)
        else:
            bank_share = min(amount, money(remaining * amount / remaining_gross))
        payment.deposited_amount = bank_share
        payment.deposit_status = "DEPOSITED" if bank_share >= amount else "PARTIALLY_DEPOSITED"
        payment.collection_clearance_status = "CLEARED"
        payment.collection_sheet_id = sheet.id
        payment.collection_sheet_deposit_journal_id = sheet.bank_journal_id
        remaining -= bank_share
        remaining_gross -= amount


def clearance_repair_report(sheet, apply=False):
    """Preview or idempotently repair one known legacy sheet's metadata only."""
    expected = {
        "CS-20260313-0001": {
            "gross": Decimal("12600.00"), "expenses": Decimal("0.00"),
            "actual": Decimal("12600.00"), "difference": Decimal("0.00"),
            "amounts": [Decimal("2100.00")] * 4 + [Decimal("4200.00")],
        }
    }.get(sheet.sheet_number)
    if not expected:
        raise SheetError("This command is restricted to an explicitly approved historical sheet", 409)

    # These are deliberately redundant production guardrails.  In particular, do
    # not derive the expected values from the row that is about to be repaired.
    errors = []
    recalculate(sheet)
    if sheet.status != "RECONCILED": errors.append("status is not RECONCILED")
    if money(sheet.gross_collection) != expected["gross"]: errors.append("gross collection is not 12600.00")
    if money(sheet.total_expenses) != expected["expenses"]: errors.append("expenses are not 0.00")
    if money(sheet.actual_deposit or 0) != expected["actual"]: errors.append("actual deposit is not 12600.00")
    if money(sheet.difference) != expected["difference"]: errors.append("difference is not 0.00")
    if money(sheet.expected_deposit) != expected["gross"]: errors.append("expected deposit is not 12600.00")
    item_amounts = sorted(money(item.amount) for item in sheet.items)
    if item_amounts != expected["amounts"]: errors.append("sheet items do not match 4200.00 + four 2100.00 receipts")
    if len(sheet.items) != 5: errors.append("sheet does not contain exactly five items")
    journal = db.session.get(AccountingJournalEntry, sheet.bank_journal_id) if sheet.bank_journal_id else None
    if not journal: errors.append("existing bank deposit journal is missing")
    elif (journal.status != "POSTED" or journal.source_type != "COLLECTION_SHEET_DEPOSIT"
          or journal.source_id != sheet.id or money(journal.total_debit) != expected["actual"]
          or money(journal.total_credit) != expected["actual"]):
        errors.append("existing bank deposit journal is not the posted 12600.00 journal for this sheet")
    elif (sum((money(line.debit) for line in journal.lines if line.account_id == sheet.bank_account_id), Decimal("0")) != expected["actual"]
          or sum((money(line.credit) for line in journal.lines
                  if line.account_id == sheet.collector.default_collection_account_id), Decimal("0")) != expected["actual"]):
        errors.append("existing deposit journal does not debit the sheet bank and credit collector clearing by 12600.00")

    rows = []
    payment_total = Decimal("0.00")
    for item in sorted(sheet.items, key=lambda row: row.id):
        payment = item.payment
        if not payment:
            errors.append(f"item {item.id} generated payment is missing")
            continue
        payment_total += money(payment.amount_collected)
        if money(payment.amount_collected) != money(item.amount): errors.append(f"payment {payment.id} amount does not match its item")
        if payment.loan_id != item.loan_id: errors.append(f"payment {payment.id} loan does not match its item")
        if payment.collector_id != sheet.collector_id: errors.append(f"payment {payment.id} collector does not match the sheet")
        if payment.collection_method != "CASH_COLLECTOR" or payment.status != "POSTED" or not payment.journal_id or payment.reversed_at:
            errors.append(f"payment {payment.id} is not an unreversed posted collector payment")
        allocations = CollectionDepositAllocation.query.filter_by(payment_id=payment.id).all()
        rows.append({
            "payment_id": payment.id, "receipt_number": payment.receipt_number,
            "customer": item.customer.full_name if item.customer else None,
            "loan_number": item.loan.loan_number if item.loan else None,
            "collected_amount": f"{money(payment.amount_collected):.2f}",
            "current_amount_deposited": f"{money(payment.deposited_amount):.2f}",
            "current_amount_undeposited": f"{money(payment.undeposited_amount):.2f}",
            "current_deposit_status": payment.deposit_status,
            "current_clearance_status": payment.collection_clearance_status,
            "linked_collection_sheet": payment.collection_sheet.sheet_number if payment.collection_sheet else None,
            "linked_deposit_journal": payment.collection_sheet_deposit_journal_id,
            "linked_deposit_batches": [a.deposit_batch_id for a in allocations],
            "proposed_clearance_status": "CLEARED",
            "proposed_deposit_status": "DEPOSITED",
            "proposed_amount_deposited": f"{money(payment.amount_collected):.2f}",
            "proposed_amount_undeposited": "0.00",
            "proposed_linked_deposit_journal": sheet.bank_journal_id,
        })
    if payment_total != expected["gross"]: errors.append("source payment total is not 12600.00")
    if len(rows) != 5: errors.append("exactly five source payments were not identified")
    if errors:
        raise SheetError("Collection sheet clearance repair validation failed", 409, errors=errors)

    already_cleared = all(
        row["current_clearance_status"] == "CLEARED"
        and row["current_amount_deposited"] == row["collected_amount"]
        and row["linked_collection_sheet"] == sheet.sheet_number
        and row["linked_deposit_journal"] == sheet.bank_journal_id
        for row in rows
    )
    if apply:
        clear_reconciled_payments(sheet)
    return {"mode": "apply" if apply else "preview", "sheet_number": sheet.sheet_number,
            "sheet_status": sheet.status, "bank_journal_id": sheet.bank_journal_id,
            "bank_journal_number": journal.journal_no, "already_cleared": already_cleared,
            "message": "No repair required. Collection Sheet receipts are already cleared." if already_cleared else None,
            "payments": rows, "totals": {"receipt_count": len(rows), "collected": f"{payment_total:.2f}",
                "currently_deposited": f"{sum((Decimal(r['current_amount_deposited']) for r in rows), Decimal('0')):.2f}",
                "currently_undeposited": f"{sum((Decimal(r['current_amount_undeposited']) for r in rows), Decimal('0')):.2f}",
                "proposed_cleared": f"{payment_total:.2f}"},
            "accounting_journal_changes": "NONE", "customer_payment_changes": "NONE",
            "loan_allocation_changes": "NONE", "bank_cash_movement": "NONE"}


def clearance_safety_snapshot(sheet):
    """Return immutable financial state used to prove a metadata repair is isolated."""
    payment_ids = [item.payment_id for item in sheet.items if item.payment_id]
    loan_ids = [item.loan_id for item in sheet.items]
    payments = Payment.query.filter(Payment.id.in_(payment_ids)).order_by(Payment.id).all()
    accounts = [sheet.bank_account_id]
    if sheet.collector and sheet.collector.default_collection_account_id:
        accounts.append(sheet.collector.default_collection_account_id)
    account_balances = {}
    for account_id in filter(None, accounts):
        debit, credit = db.session.query(
            func.coalesce(func.sum(AccountingJournalLine.debit), 0),
            func.coalesce(func.sum(AccountingJournalLine.credit), 0),
        ).join(AccountingJournalEntry).filter(
            AccountingJournalLine.account_id == account_id,
            AccountingJournalEntry.status == "POSTED",
        ).one()
        account_balances[str(account_id)] = f"{money(Decimal(debit) - Decimal(credit)):.2f}"
    receivable_balances = {}
    receivable_rows = db.session.query(
        AccountingJournalLine.loan_id, AccountingAccount.account_subtype,
        func.coalesce(func.sum(AccountingJournalLine.debit), 0),
        func.coalesce(func.sum(AccountingJournalLine.credit), 0),
    ).join(AccountingJournalEntry).join(AccountingAccount).filter(
        AccountingJournalLine.loan_id.in_(loan_ids), AccountingJournalEntry.status == "POSTED",
        AccountingAccount.account_subtype.in_(["LOAN_RECEIVABLE", "INTEREST_RECEIVABLE"]),
    ).group_by(AccountingJournalLine.loan_id, AccountingAccount.account_subtype).all()
    for loan_id, subtype, debit, credit in receivable_rows:
        receivable_balances[f"{loan_id}:{subtype}"] = f"{money(Decimal(debit) - Decimal(credit)):.2f}"
    return {
        "journal_count": AccountingJournalEntry.query.count(),
        "journal_line_count": AccountingJournalLine.query.count(),
        "account_balances": account_balances,
        "loan_receivable_balances": receivable_balances,
        "payment_financials": [{"id": p.id, "amount": str(p.amount_collected), "principal": str(p.principal_paid),
            "interest": str(p.interest_paid), "penalty": str(p.penalty_paid), "other_fee": str(p.other_fee_paid),
            "journal_id": p.journal_id} for p in payments],
        "loan_financials": [{"id": loan.id, "principal": str(loan.principal_amount),
            "total_payable": str(loan.total_payable), "cash_paid_cache": str(loan.cash_paid_cache),
            "outstanding_amount": str(loan.outstanding_amount)}
            for loan in Loan.query.filter(Loan.id.in_(loan_ids)).order_by(Loan.id).all()],
        "sheet": {"gross": str(sheet.gross_collection), "actual": str(sheet.actual_deposit),
            "difference": str(sheet.difference), "status": sheet.status},
    }


def reverse(sheet, user_id, reason, reversal_date=None):
    if sheet.status not in {"POSTED", "RECONCILED"}: raise SheetError("Only posted collection sheets can be reversed", 409)
    if not reason: raise SheetError("Reversal reason is required")
    reversal_date = reversal_date or date.today()
    if sheet.bank_journal_id: reverse_journal(db.session.get(AccountingJournalEntry, sheet.bank_journal_id), reversal_date, reason, user_id)
    for expense in sheet.expenses:
        if expense.journal_entry_id: reverse_journal(db.session.get(AccountingJournalEntry, expense.journal_entry_id), reversal_date, reason, user_id)
    for item in sheet.items:
        if item.payment_id:
            payment = db.session.get(Payment, item.payment_id)
            payment.deposited_amount = Decimal("0.00"); payment.collection_clearance_status = "UNDEPOSITED"
            payment.collection_sheet_deposit_journal_id = None
            reverse_payment(payment, reversal_date, reason, user_id); item.posting_status = "REVERSED"
    sheet.status = "REVERSED"; sheet.reversed_at = datetime.utcnow(); sheet.reversed_by_id = user_id; sheet.reversal_reason = reason
    log_audit("COLLECTION_SHEET_REVERSE", "CollectionSheet", sheet.id, user_id, {"reason": reason})
    db.session.commit(); return serialize(sheet, True)


def search_loans(query):
    q = (query or "").strip()
    if not q: return []
    pattern = f"%{q}%"
    loans = Loan.query.join(Customer).filter(or_(Loan.loan_number.ilike(pattern), Customer.full_name.ilike(pattern),
                                                  Customer.nic_number.ilike(pattern), Customer.mobile.ilike(pattern))).limit(20).all()
    return [{"loan_id": l.id, "loan_number": l.loan_number, "customer_id": l.customer_id,
             "customer_name": l.customer.full_name, "nic": l.customer.nic_number, "mobile": l.customer.mobile,
             "loan_status": l.status, "contractual_outstanding": f"{money(l.outstanding):.2f}",
             "delay_interest_outstanding": f"{money(sum((Decimal(x.delay_interest_accrued or 0) - Decimal(x.delay_interest_paid or 0) for x in l.ledger_entries), Decimal('0'))):.2f}"} for l in loans]
