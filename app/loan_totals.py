"""Canonical loan receipt and settlement totals.

Receipts and settlement concessions are intentionally kept separate here.  In
particular, a ledger row being satisfied does not make its waived portion cash.
"""
from decimal import Decimal, ROUND_HALF_UP


CENT = Decimal("0.01")
INVALID_RECEIPT_STATUSES = {"REVERSED", "CANCELLED", "FAILED", "DRAFT"}


def money(value):
    return Decimal(str(value or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


def is_valid_posted_receipt(payment):
    """Whether a payment is a posted customer receipt eligible for cash paid."""
    return (
        not payment.reversed_at
        and str(payment.status or "").strip().upper() == "POSTED"
        and str(payment.status or "").strip().upper() not in INVALID_RECEIPT_STATUSES
    )


def loan_totals(loan):
    entries = list(loan.ledger_entries)
    receipts = [payment for payment in loan.payments if is_valid_posted_receipt(payment)]
    cash_paid = money(sum((money(payment.amount_collected) for payment in receipts), Decimal()))
    principal_paid = money(sum((money(entry.principal_paid) for entry in entries), Decimal()))
    normal_interest_paid = money(sum((money(entry.interest_paid) for entry in entries), Decimal()))
    delay_interest_paid = money(sum((money(entry.delay_interest_paid) for entry in entries), Decimal()))
    penalty_paid = money(sum((money(payment.penalty_paid) for payment in receipts), Decimal()))
    fees_paid = money(sum((money(payment.other_fee_paid) for payment in receipts), Decimal()))
    # Loan-level approved totals are authoritative when present.  Ledger waiver
    # columns provide the equivalent detail for historical settlements and must
    # not be added again when both representations exist.
    interest_waived = money(getattr(loan, "interest_rebate_amount", 0)) or money(sum((money(entry.waived_interest_amount) for entry in entries), Decimal()))
    delay_interest_waived = money(getattr(loan, "delay_interest_waiver_amount", 0)) or money(sum((money(getattr(entry, "waived_delay_interest_amount", 0)) for entry in entries), Decimal()))
    penalty_waived = money(getattr(loan, "penalty_waiver_amount", 0)) or money(sum((money(entry.waived_penalty_amount) for entry in entries), Decimal()))
    settlement_adjustments = money(interest_waived + delay_interest_waived + penalty_waived)
    gross_satisfied_amount = money(cash_paid + settlement_adjustments)
    # Settlement concessions close receivables without becoming cash receipts.
    outstanding_amount = max(Decimal("0.00"), money(loan.total_payable) - gross_satisfied_amount)
    return {
        "cash_paid": cash_paid, "total_paid": cash_paid,
        "principal_paid": principal_paid, "normal_interest_paid": normal_interest_paid,
        "delay_interest_paid": delay_interest_paid, "penalty_paid": penalty_paid,
        "fees_paid": fees_paid, "interest_waived": interest_waived,
        "delay_interest_waived": delay_interest_waived, "penalty_waived": penalty_waived,
        "settlement_adjustments": settlement_adjustments,
        "gross_satisfied_amount": gross_satisfied_amount,
        "outstanding_amount": money(outstanding_amount),
    }
