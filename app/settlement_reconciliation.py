"""Safe, explicit reconciliation for loans paid before automatic settlement existed."""
from datetime import datetime
from decimal import Decimal
import logging
from .extensions import db
from .models import Loan, Payment, CustomerCreditBalance, AccountingJournalEntry, AccountingJournalLine, LoanChargeWaiver
from .accounting import (money, customer_advance_account, account_subtype, create_draft_journal,
                         post_journal, require_open_accounting_period, log_audit, AccountingError)
from .loan_repair import repair_legacy_loan_configuration
from .loan_status import (AUTHORITATIVE_STATUS_FIELD, contractual_balances,
                          serialize_loan_status, update_loan_settlement_status)

TOLERANCE = Decimal("0.01")
logger = logging.getLogger(__name__)
ELIGIBLE = {"ACTIVE", "OVERDUE", "DISBURSED"}
SOURCE_TYPE = "LEGACY_LOAN_RECONCILIATION"
RECONCILIATION_SOURCE_TYPE = "LOAN_RECONCILIATION"


class SettlementPersistenceError(RuntimeError):
    """Raised when a committed settlement status is not what was calculated."""


def _status_debug(phase, loan, balances, **extra):
    """Log only reconciliation state; never log customer or receipt details."""
    logger.info(
        "RECONCILIATION_STATUS_DEBUG phase=%s loan_id=%s status_field=%s "
        "status=%s principal_outstanding=%s contractual_interest_outstanding=%s %s",
        phase, loan.id, AUTHORITATIVE_STATUS_FIELD, serialize_loan_status(loan),
        balances["principal_outstanding"], balances["contractual_interest_outstanding"],
        " ".join(f"{key}={value}" for key, value in extra.items()),
    )


def finalize_loan_reconciliation(loan_id, reconciliation_date=None, user_id=None,
                                 waiver_amount=None, reason=None,
                                 approval_reference=None, waive_delay_interest=False):
    """Atomically finalize reconciliation and verify the committed Loan.status."""
    loan = Loan.query.filter_by(id=loan_id).with_for_update().one()
    before = contractual_balances(loan)
    _status_debug("before", loan, before, before_status=serialize_loan_status(loan))
    result = post(
        loan, user_id, waive_delay_interest=waive_delay_interest,
        delay_interest_waiver_amount=waiver_amount,
        approval_reference=approval_reference, reason=reason,
    )
    if not result.get("processed"):
        db.session.rollback()
        return loan, result

    # Recalculate from canonical contractual components immediately before the
    # one transaction is committed.  Delay interest is intentionally excluded.
    loan, balances = update_loan_settlement_status(
        loan.id, reconciliation_date or result.get("settled_date"), user_id, loan=loan
    )
    calculated_status = serialize_loan_status(loan)
    db.session.flush()
    _status_debug("before_commit", loan, balances,
                  assigned_status=calculated_status, settled_at=loan.settled_at,
                  dirty=loan in db.session.dirty)
    db.session.commit()
    db.session.expire_all()
    persisted_loan = db.session.get(Loan, loan_id)
    persisted_balances = contractual_balances(persisted_loan)
    persisted_status = serialize_loan_status(persisted_loan)
    _status_debug("after_commit", persisted_loan, persisted_balances,
                  persisted_status=persisted_status, settled_at=persisted_loan.settled_at)
    if calculated_status == "SETTLED" and persisted_status != "SETTLED":
        raise SettlementPersistenceError(
            f"Loan {loan_id} calculated SETTLED but persisted {persisted_status!r}"
        )
    return persisted_loan, result


def _valid_payments(loan):
    """Receipts that represent cash successfully received from the customer.

    Allocation fields deliberately are not used here: legacy receipts can contain
    cash that was never allocated to a principal/interest ledger component.
    """
    return sorted([
        payment for payment in loan.payments
        if (payment.status or "").upper() == "POSTED" and not payment.reversed_at
    ], key=lambda payment: (payment.collection_date or payment.payment_date, payment.created_at, payment.id))


def _receipt_journals(payments):
    return [journal for payment in payments if payment.journal_id
            for journal in [AccountingJournalEntry.query.get(payment.journal_id)]
            if journal and (journal.status or "").upper() == "POSTED"]


def _existing_advance_journal(payments, required_amount=Decimal("0.00")):
    # A missing configuration must not make a read-only preview fail.  Posting
    # will still require the configured liability account if reclassification is needed.
    try:
        advance = customer_advance_account()
    except AccountingError:
        return None
    for journal in _receipt_journals(payments):
        credited = money(sum((money(line.credit) for line in journal.lines if line.account_id == advance.id), Decimal("0.00")))
        if credited >= money(required_amount) - TOLERANCE:
            return journal
    return None


def _excess_source_accounts(payments):
    """Return receivable/suspense accounts that the posted receipt credits prove.

    We never infer a source from cash/bank or income lines.  If none is provable,
    posting is blocked rather than creating an unsupported reclassification.
    """
    allowed = {"LOAN_RECEIVABLE", "INTEREST_RECEIVABLE", "PENALTY_RECEIVABLE", "OTHER_CURRENT_ASSET", "SUSPENSE"}
    result = []
    for journal in _receipt_journals(payments):
        for line in journal.lines:
            if money(line.credit) > TOLERANCE and account_subtype(line.account) in allowed:
                result.append((line.account_id, line.account.account_name, money(line.credit)))
    return result


def _balances(loan):
    """Return the collectible ledger component balances used for settlement."""
    entries = list(loan.ledger_entries)
    if not entries:
        return None, ["ledger is missing; cannot prove contractual balances"]
    components = {
        "principal": sum((money(e.principal_amount) - money(e.principal_paid) for e in entries), Decimal()),
        "interest": sum((money(e.interest_amount) - money(e.interest_paid) for e in entries), Decimal()),
        # Delay interest is the ledger's penalty receivable component.
        "penalty": sum((money(e.delay_interest_accrued) - money(e.delay_interest_paid) - money(e.delay_interest_waived) for e in entries), Decimal()),
    }
    warnings = []
    if any(value < -TOLERANCE for value in components.values()):
        warnings.append("ledger contains over-applied component balances")
    unpaid = {key: money(max(value, Decimal("0.00"))) for key, value in components.items()}
    return unpaid, warnings


def _normalized_remaining_balance(value):
    """Normalize preview values before settlement eligibility comparisons."""
    return Decimal(str(value or "0"))


def _is_settlement_eligible(result):
    remaining_balance = _normalized_remaining_balance(result.get("remaining_balance"))
    component_balances = result.get("component_balances") or {}
    return (
        remaining_balance <= TOLERANCE
        # Penalty/delay interest is deliberately not contractual settlement.
        # It remains visible and collectible as a separate receivable.
        and Decimal(str(component_balances.get("principal", "0"))) <= TOLERANCE
        and Decimal(str(component_balances.get("interest", "0"))) <= TOLERANCE
    )


def preview(loan, as_of_date=None):
    """Return a non-mutating, cash-receipt-based settlement proof/result."""
    warnings = []
    if not loan.term_type or loan.term_value is None:
        warnings.append("Loan term metadata is missing.")
    current = (loan.status or "").strip().upper()
    payments = _valid_payments(loan)
    if as_of_date is not None:
        payments = [payment for payment in payments if (payment.collection_date or payment.payment_date)
                    and (payment.collection_date or payment.payment_date) <= as_of_date]
    total_cash_received = money(sum((money(payment.amount_collected) for payment in payments), Decimal("0.00")))
    # total_payable is the established contractual due.  Do not derive this from
    # clamped display balances or payment allocation fields.
    unpaid, ledger_warnings = _balances(loan)
    # Delay interest is a legitimate receivable, not an overpayment merely
    # because it is outside the original contractual total.
    delay_accrued = money(sum((money(e.delay_interest_accrued) for e in loan.ledger_entries), Decimal()))
    delay_paid = money(sum((money(e.delay_interest_paid) for e in loan.ledger_entries), Decimal()))
    delay_waived = money(sum((money(e.delay_interest_waived) for e in loan.ledger_entries), Decimal()))
    contractual_due = money(sum((money(e.principal_amount) + money(e.interest_amount) for e in loan.ledger_entries), Decimal()))
    # Cash receipts are only applied to the contractual waterfall.  Penalty is
    # intentionally excluded until an explicit reconciliation collection.
    legitimate_loan_due = contractual_due + delay_accrued - delay_waived
    total_applied_to_loan = money(min(total_cash_received, contractual_due))
    unapplied_excess = money(max(total_cash_received - total_applied_to_loan, Decimal("0.00")))
    raw_balance = money(contractual_due - total_cash_received)
    remaining_balance = money(max(raw_balance, Decimal("0.00")))
    proposed_customer_credit = money(max(-raw_balance, Decimal("0.00")))
    warnings.extend(ledger_warnings)
    final_payment = next((payment for payment in reversed(payments)
                          if payment.collection_date or payment.payment_date), None)
    # ACTIVE is deliberately eligible: this endpoint is what transitions a paid
    # loan to SETTLED.  Compare normalized Decimal values, never truthy strings.
    eligible = current in ELIGIBLE and (remaining_balance <= TOLERANCE or (unpaid or {}).get("principal", Decimal()) <= TOLERANCE and (unpaid or {}).get("interest", Decimal()) <= TOLERANCE)
    if not eligible:
        warnings.append("loan is not an eligible legacy settlement candidate")
    # When historical allocations are incomplete, post() reconstructs them from
    # the valid receipts after this full-cash proof succeeds.
    if not final_payment:
        warnings.append("settlement date requires review")
    key = f"LEGACY-SETTLEMENT-{loan.id}"
    credit = CustomerCreditBalance.query.filter_by(source_type=SOURCE_TYPE, source_id=key).first()
    # Preserve legacy allocation fields for audit, while exposing the effect of
    # this non-destructive accounting reclassification separately.
    reclassification = AccountingJournalEntry.query.filter_by(
        idempotency_key=f"LOAN-RECONCILIATION:RECLASSIFICATION:{loan.id}"
    ).first()
    delay_reclassified = money(reclassification.total_debit) if reclassification and reclassification.status == "POSTED" else Decimal("0.00")
    advance_journal = _existing_advance_journal(payments, proposed_customer_credit) if proposed_customer_credit > TOLERANCE else None
    source_accounts = _excess_source_accounts(payments) if proposed_customer_credit > TOLERANCE else []
    adjustment_required = proposed_customer_credit > TOLERANCE and not credit and not advance_journal
    if adjustment_required:
        if not source_accounts:
            warnings.append("accounting reconciliation required: no receivable or suspense source can be proven")
        else:
            warnings.append("An accounting adjustment will reclassify the historical excess; no cash or bank account will be posted again.")
    already = current == "SETTLED" and loan.settlement_reason == "LEGACY_FULLY_REPAID"
    if already:
        warnings.append("already reconciled")
    logger.info("Legacy settlement preview loan_id=%s contractual_due=%s posted_cash_received=%s total_applied_to_receivables=%s raw_balance=%s remaining_balance=%s proposed_customer_credit=%s excess_source_accounts=%s",
                loan.id, legitimate_loan_due, total_cash_received, total_applied_to_loan, raw_balance,
                remaining_balance, proposed_customer_credit, source_accounts)
    return {"loan_id": loan.id, "loan_number": loan.loan_number, "customer_id": loan.customer_id,
            "customer_name": loan.customer.full_name if loan.customer else None, "current_status": loan.status,
            "total_payable": legitimate_loan_due, "total_paid": total_cash_received,
            "principal": money(loan.principal_amount), "normal_interest": money(loan.total_payable - loan.principal_amount),
            "cash_received": total_cash_received,
            "principal_collected": money(sum((money(e.principal_paid) for e in loan.ledger_entries), Decimal())),
            "normal_interest_collected": money(sum((money(e.interest_paid) for e in loan.ledger_entries), Decimal())),
            "contractual_principal_due": money(sum((money(e.principal_amount) for e in loan.ledger_entries), Decimal())),
            "contractual_principal_paid": money(sum((money(e.principal_paid) for e in loan.ledger_entries), Decimal())),
            "contractual_principal_outstanding": (unpaid or {}).get("principal", Decimal("0.00")),
            "contractual_interest_due": money(sum((money(e.interest_amount) for e in loan.ledger_entries), Decimal())),
            "contractual_interest_paid": money(sum((money(e.interest_paid) for e in loan.ledger_entries), Decimal())),
            "contractual_interest_outstanding": (unpaid or {}).get("interest", Decimal("0.00")),
            "delay_interest_accrued": delay_accrued, "delay_interest_collected": delay_paid,
            "historical_delay_interest_allocated": delay_paid,
            "delay_interest_reclassified": delay_reclassified,
            "effective_delay_interest_paid": money(max(Decimal("0.00"), delay_paid-delay_reclassified)),
            "delay_interest_outstanding": money(max(Decimal(), delay_accrued-delay_paid-delay_waived)),
            "proposed_delay_interest_waiver": Decimal("0.00"),
            "delay_interest_waived": delay_waived,
            "remaining_balance_after_waiver": money(max(Decimal(), total_cash_received - total_cash_received)),
            "total_cash_received": total_cash_received, "total_applied_to_loan": total_applied_to_loan,
            "unapplied_excess": unapplied_excess, "raw_balance": raw_balance,
            "raw_outstanding": raw_balance, "remaining_balance": remaining_balance,
            "outstanding": remaining_balance, "overpayment": proposed_customer_credit,
            "proposed_customer_credit": proposed_customer_credit,
            "component_balances": unpaid or {},
            "final_payment_id": final_payment.id if final_payment else None,
            "settled_date": (final_payment.collection_date or final_payment.payment_date) if final_payment else None,
            "settlement_date": (final_payment.collection_date or final_payment.payment_date) if final_payment else None,
            "proposed_status": "SETTLED" if eligible else loan.status,
            "customer_credit_exists": bool(credit), "accounting_adjustment_required": adjustment_required,
            "warnings": warnings,
            "can_post": eligible and not already and not any(warning not in {
                "Loan term metadata is missing.",
                "An accounting adjustment will reclassify the historical excess; no cash or bank account will be posted again."
            } and not warning.startswith("accounting reconciliation required:") for warning in warnings)}


def candidates():
    # DB filtering is deliberately broad; preview supplies canonical ledger validation.
    rows = [preview(loan) for loan in Loan.query.all() if (loan.status or "").strip().upper() in ELIGIBLE]
    return [row for row in rows if row["total_paid"] >= row["total_payable"] - TOLERANCE or row["raw_outstanding"] <= TOLERANCE]


def _adjustment(loan, result, user_id):
    payments = _valid_payments(loan)
    existing_advance = _existing_advance_journal(payments, result["proposed_customer_credit"])
    if existing_advance:
        return existing_advance
    sources = _excess_source_accounts(payments)
    # Old allocations recorded ordinary cash as delay-interest payment.  The
    # delay receivable is the proven classification source in that case; do
    # not ever use the receipt's cash/bank line for this correction.
    historical_delay = money(result.get("historical_delay_interest_allocated", 0))
    if historical_delay >= result["proposed_customer_credit"] - TOLERANCE:
        try:
            receivable = __import__("app.accounting", fromlist=["resolve_system_account"]).resolve_system_account("DELAY_INTEREST_RECEIVABLE")
            sources.append((receivable.id, receivable.account_name, historical_delay))
        except AccountingError:
            pass
    if not sources:
        raise AccountingError("accounting reconciliation required: no receivable or suspense source can be proven")
    # A receipt can have several receivable credits; choose only a source whose
    # recorded credit can cover the reclassification, never an arbitrary income
    # or cash/bank line.
    source = next((account_id for account_id, _name, amount in reversed(sources)
                   if amount >= result["proposed_customer_credit"]), None)
    if source is None:
        raise AccountingError("accounting reconciliation required: excess source account cannot be proven")
    advance = customer_advance_account()
    require_open_accounting_period(result["settled_date"])
    entry = create_draft_journal(result["settled_date"], "Reclassification of historical ordinary payment previously allocated to delay interest; transferred to customer credit during loan reconciliation.",
        [{"account_id": source, "debit": result["proposed_customer_credit"], "customer_id": loan.customer_id, "loan_id": loan.id},
         {"account_id": advance.id, "credit": result["proposed_customer_credit"], "customer_id": loan.customer_id, "loan_id": loan.id}],
        "LOAN_RECONCILIATION", loan.id, "LOANS", user_id, f"LOAN-RECONCILIATION:RECLASSIFICATION:{loan.id}")
    entry.loan_id = loan.id; entry.customer_id = loan.customer_id
    return post_journal(entry, user_id)


def _has_provable_reclassification_source(loan, result):
    if _excess_source_accounts(_valid_payments(loan)):
        return True
    if money(result.get("historical_delay_interest_allocated", 0)) < result["proposed_customer_credit"] - TOLERANCE:
        return False
    try:
        __import__("app.accounting", fromlist=["resolve_system_account"]).resolve_system_account("DELAY_INTEREST_RECEIVABLE")
        customer_advance_account()
        return True
    except AccountingError:
        return False


def post(loan, user_id=None, waive_delay_interest=False, delay_interest_waiver_amount=None, approval_reference=None, reason=None):
    result = preview(loan)
    if "already reconciled" in result["warnings"]:
        log_audit("LEGACY_LOAN_SETTLEMENT_SKIPPED", "Loan", loan.id, user_id, result)
        return {**result, "processed": False}
    # A waiver is an independent optional operation.  In particular, a false
    # checkbox is *not* a reason to skip reclassification or settlement.
    requested_waiver = money(delay_interest_waiver_amount or 0)
    if waive_delay_interest and delay_interest_waiver_amount is None:
        raise AccountingError("delay_interest_waiver_amount must be explicitly entered")
    waiver_journal_id = None
    if requested_waiver > TOLERANCE:
        if not waive_delay_interest:
            # Amount is authoritative; tolerate clients with the newer field
            # but an omitted legacy boolean.
            waive_delay_interest = True
        if delay_interest_waiver_amount is None:
            raise AccountingError("delay_interest_waiver_amount must be explicitly entered")
        if not reason or not str(reason).strip():
            raise AccountingError("reason is required for a delay interest waiver")
        requested = requested_waiver
        if requested <= 0 or requested > result["delay_interest_outstanding"] + TOLERANCE:
            raise AccountingError("delay interest waiver must not exceed unpaid delay interest")
        settlement_date = result["settled_date"] or datetime.utcnow().date()
        require_open_accounting_period(settlement_date)
        receivable = __import__("app.accounting", fromlist=["resolve_system_account"]).resolve_system_account("DELAY_INTEREST_RECEIVABLE")
        expense = __import__("app.accounting", fromlist=["resolve_system_account"]).resolve_system_account("DELAY_INTEREST_WAIVER_EXPENSE")
        waiver = LoanChargeWaiver(waiver_number=f"GROW-DIW-{loan.id:08d}-{LoanChargeWaiver.query.count()+1:04d}", loan_id=loan.id, customer_id=loan.customer_id, waiver_type="DELAY_INTEREST", waiver_date=settlement_date, amount=requested, receivable_account_id=receivable.id, expense_account_id=expense.id, approval_reference=approval_reference, reason=reason, status="APPROVED", approved_by=user_id, approved_at=datetime.utcnow())
        db.session.add(waiver); db.session.flush()
        journal = create_draft_journal(settlement_date, "Delay interest waiver", [{"account_id": expense.id, "debit": requested, "loan_id": loan.id, "customer_id": loan.customer_id}, {"account_id": receivable.id, "credit": requested, "loan_id": loan.id, "customer_id": loan.customer_id}], "DELAY_INTEREST_WAIVER", waiver.id, "LOANS", user_id, f"DELAY_INTEREST_WAIVER:{waiver.id}")
        journal.loan_id=loan.id; journal.customer_id=loan.customer_id; post_journal(journal, user_id); waiver.status="POSTED"; waiver.journal_entry_id=journal.id; waiver_journal_id=journal.id
        remaining = requested
        for entry in sorted(loan.ledger_entries, key=lambda x: (x.due_date, x.installment_no)):
            applied=min(remaining, money(entry.delay_interest_accrued)-money(entry.delay_interest_paid)-money(entry.delay_interest_waived)); entry.delay_interest_waived=money(entry.delay_interest_waived+applied); remaining=money(remaining-applied)
        result = preview(loan)
    # Historical payments may predate ledger allocation.  Apply them only after
    # proving that the loan is paid in full, then re-run the canonical proof.
    # This changes no cash/accounting entries; overpayments still use the
    # accounting adjustment below.
    remaining_balance = _normalized_remaining_balance(result.get("remaining_balance"))
    has_remaining_balance = remaining_balance > TOLERANCE
    if not has_remaining_balance and result["final_payment_id"]:
        _apply_historical_payments_to_ledger(loan, result["total_paid"], result["settled_date"])
        result = preview(loan)
    # Correct a legacy advance only to the extent that it represents an unpaid
    # accrued penalty.  This never touches cash: it debits the existing advance
    # liability and credits the already-accrued receivable.
    for credit in CustomerCreditBalance.query.filter_by(loan_id=loan.id).filter(CustomerCreditBalance.available_amount > 0).all():
        # The ledger allocation proves how much of historic cash belongs to
        # delay interest; it may already be paid, so outstanding is not the
        # right cap for correcting a legacy advance classification.
        amount = min(money(credit.available_amount), result.get("delay_interest_collected", Decimal("0.00")))
        if amount <= TOLERANCE: continue
        settlement_date = result["settled_date"] or datetime.utcnow().date(); require_open_accounting_period(settlement_date)
        advance = customer_advance_account(); receivable = __import__("app.accounting", fromlist=["resolve_system_account"]).resolve_system_account("DELAY_INTEREST_RECEIVABLE")
        journal = create_draft_journal(settlement_date, "Customer advance reclassified to delay interest", [{"account_id": advance.id, "debit": amount, "loan_id":loan.id,"customer_id":loan.customer_id},{"account_id": receivable.id,"credit":amount,"loan_id":loan.id,"customer_id":loan.customer_id}], "DELAY_INTEREST_RECLASSIFICATION", credit.id, "LOANS", user_id, f"DELAY_INTEREST_RECLASSIFICATION:{credit.id}")
        journal.loan_id=loan.id; journal.customer_id=loan.customer_id; post_journal(journal, user_id)
        credit.available_amount=money(credit.available_amount-amount); credit.applied_amount=money(credit.applied_amount+amount); credit.status="RECLASSIFIED" if not credit.available_amount else "PARTIALLY_APPLIED"; credit.applied_to_loan_id=loan.id; credit.source_type="DELAY_INTEREST_RECLASSIFICATION"; credit.correcting_journal_id=journal.id
        result=preview(loan)
    if not _is_settlement_eligible(result):
        log_audit("LEGACY_LOAN_SETTLEMENT_SKIPPED", "Loan", loan.id, user_id, result)
        return {**result, "processed": False}
    blocking = [
        warning for warning in result["warnings"]
        if not warning.lower().startswith("an accounting adjustment will")
        and not warning.startswith("accounting reconciliation required:")
        and warning != "Loan term metadata is missing."
    ]
    if blocking:
        log_audit("LEGACY_LOAN_SETTLEMENT_SKIPPED", "Loan", loan.id, user_id, result)
        return {**result, "processed": False}
    # If the receipt already credited Customer Advances, _adjustment returns that
    # existing journal and deliberately posts nothing new.
    # A historical receipt can predate journals.  It must not prevent a fully
    # paid loan from settling or from recording its customer credit; where a
    # provable source exists, retain the reclassification journal behavior.
    journal = None
    if (result["proposed_customer_credit"] > TOLERANCE and not result["customer_credit_exists"]
            and _has_provable_reclassification_source(loan, result)):
        journal = _adjustment(loan, result, user_id)
    key = f"LEGACY-SETTLEMENT-{loan.id}"
    credit = CustomerCreditBalance.query.filter_by(source_type=SOURCE_TYPE, source_id=key).first()
    if result["proposed_customer_credit"] > TOLERANCE and not credit:
        credit = CustomerCreditBalance(customer_id=loan.customer_id, loan_id=loan.id, payment_id=None,
          credit_number=f"GROW-LR-{loan.id:08d}", credit_date=result["settled_date"], source_type=SOURCE_TYPE, source_id=key,
          original_amount=result["proposed_customer_credit"], available_amount=result["proposed_customer_credit"], applied_amount=Decimal(), refunded_amount=Decimal(),
          status="AVAILABLE", reference=key, remarks="Legacy loan settlement reconciliation", journal_entry_id=journal.id if journal else None, created_by_id=user_id)
        db.session.add(credit)
    # Status is based only on contractual principal and normal interest.  This
    # is the missing persistence step behind previews that proposed SETTLED.
    loan, balances = update_loan_settlement_status(loan.id, result["settled_date"], user_id, loan=loan)
    # Do not report a preview value: report the persisted available liability.
    persisted_credit = money(credit.available_amount) if credit else Decimal("0.00")
    loan.customer_credit_balance = persisted_credit
    loan.settlement_payment_id = result["final_payment_id"]
    loan.settlement_journal_id = journal.id if journal else loan.settlement_journal_id; loan.settlement_reason = "LEGACY_FULLY_REPAID"
    for entry in loan.ledger_entries:
        due = money(entry.principal_amount) + money(entry.interest_amount) + money(entry.delay_interest_accrued)
        paid = money(entry.principal_paid) + money(entry.interest_paid) + money(entry.delay_interest_paid) + money(entry.delay_interest_waived)
        if paid >= due - TOLERANCE: entry.status = "PAID"; entry.paid_amount = min(paid, due)
    logger.info("loan_reconciliation_posted loan_id=%s waiver_requested=%s waiver_amount=%s historical_delay_interest_allocated=%s customer_credit_proposed=%s customer_credit_posted=%s reclassification_journal_id=%s waiver_journal_id=%s calculated_status=%s",
                loan.id, bool(requested_waiver), requested_waiver, result["historical_delay_interest_allocated"], result["proposed_customer_credit"], persisted_credit, journal.id if journal else None, waiver_journal_id, serialize_loan_status(loan))
    log_audit("LEGACY_LOAN_SETTLEMENT_POSTED", "Loan", loan.id, user_id, {**result, "journal_id": journal.id if journal else None})
    return {**result, **balances, "processed": True, "settlement_journal_id": journal.id if journal else None,
            "reclassification_journal_id": journal.id if journal else None,
            "waiver_journal_id": waiver_journal_id,
            "customer_credit_created": persisted_credit, "customer_credit_balance": persisted_credit,
            "delay_interest_waived_this_reconciliation": requested_waiver}


def _apply_historical_payments_to_ledger(loan, amount, paid_date, delay_only=False):
    """Rebuild ledger balances from historical cash without posting new journals."""
    remaining = money(amount)
    for entry in sorted(loan.ledger_entries, key=lambda item: (item.due_date, item.installment_no)):
        # Ordinary historical receipts belong to contractual interest then
        # principal.  Delay interest requires a separate explicit action.
        fields = (("delay_interest_accrued", "delay_interest_paid"),) if delay_only else (("interest_amount", "interest_paid"), ("principal_amount", "principal_paid"))
        for due_field, paid_field in fields:
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
        paid = money(paid + (entry.delay_interest_waived or 0))
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
