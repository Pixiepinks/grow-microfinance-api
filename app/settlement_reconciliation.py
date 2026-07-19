"""Safe, explicit reconciliation for loans paid before automatic settlement existed."""
from datetime import datetime
from decimal import Decimal
from .extensions import db
from .models import Loan, Payment, CustomerCreditBalance, AccountingJournalEntry, AccountingJournalLine
from .accounting import (money, customer_advance_account, account_subtype, create_draft_journal,
                         post_journal, require_open_accounting_period, log_audit, AccountingError)
from .loan_repair import repair_legacy_loan_configuration

TOLERANCE = Decimal("0.01")
ELIGIBLE = {"ACTIVE", "OVERDUE", "DISBURSED"}
SOURCE_TYPE = "LEGACY_LOAN_RECONCILIATION"
RECONCILIATION_SOURCE_TYPE = "LOAN_RECONCILIATION"


def _valid_payments(loan):
    return sorted([p for p in loan.payments if not p.reversed_at and (p.status or "").upper() != "REVERSED"],
                  key=lambda p: (p.collection_date or p.payment_date, p.created_at, p.id))


def _balances(loan):
    entries = list(loan.ledger_entries)
    if not entries:
        return None, ["ledger is missing; cannot prove contractual balances"]
    components = {
        "principal": sum((money(e.principal_amount) - money(e.principal_paid) for e in entries), Decimal()),
        "interest": sum((money(e.interest_amount) - money(e.interest_paid) for e in entries), Decimal()),
        "delay_interest": sum((money(e.delay_interest_accrued) - money(e.delay_interest_paid) for e in entries), Decimal()),
    }
    warnings = []
    if any(value < -TOLERANCE for value in components.values()):
        warnings.append("ledger contains over-applied component balances")
    unpaid = {key: money(max(value, Decimal())) for key, value in components.items()}
    return unpaid, warnings


def preview(loan):
    """Return a non-mutating proof/result.  A warning means it must not be posted."""
    warnings = []
    current = (loan.status or "").strip().upper()
    payments = _valid_payments(loan)
    paid = money(sum((money(p.amount_collected) for p in payments), Decimal()))
    due = money(loan.total_payable)
    unpaid, ledger_warnings = _balances(loan)
    warnings.extend(ledger_warnings)
    raw_outstanding = money(due - paid)
    final_payment = None
    cumulative = Decimal()
    for payment in payments:
        cumulative = money(cumulative + money(payment.amount_collected))
        if cumulative >= due - TOLERANCE:
            final_payment = payment
            break
    eligible = current in ELIGIBLE and (paid >= due - TOLERANCE or raw_outstanding <= TOLERANCE)
    if not eligible:
        warnings.append("loan is not an eligible legacy settlement candidate")
    if unpaid is not None and any(amount > TOLERANCE for amount in unpaid.values()):
        warnings.append("unpaid principal, interest, penalties, delay interest, or collectible fees remain")
    if not final_payment or not (final_payment.collection_date or final_payment.payment_date):
        warnings.append("settlement date requires review")
    key = f"LEGACY-SETTLEMENT-{loan.id}"
    credit = CustomerCreditBalance.query.filter_by(source_type=SOURCE_TYPE, source_id=key).first()
    overpayment = money(max(Decimal(), paid - due))
    adjustment_required = overpayment > TOLERANCE and not credit
    if adjustment_required:
        journal = AccountingJournalEntry.query.filter_by(id=final_payment.journal_id).first() if final_payment.journal_id else None
        if not journal or (journal.status or "").upper() != "POSTED":
            warnings.append("accounting reconciliation required: final payment has no complete posted journal")
        else:
            warnings.append("accounting adjustment will reclassify historical excess; no cash or bank is posted")
    already = current == "SETTLED" and loan.settlement_reason == "LEGACY_FULLY_REPAID"
    if already:
        warnings.append("already reconciled")
    return {"loan_id": loan.id, "loan_number": loan.loan_number, "customer_id": loan.customer_id,
            "customer_name": loan.customer.full_name if loan.customer else None, "current_status": loan.status,
            "total_payable": due, "total_paid": paid, "raw_outstanding": raw_outstanding,
            "outstanding": max(Decimal(), raw_outstanding), "overpayment": overpayment,
            "final_payment_id": final_payment.id if final_payment else None,
            "settled_date": (final_payment.collection_date or final_payment.payment_date) if final_payment else None,
            "proposed_status": "SETTLED" if eligible else loan.status,
            "customer_credit_exists": bool(credit), "accounting_adjustment_required": adjustment_required,
            "warnings": warnings, "can_post": eligible and not warnings and not already}


def candidates():
    # DB filtering is deliberately broad; preview supplies canonical ledger validation.
    rows = [preview(loan) for loan in Loan.query.all() if (loan.status or "").strip().upper() in ELIGIBLE]
    return [row for row in rows if row["total_paid"] >= row["total_payable"] - TOLERANCE or row["raw_outstanding"] <= TOLERANCE]


def _adjustment(loan, result, user_id):
    final_payment = Payment.query.get(result["final_payment_id"])
    original = AccountingJournalEntry.query.get(final_payment.journal_id)
    advance = customer_advance_account()
    # A prior posting to Customer Advances is already the required liability.
    if any(line.account_id == advance.id and money(line.credit) > 0 for line in original.lines):
        return None
    candidates = [line for line in original.lines if money(line.credit) > 0 and line.account_id != advance.id]
    if not candidates:
        raise AccountingError("accounting reconciliation required: no credit source can be proven")
    source = candidates[-1].account_id
    require_open_accounting_period(result["settled_date"])
    entry = create_draft_journal(result["settled_date"], "Legacy loan overpayment reclassification",
        [{"account_id": source, "debit": result["overpayment"], "customer_id": loan.customer_id, "loan_id": loan.id},
         {"account_id": advance.id, "credit": result["overpayment"], "customer_id": loan.customer_id, "loan_id": loan.id}],
        "LEGACY_LOAN_SETTLEMENT", loan.id, "LOANS", user_id, f"LEGACY-SETTLEMENT:{loan.id}")
    entry.loan_id = loan.id; entry.customer_id = loan.customer_id
    return post_journal(entry, user_id)


def post(loan, user_id=None):
    result = preview(loan)
    if "already reconciled" in result["warnings"]:
        log_audit("LEGACY_LOAN_SETTLEMENT_SKIPPED", "Loan", loan.id, user_id, result)
        return {**result, "processed": False}
    blocking = [w for w in result["warnings"] if not w.startswith("accounting adjustment will")]
    if blocking:
        log_audit("LEGACY_LOAN_SETTLEMENT_SKIPPED", "Loan", loan.id, user_id, result)
        return {**result, "processed": False}
    journal = _adjustment(loan, result, user_id) if result["accounting_adjustment_required"] else None
    key = f"LEGACY-SETTLEMENT-{loan.id}"
    credit = CustomerCreditBalance.query.filter_by(source_type=SOURCE_TYPE, source_id=key).first()
    if result["overpayment"] > TOLERANCE and not credit:
        credit = CustomerCreditBalance(customer_id=loan.customer_id, loan_id=loan.id, payment_id=None,
          credit_number=f"GROW-LR-{loan.id:08d}", credit_date=result["settled_date"], source_type=SOURCE_TYPE, source_id=key,
          original_amount=result["overpayment"], available_amount=result["overpayment"], applied_amount=Decimal(), refunded_amount=Decimal(),
          status="AVAILABLE", reference=key, remarks="Legacy loan settlement reconciliation", journal_entry_id=journal.id if journal else None, created_by_id=user_id)
        db.session.add(credit)
    loan.status = "SETTLED"; loan.customer_credit_balance = result["overpayment"]
    loan.settled_date = result["settled_date"]; loan.settled_at = datetime.combine(result["settled_date"], datetime.min.time())
    loan.settled_by_id = user_id; loan.settlement_payment_id = result["final_payment_id"]
    loan.settlement_journal_id = journal.id if journal else loan.settlement_journal_id; loan.settlement_reason = "LEGACY_FULLY_REPAID"
    for entry in loan.ledger_entries:
        due = money(entry.principal_amount) + money(entry.interest_amount) + money(entry.delay_interest_accrued)
        paid = money(entry.principal_paid) + money(entry.interest_paid) + money(entry.delay_interest_paid)
        if paid >= due - TOLERANCE: entry.status = "PAID"; entry.paid_amount = min(paid, due)
    log_audit("LEGACY_LOAN_SETTLEMENT_POSTED", "Loan", loan.id, user_id, {**result, "journal_id": journal.id if journal else None})
    return {**result, "processed": True, "settlement_journal_id": journal.id if journal else None}


def _apply_historical_payments_to_ledger(loan, amount, paid_date):
    """Rebuild ledger balances from historical cash without posting new journals."""
    remaining = money(amount)
    for entry in sorted(loan.ledger_entries, key=lambda item: (item.due_date, item.installment_no)):
        for due_field, paid_field in (("delay_interest_accrued", "delay_interest_paid"),
                                      ("interest_amount", "interest_paid"),
                                      ("principal_amount", "principal_paid")):
            if remaining <= 0:
                break
            due = money(getattr(entry, due_field) or 0)
            already_paid = money(getattr(entry, paid_field) or 0)
            applied = min(remaining, max(Decimal("0.00"), due - already_paid))
            if applied:
                setattr(entry, paid_field, money(already_paid + applied))
                remaining = money(remaining - applied)
        paid = money((entry.principal_paid or 0) + (entry.interest_paid or 0) + (entry.delay_interest_paid or 0))
        due = money((entry.principal_amount or 0) + (entry.interest_amount or 0) + (entry.delay_interest_accrued or 0))
        entry.paid_amount = min(paid, due)
        entry.paid_date = paid_date if entry.paid_amount else entry.paid_date
        entry.status = "PAID" if entry.paid_amount >= due - TOLERANCE else ("PARTIAL" if entry.paid_amount else "PENDING")
    return remaining


def reconcile(loan, user_id=None):
    """Repair and settle a legacy loan, returning the button's compact JSON contract."""
    repaired_fields = repair_legacy_loan_configuration(loan, user_id=user_id)
    payments = _valid_payments(loan)
    paid = money(sum((money(payment.amount_collected) for payment in payments), Decimal("0.00")))
    due = money(loan.total_payable)
    remaining = max(Decimal("0.00"), money(due - paid))
    last_payment = next((payment for payment in reversed(payments) if payment.collection_date or payment.payment_date), None)
    settlement_date = ((last_payment.collection_date or last_payment.payment_date) if last_payment else None)

    if remaining > TOLERANCE:
        log_audit("LOAN_RECONCILIATION", "Loan", loan.id, user_id,
                  {"repaired_fields": repaired_fields, "remaining_balance": str(remaining)})
        return {"success": True, "loan_repaired": bool(repaired_fields), "loan_settled": False,
                "remaining_balance": remaining}

    if not settlement_date:
        raise ValueError("A payment date is required to settle this loan.")
    unapplied = _apply_historical_payments_to_ledger(loan, paid, settlement_date)
    # Cash beyond the contract is customer money, not a negative receivable.
    credit_amount = money(max(Decimal("0.00"), paid - due))
    credit = CustomerCreditBalance.query.filter_by(
        source_type=RECONCILIATION_SOURCE_TYPE, source_id=str(loan.id)
    ).first()
    if credit_amount > TOLERANCE and not credit:
        credit = CustomerCreditBalance(
            customer_id=loan.customer_id, loan_id=loan.id, payment_id=None,
            credit_number=f"GROW-RC-{loan.id:08d}", credit_date=settlement_date,
            source_type=RECONCILIATION_SOURCE_TYPE, source_id=str(loan.id),
            original_amount=credit_amount, available_amount=credit_amount,
            applied_amount=Decimal("0.00"), refunded_amount=Decimal("0.00"), status="AVAILABLE",
            reference=f"LOAN-RECONCILIATION:{loan.id}", remarks="Loan reconciliation overpayment",
            created_by_id=user_id,
        )
        db.session.add(credit)
    loan.customer_credit_balance = money(sum((Decimal(item.available_amount or 0) for item in
        CustomerCreditBalance.query.filter_by(loan_id=loan.id).filter(
            CustomerCreditBalance.status.in_(("AVAILABLE", "PARTIALLY_APPLIED"))).all()), Decimal("0.00")) + (credit_amount if credit_amount > TOLERANCE and not credit else Decimal("0.00")))
    loan.status = "SETTLED"
    loan.settled_date = settlement_date
    loan.settled_at = datetime.combine(settlement_date, datetime.min.time())
    loan.settled_by_id = user_id
    loan.settlement_payment_id = last_payment.id
    loan.settlement_reason = "RECONCILED_FULLY_REPAID"
    # SETTLED is excluded by the accrual job; this marker also records its final boundary.
    loan.accrual_processed_through = max((loan.accrual_processed_through or settlement_date), settlement_date)
    log_audit("LOAN_RECONCILED", "Loan", loan.id, user_id,
              {"repaired_fields": repaired_fields, "customer_credit": str(credit_amount), "unapplied": str(unapplied)})
    result = {"success": True, "loan_repaired": bool(repaired_fields), "loan_settled": True}
    if credit_amount > TOLERANCE:
        result["customer_credit"] = credit_amount
    if repaired_fields or credit_amount > TOLERANCE:
        result["message"] = "Loan reconciled successfully."
    return result
