from decimal import Decimal

from .accounting import log_audit, money
from .extensions import db
from .loan_ledger import backfill_period_start_dates_from_schedule, derive_loan_metadata_from_ledger, generate_loan_ledger
from .loan_terms import calculate_flat_term_amounts, resolve_loan_term
from .models import Loan, LoanApplication, LoanLedger, Payment


class LoanRepairError(ValueError):
    pass


def _normalize_status(value):
    return (value or "").upper()


def is_safe_to_repair_defective_loan(loan: Loan) -> tuple[bool, list[str]]:
    reasons = []
    if _normalize_status(loan.status) != "ACTIVE":
        reasons.append("loan status is not ACTIVE")
    if money(loan.total_paid) != Decimal("0.00"):
        reasons.append("loan has paid amount")
    if Payment.query.filter_by(loan_id=loan.id).count():
        reasons.append("loan has payment records")
    if any(money(entry.paid_amount or 0) != Decimal("0.00") or entry.status in {"PAID", "SETTLED"} for entry in loan.ledger_entries):
        reasons.append("loan has paid or settled ledger rows")
    if len(loan.ledger_entries) != 1:
        reasons.append("loan does not have exactly one defective ledger row")
    if loan.term_type and loan.repayment_frequency and loan.installment_count and loan.installment_count > 1:
        reasons.append("loan does not look like a missing-term defective loan")
    return not reasons, reasons


def repair_unpaid_defective_loan(loan_id: int, *, user_id=None, apply_changes: bool = False) -> dict:
    loan = Loan.query.get(loan_id)
    if not loan:
        raise LoanRepairError("Loan not found")
    safe, reasons = is_safe_to_repair_defective_loan(loan)
    if not safe:
        raise LoanRepairError("Reverse Disbursement and redisbursement required: " + "; ".join(reasons))

    application = LoanApplication.query.filter_by(customer_id=loan.customer_id, status="DISBURSED").order_by(LoanApplication.approved_at.desc(), LoanApplication.id.desc()).first()
    if not application:
        application = LoanApplication.query.filter_by(customer_id=loan.customer_id).order_by(LoanApplication.approved_at.desc(), LoanApplication.id.desc()).first()
    missing = [field for field in ["term_type", "term_value", "repayment_frequency", "interest_rate", "interest_rate_basis"] if not getattr(application, field, None)] if application else ["application"]
    if missing:
        raise LoanRepairError("Loan term information is incomplete: " + ", ".join(missing))

    resolved = resolve_loan_term(loan.start_date, application.term_type, application.term_value, application.repayment_frequency)
    total_interest, total_repayment, installment_amount = calculate_flat_term_amounts(loan.principal_amount, application.interest_rate, resolved.installment_count)
    old_total = money(loan.total_payable)

    for entry in list(loan.ledger_entries):
        db.session.delete(entry)
    db.session.flush()

    loan.term_type = application.term_type
    loan.term_value = application.term_value
    loan.loan_days = resolved.total_days if application.term_type == "DAYS" else None
    loan.tenure_months = application.term_value if application.term_type == "MONTHS" else None
    loan.repayment_frequency = application.repayment_frequency
    loan.interest_rate = application.interest_rate
    loan.interest_rate_basis = application.interest_rate_basis
    loan.interest_type = application.interest_type or "FLAT"
    loan.number_of_installments = resolved.installment_count
    loan.installment_count = resolved.installment_count
    loan.installment_amount = installment_amount
    loan.total_interest = total_interest
    loan.total_repayment = total_repayment
    loan.total_payable = total_repayment
    loan.total_days = resolved.total_days
    loan.maturity_date = resolved.maturity_date
    loan.end_date = resolved.maturity_date
    generate_loan_ledger(loan)
    log_audit("LOAN_TERM_LEDGER_REPAIR", "Loan", loan.id, user_id, {"old_total_payable": str(old_total), "new_total_payable": str(total_repayment), "application_id": application.id})

    summary = {"loan_id": loan.id, "old_total_payable": float(old_total), "new_total_payable": float(total_repayment), "installment_count": resolved.installment_count, "audit_logged": True, "correction_journal_required": old_total != total_repayment}
    if apply_changes:
        db.session.commit()
    else:
        db.session.rollback()
    return summary


def _set_missing(loan, field, value, changed):
    if getattr(loan, field, None) is None and value is not None:
        setattr(loan, field, value)
        changed.append(field)

def repair_loan_term_metadata_from_ledger(loan_id: int, *, user_id=None) -> dict:
    loan = Loan.query.get(loan_id)
    if not loan:
        raise LoanRepairError("Loan not found")
    entries = sorted(list(loan.ledger_entries), key=lambda e: (e.installment_no or 0, e.due_date))
    if not entries:
        raise LoanRepairError("Loan has no ledger rows to derive metadata from")

    before_totals = {
        "principal": str(sum((money(e.principal_amount) for e in entries), Decimal("0.00"))),
        "interest": str(sum((money(e.interest_amount) for e in entries), Decimal("0.00"))),
        "payable": str(sum((money(e.installment_amount) for e in entries), Decimal("0.00"))),
    }
    derived = derive_loan_metadata_from_ledger(loan)
    changed = []
    for field in ["term_type", "term_value", "loan_days", "repayment_frequency", "installment_count", "number_of_installments", "start_date", "maturity_date", "end_date", "final_installment_due_date"]:
        _set_missing(loan, field, derived.get(field), changed)
    if loan.total_days is None and derived.get("total_days") is not None:
        loan.total_days = derived["total_days"]
        changed.append("total_days")
    period_starts_backfilled = backfill_period_start_dates_from_schedule(loan)
    if period_starts_backfilled:
        changed.append("period_start_date")

    after_totals = {
        "principal": str(sum((money(e.principal_amount) for e in entries), Decimal("0.00"))),
        "interest": str(sum((money(e.interest_amount) for e in entries), Decimal("0.00"))),
        "payable": str(sum((money(e.installment_amount) for e in entries), Decimal("0.00"))),
    }
    if before_totals != after_totals:
        db.session.rollback()
        raise LoanRepairError("Repair attempted to change financial ledger totals")

    log_audit("LOAN_TERM_METADATA_REPAIR", "Loan", loan.id, user_id, {"changed_fields": changed, "ledger_rows": len(entries), "totals": after_totals})
    db.session.commit()
    return {"loan_id": loan.id, "changed_fields": changed, "ledger_rows": len(entries), "totals": after_totals, "audit_logged": True}


def repair_legacy_loan_configuration(loan: Loan, *, user_id=None) -> list[str]:
    """Fill configuration omitted by early disbursements without changing the schedule.

    The ledger is the historical repayment schedule, so it is the authoritative
    source for the term and installment count.  Monetary values are rebuilt from
    the approved/principal amount and contractual total, rather than regenerated,
    which keeps already-collected loans auditable.
    """
    entries = sorted(list(loan.ledger_entries), key=lambda e: (e.installment_no or 0, e.due_date))
    if not entries:
        return []
    derived = derive_loan_metadata_from_ledger(loan)
    changed = []
    for field in ("term_type", "term_value", "loan_days", "repayment_frequency",
                  "installment_count", "number_of_installments", "maturity_date",
                  "final_installment_due_date"):
        _set_missing(loan, field, derived.get(field), changed)

    count = loan.installment_count or loan.number_of_installments or len(entries)
    principal = money(loan.principal_amount)
    # A legacy loan can have a stale total_repayment, but total_payable is the
    # amount used by the payment history and must therefore win.
    payable = money(loan.total_payable or sum((money(e.installment_amount) for e in entries), Decimal()))
    if loan.total_repayment is None:
        loan.total_repayment = payable
        changed.append("total_repayment")
    if loan.total_interest is None:
        loan.total_interest = money(payable - principal)
        changed.append("total_interest")
    if loan.installment_amount is None and count:
        loan.installment_amount = money(payable / Decimal(count))
        changed.append("installment_amount")
    if loan.end_date is None and derived.get("end_date"):
        loan.end_date = derived["end_date"]
        changed.append("end_date")
    if changed:
        log_audit("LEGACY_LOAN_CONFIGURATION_REPAIRED", "Loan", loan.id, user_id,
                  {"changed_fields": changed, "total_payable": str(payable), "principal": str(principal)})
    return changed
