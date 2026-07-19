"""Canonical contractual settlement status for a loan.

Delay interest is a separate receivable.  It must never keep the principal
loan account ACTIVE after principal and contractual interest are cleared.
"""
from datetime import datetime
from decimal import Decimal

from .models import Loan


SETTLEMENT_TOLERANCE = Decimal("0.01")
PRESERVED_STATUSES = {"WRITTEN_OFF", "CANCELLED"}
AUTHORITATIVE_STATUS_FIELD = "status"


def _amount(value):
    return Decimal(str(value or "0"))


def serialize_loan_status(loan):
    """Serialize the persisted, authoritative Loan.status value.

    This deliberately does not infer a status from payments, total_payable, or
    a ledger row.  ``Loan.status`` is a VARCHAR column and is the one status
    used by every loan API surface.
    """
    value = getattr(loan, AUTHORITATIVE_STATUS_FIELD, None)
    value = getattr(value, "value", value)  # safe if a future enum is used
    return str(value or "").strip().upper()


def contractual_balances(loan):
    """Return canonical outstanding contractual and delay-interest balances."""
    entries = list(loan.ledger_entries)
    principal = sum((_amount(row.principal_amount) - _amount(row.principal_paid) for row in entries), Decimal())
    interest = sum((_amount(row.interest_amount) - _amount(row.interest_paid) - _amount(row.waived_interest_amount) for row in entries), Decimal())
    delay_interest = sum((_amount(row.delay_interest_accrued) - _amount(row.delay_interest_paid) - _amount(row.delay_interest_waived) for row in entries), Decimal())
    return {
        "principal_outstanding": max(principal, Decimal("0")),
        "contractual_interest_outstanding": max(interest, Decimal("0")),
        "delay_interest_outstanding": max(delay_interest, Decimal("0")),
    }


def update_loan_settlement_status(loan_id, settlement_date=None, user_id=None, loan=None):
    """Set the authoritative ``Loan.status`` from contractual balances.

    The caller owns the transaction.  Keeping this helper commit-free lets a
    reconciliation post its waivers, allocations, status, and metadata in one
    atomic transaction.
    """
    loan = loan or Loan.query.filter_by(id=loan_id).with_for_update().one()
    balances = contractual_balances(loan)
    current = (loan.status or "").strip().upper()
    settled = (balances["principal_outstanding"] <= SETTLEMENT_TOLERANCE
               and balances["contractual_interest_outstanding"] <= SETTLEMENT_TOLERANCE)
    if current not in PRESERVED_STATUSES:
        if settled:
            loan.status = "SETTLED"
            # Replays must not replace the original settlement audit data.
            if current != "SETTLED":
                loan.settled_date = settlement_date or loan.settled_date
                loan.settled_at = (datetime.combine(settlement_date, datetime.min.time())
                                   if settlement_date else datetime.utcnow())
                loan.settled_by_id = user_id
        else:
            loan.status = "ACTIVE"
    balances["is_contractually_settled"] = settled
    return loan, balances
