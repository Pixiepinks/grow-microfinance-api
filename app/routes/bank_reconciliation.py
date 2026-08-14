from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import get_jwt_identity
from sqlalchemy import func, or_

from ..extensions import db
from ..accounting import (effectively_posted_journal_filter, get_gl_lines_for_account,
                          is_effectively_posted_journal, money)
from ..models import (AccountingAccount, AccountingJournalEntry, AccountingJournalLine,
                      BankReconciliation, BankReconciliationAudit, BankReconciliationLine)
from .utils import role_required


bank_reconciliation_bp = Blueprint("bank_reconciliation", __name__, url_prefix="/admin/accounting")
# Retain the previously published admin routes while making the build/web route
# the canonical API surface. The unprefixed compatibility surface remains
# deliberately limited to create.
bank_reconciliation_legacy_bp = Blueprint("bank_reconciliation_legacy", __name__, url_prefix="/admin")
bank_reconciliation_compat_bp = Blueprint("bank_reconciliation_compat", __name__)
EDITABLE = {"DRAFT", "IN_PROGRESS", "REOPENED"}
ZERO = Decimal("0.00")


def _uid():
    identity = get_jwt_identity()
    return int(identity) if identity else None


def _error(message, field=None, code=None):
    body = {"success": False, "error": code or ("invalid_bank_account" if field == "bank_account_id" else "validation_error"),
            "error_code": "BANK_RECONCILIATION_VALIDATION", "message": message}
    if field:
        body["field"] = field
    return jsonify(body), 422


def _remove_error(error, message, status):
    """Return the stable JSON contract used by reconciliation unmatching."""
    return jsonify({"success": False, "error": error, "message": message}), status


def _decimal(value, field):
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{field} must be a valid amount")


def _date(value, field):
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be an ISO date")


def _bank(account_id):
    account = db.session.get(AccountingAccount, account_id)
    if not account:
        raise ValueError("bank_account_id must identify a BANK account")
    if not account.is_active:
        raise ValueError("bank_account_id must identify an active account")
    if not account.allow_manual_posting:
        raise ValueError("bank_account_id must identify an account that allows posting")
    if str(account.account_type).upper() != "ASSET" or str(account.account_subtype).upper() != "BANK":
        raise ValueError("bank_account_id must identify a BANK account")
    return account


def resolve_bank_account(value, *, code_hint=None):
    """Resolve API account references without confusing an account code for an ID."""
    if isinstance(value, dict):
        code_hint = value.get("code") or value.get("account_code") or code_hint
        value = value.get("id")
    raw = code_hint if code_hint not in (None, "") else value
    account = None
    if code_hint not in (None, "") or isinstance(raw, str):
        account = AccountingAccount.query.filter_by(account_code=str(raw).strip()).one_or_none()
    if account is None and code_hint in (None, ""):
        try:
            account = db.session.get(AccountingAccount, int(raw))
        except (TypeError, ValueError):
            account = None
    if account is None:
        raise ValueError("bank_account_id must identify a BANK account")
    return _bank(account.id)


def _audit(rec, action, line_id=None, reason=None):
    db.session.add(BankReconciliationAudit(bank_reconciliation_id=rec.id, action=action,
        user_id=_uid(), bank_account_id=rec.bank_account_id, journal_line_id=line_id,
        reconciliation_number=rec.reconciliation_number, reason=reason))


def _posted_query(account_id):
    return (AccountingJournalLine.query.join(AccountingJournalEntry)
            .filter(AccountingJournalLine.account_id == account_id,
                    effectively_posted_journal_filter()))


def _reconciliation_eligibility(line, rec=None):
    entry = line.journal_entry
    if not is_effectively_posted_journal(entry):
        return False, "Journal entry is not posted."
    if rec and line.account_id != rec.bank_account_id:
        return False, "Journal line does not belong to the reconciliation bank account."
    if rec and not rec.statement_date_from <= entry.journal_date <= rec.statement_date_to:
        return False, "Journal line is outside the reconciliation period."
    if rec and line.bank_reconciliation_id not in (None, rec.id):
        return False, "Journal line is already reconciled in another reconciliation."
    return True, None


def _line_status_fields(line, rec=None):
    posted = is_effectively_posted_journal(line.journal_entry)
    eligible, reason = _reconciliation_eligibility(line, rec)
    return {"journal_status": line.journal_entry.status, "journal_line_status": None,
            "posted_at": (line.journal_entry.posted_at.isoformat()
                          if line.journal_entry.posted_at else None),
            "is_posted": posted, "is_reconcilable": eligible,
            "reconciliation_block_reason": reason}


def _refresh(rec):
    ledger = get_gl_lines_for_account(account_id=rec.bank_account_id,
                                      date_from=rec.statement_date_from,
                                      date_to=rec.statement_date_to)
    rec.gl_opening_balance = ledger["opening_balance"]
    rec.gl_closing_balance = ledger["closing_balance"]
    selected = [allocation.journal_line for allocation in rec.allocations]
    rec.total_reconciled_debits = sum((Decimal(l.debit) for l in selected), ZERO)
    rec.total_reconciled_credits = sum((Decimal(l.credit) for l in selected), ZERO)
    selected_ids = {line.id for line in selected}
    unmatched = [line for line in ledger["lines"] if line.id not in selected_ids and not line.is_reconciled]
    rec.total_unreconciled_debits = sum((Decimal(l.debit) for l in unmatched), ZERO)
    rec.total_unreconciled_credits = sum((Decimal(l.credit) for l in unmatched), ZERO)
    return len(unmatched)


def _serialize(rec):
    count = _refresh(rec)
    account = rec.bank_account
    difference = money(Decimal(rec.statement_closing_balance) - Decimal(rec.gl_closing_balance))
    return {"id": rec.id, "reconciliation_number": rec.reconciliation_number,
            "bank_account_id": rec.bank_account_id, "status": rec.status,
            "bank_account_code": account.account_code if account else None,
            "bank_account_name": account.account_name if account else None,
            "bank_account": ({"id": account.id, "code": account.account_code,
                              "name": account.account_name} if account else None),
            "statement_date_from": rec.statement_date_from.isoformat(),
            "statement_date_to": rec.statement_date_to.isoformat(),
            "statement_opening_balance": f"{rec.statement_opening_balance:.2f}",
            "statement_closing_balance": f"{rec.statement_closing_balance:.2f}",
            "gl_opening_balance": f"{rec.gl_opening_balance:.2f}",
            "gl_closing_balance": f"{rec.gl_closing_balance:.2f}",
            "total_reconciled_debits": f"{rec.total_reconciled_debits:.2f}",
            "total_reconciled_credits": f"{rec.total_reconciled_credits:.2f}",
            "total_unreconciled_debits": f"{rec.total_unreconciled_debits:.2f}",
            "total_unreconciled_credits": f"{rec.total_unreconciled_credits:.2f}",
            "reconciled_debits": f"{rec.total_reconciled_debits:.2f}",
            "reconciled_credits": f"{rec.total_reconciled_credits:.2f}",
            "unreconciled_debits": f"{rec.total_unreconciled_debits:.2f}",
            "unreconciled_credits": f"{rec.total_unreconciled_credits:.2f}",
            "difference": f"{difference:.2f}",
            "unreconciled_count": count, "matched_transaction_count": len(rec.allocations),
            "notes": rec.notes,
            "completed_at": rec.completed_at.isoformat() if rec.completed_at else None,
            "completed_by_id": rec.approved_by_id}


def _transaction_rows(rec):
    """Return the reconciliation-period bank lines using the public API shape."""
    lines = (_posted_query(rec.bank_account_id)
             .filter(AccountingJournalEntry.journal_date >= rec.statement_date_from,
                     AccountingJournalEntry.journal_date <= rec.statement_date_to)
             .order_by(AccountingJournalEntry.journal_date,
                       AccountingJournalEntry.journal_no,
                       AccountingJournalLine.line_no).all())
    return [{"journal_line_id": line.id, "journal_entry_id": line.journal_entry.id,
             "journal_number": line.journal_entry.journal_no,
             "posting_date": line.journal_entry.journal_date.isoformat(),
             "description": line.description or line.journal_entry.description,
             "reference": line.journal_entry.reference,
             "debit": f"{money(line.debit):.2f}", "credit": f"{money(line.credit):.2f}",
             "is_reconciled": bool(line.is_reconciled),
             "reconciliation_number": (line.bank_reconciliation.reconciliation_number
                                       if line.bank_reconciliation else None),
             "reconciled_date": (line.reconciled_date.isoformat()
                                 if line.reconciled_date else None),
             "statement_reference": line.bank_statement_reference}
            for line in lines]


@bank_reconciliation_bp.route("/bank-reconciliations", methods=["POST"], strict_slashes=False)
@bank_reconciliation_legacy_bp.route("/bank-reconciliations", methods=["POST"], strict_slashes=False)
@bank_reconciliation_compat_bp.route("/bank-reconciliations", methods=["POST"], strict_slashes=False)
@role_required(["admin"])
def create_reconciliation():
    data = request.get_json() or {}
    try:
        account = resolve_bank_account(data.get("bank_account_id"),
                                       code_hint=data.get("bank_account_code"))
        start, end = _date(data.get("statement_date_from"), "statement_date_from"), _date(data.get("statement_date_to"), "statement_date_to")
        if end < start:
            raise ValueError("statement_date_to cannot precede statement_date_from")
        opening = _decimal(data.get("statement_opening_balance"), "statement_opening_balance")
        closing = _decimal(data.get("statement_closing_balance"), "statement_closing_balance")
    except (ValueError, TypeError) as exc:
        message = ("A valid accounting bank account is required."
                   if "bank_account" in str(exc) else str(exc))
        return _error(message, "bank_account_id" if "bank_account" in str(exc) else None)
    ledger = get_gl_lines_for_account(account_id=account.id, date_from=start, date_to=end)
    rec = BankReconciliation(reconciliation_number=f"PENDING-{uuid4().hex}", bank_account_id=account.id,
        statement_date_from=start, statement_date_to=end, statement_opening_balance=opening,
        statement_closing_balance=closing, gl_opening_balance=ledger["opening_balance"],
        gl_closing_balance=ledger["closing_balance"], notes=data.get("notes"), created_by_id=_uid())
    db.session.add(rec); db.session.flush()
    # The database-generated id makes number allocation atomic across workers.
    rec.reconciliation_number = f"BR-{end:%Y%m%d}-{rec.id:04d}"
    _audit(rec, "CREATED"); db.session.commit()
    response = _serialize(rec)
    response["success"] = True
    return jsonify(response), 201


@bank_reconciliation_bp.route("/bank-reconciliations", methods=["GET"])
@bank_reconciliation_legacy_bp.route("/bank-reconciliations", methods=["GET"])
@role_required(["admin"])
def list_reconciliations():
    records = BankReconciliation.query.order_by(BankReconciliation.id.desc()).all()
    return jsonify({"items": [_serialize(record) for record in records], "count": len(records)})


@bank_reconciliation_bp.route("/bank-reconciliations/<int:rec_id>", methods=["GET"])
@bank_reconciliation_legacy_bp.route("/bank-reconciliations/<int:rec_id>", methods=["GET"])
@role_required(["admin"])
def get_reconciliation(rec_id):
    return jsonify(_serialize(BankReconciliation.query.get_or_404(rec_id)))


@bank_reconciliation_bp.route("/bank-reconciliation/transactions", methods=["GET"])
@bank_reconciliation_legacy_bp.route("/bank-reconciliation/transactions", methods=["GET"])
@role_required(["admin"])
def bank_transactions():
    try:
        account = _bank(int(request.args.get("bank_account_id")))
    except (ValueError, TypeError) as exc:
        return _error(str(exc))
    q = _posted_query(account.id)
    try:
        if request.args.get("date_from"): q = q.filter(AccountingJournalEntry.journal_date >= _date(request.args["date_from"], "date_from"))
        if request.args.get("date_to"): q = q.filter(AccountingJournalEntry.journal_date <= _date(request.args["date_to"], "date_to"))
        if request.args.get("amount"): q = q.filter(or_(AccountingJournalLine.debit == _decimal(request.args["amount"], "amount"), AccountingJournalLine.credit == _decimal(request.args["amount"], "amount")))
    except ValueError as exc: return _error(str(exc))
    status = request.args.get("reconciliation_status", "").upper()
    if status in {"RECONCILED", "TRUE"}: q = q.filter(AccountingJournalLine.is_reconciled.is_(True))
    if status in {"UNRECONCILED", "FALSE"}: q = q.filter(AccountingJournalLine.is_reconciled.is_(False))
    if request.args.get("reference"):
        pattern = f"%{request.args['reference']}%"; q = q.filter(or_(AccountingJournalEntry.reference.ilike(pattern), AccountingJournalLine.bank_statement_reference.ilike(pattern)))
    if request.args.get("description"):
        pattern = f"%{request.args['description']}%"; q = q.filter(or_(AccountingJournalEntry.description.ilike(pattern), AccountingJournalLine.description.ilike(pattern)))
    running = ZERO
    if request.args.get("date_from"):
        opening_date = _date(request.args["date_from"], "date_from")
        running = _posted_query(account.id).filter(AccountingJournalEntry.journal_date < opening_date).with_entities(
            func.coalesce(func.sum(AccountingJournalLine.debit - AccountingJournalLine.credit), 0)).scalar()
        running = Decimal(running)
    rows, items = q.order_by(AccountingJournalEntry.journal_date, AccountingJournalEntry.journal_no, AccountingJournalLine.line_no).all(), []
    for line in rows:
        entry = line.journal_entry; running += Decimal(line.debit) - Decimal(line.credit)
        items.append({"journal_line_id": line.id, "journal_entry_id": entry.id, "journal_number": entry.journal_no,
            "posting_date": entry.journal_date.isoformat(), "description": line.description or entry.description,
            "reference": entry.reference, "debit": f"{line.debit:.2f}", "credit": f"{line.credit:.2f}",
            "running_balance": f"{running:.2f}", "is_reconciled": line.is_reconciled,
            "reconciled_date": line.reconciled_date.isoformat() if line.reconciled_date else None,
            "reconciliation_number": line.bank_reconciliation.reconciliation_number if line.bank_reconciliation else None,
            "statement_reference": line.bank_statement_reference})
    return jsonify({"transactions": items, "count": len(items)})


@bank_reconciliation_bp.route("/bank-reconciliations/<int:rec_id>/transactions", methods=["GET"])
@bank_reconciliation_legacy_bp.route("/bank-reconciliations/<int:rec_id>/transactions", methods=["GET"])
@role_required(["admin"])
def reconciliation_transactions(rec_id):
    """Return GL-authoritative, selectable journal lines for a reconciliation."""
    rec = BankReconciliation.query.get_or_404(rec_id)
    account = _bank(rec.bank_account_id)
    period_lines = (AccountingJournalLine.query.join(AccountingJournalEntry)
                     .filter(AccountingJournalLine.account_id == account.id,
                             AccountingJournalEntry.journal_date >= rec.statement_date_from,
                             AccountingJournalEntry.journal_date <= rec.statement_date_to)
                     .order_by(AccountingJournalEntry.journal_date,
                               AccountingJournalEntry.journal_no,
                               AccountingJournalLine.line_no).all())
    before_status = len(period_lines)
    ledger = get_gl_lines_for_account(account_id=account.id,
                                      date_from=rec.statement_date_from,
                                      date_to=rec.statement_date_to)
    visible = []
    for line in period_lines:
        other_completed = (line.bank_reconciliation_id not in (None, rec.id)
                           and line.bank_reconciliation
                           and line.bank_reconciliation.status == "COMPLETED")
        if not other_completed:
            visible.append(line)

    posted_visible = [line for line in visible if is_effectively_posted_journal(line.journal_entry)]
    reconciled = [line for line in posted_visible if line.bank_reconciliation_id == rec.id]
    unreconciled = [line for line in posted_visible if line.bank_reconciliation_id != rec.id]
    reconciled_debits = sum((money(line.debit) for line in reconciled), ZERO)
    reconciled_credits = sum((money(line.credit) for line in reconciled), ZERO)
    unreconciled_debits = sum((money(line.debit) for line in unreconciled), ZERO)
    unreconciled_credits = sum((money(line.credit) for line in unreconciled), ZERO)
    gl_balance = money(ledger["closing_balance"])
    difference = money(Decimal(rec.statement_closing_balance) - gl_balance)
    current_app.logger.info("bank_reconciliation_transactions", extra={
        "reconciliation_id": rec.id, "reconciliation_number": rec.reconciliation_number,
        "stored_bank_account_id": rec.bank_account_id, "resolved_account_id": account.id,
        "resolved_account_code": account.account_code,
        "date_from": rec.statement_date_from.isoformat(), "date_to": rec.statement_date_to.isoformat(),
        "journal_line_count_before_status_filter": before_status,
        "journal_line_count_after_status_filter": len(ledger["lines"]),
        "eligible_reconciliation_line_count": sum(_reconciliation_eligibility(line, rec)[0]
                                                   for line in visible)})

    transactions = []
    running_by_id = {line.id: line.gl_running_balance for line in ledger["lines"]}
    for line in visible:
        entry = line.journal_entry
        transactions.append({
            "journal_line_id": line.id, "journal_entry_id": entry.id,
            "journal_number": entry.journal_no, "posting_date": entry.journal_date.isoformat(),
            "description": line.description or entry.description, "reference": entry.reference,
            "debit": f"{money(line.debit):.2f}", "credit": f"{money(line.credit):.2f}",
            "running_balance": (f"{running_by_id[line.id]:.2f}"
                                if line.id in running_by_id else None),
            "is_reconciled": line.bank_reconciliation_id == rec.id,
            "reconciliation_number": (line.bank_reconciliation.reconciliation_number
                                      if line.bank_reconciliation else None),
            **_line_status_fields(line, rec)})
    return jsonify({
        "reconciliation": {"id": rec.id, "reconciliation_number": rec.reconciliation_number,
            "bank_account_id": account.id, "bank_account_code": account.account_code,
            "bank_account_name": account.account_name,
            "statement_date_from": rec.statement_date_from.isoformat(),
            "statement_date_to": rec.statement_date_to.isoformat()},
        "summary": {"gl_opening_balance": f"{ledger['opening_balance']:.2f}",
            "gl_closing_balance": f"{gl_balance:.2f}", "gl_balance": f"{gl_balance:.2f}",
            "reconciled_debits": f"{reconciled_debits:.2f}",
            "reconciled_credits": f"{reconciled_credits:.2f}",
            "unreconciled_debits": f"{unreconciled_debits:.2f}",
            "unreconciled_credits": f"{unreconciled_credits:.2f}",
            "difference": f"{difference:.2f}"},
        "transactions": transactions})


@bank_reconciliation_bp.route("/bank-reconciliations/<int:rec_id>/lines", methods=["POST"])
@bank_reconciliation_legacy_bp.route("/bank-reconciliations/<int:rec_id>/lines", methods=["POST"])
@role_required(["admin"])
def add_line(rec_id):
    rec = BankReconciliation.query.with_for_update().get_or_404(rec_id)
    if rec.status not in EDITABLE: return _error("Completed or cancelled reconciliations are not editable")
    data = request.get_json() or {}
    ids = data.get("journal_line_ids")
    if ids is None: ids = [data.get("journal_line_id")]
    if not isinstance(ids, list) or not ids or any(not isinstance(value, int) for value in ids):
        return _error("journal_line_ids must be a non-empty list of journal-line IDs")
    try:
        reconciled_date = _date(data.get("reconciled_date") or rec.statement_date_to,
                                "reconciled_date")
    except ValueError as exc:
        db.session.rollback()
        return _error(str(exc))
    if not rec.statement_date_from <= reconciled_date <= rec.statement_date_to:
        db.session.rollback()
        return _error("reconciled_date must fall within the reconciliation period")
    reference = data.get("bank_statement_reference") or data.get("statement_reference")
    lines = AccountingJournalLine.query.filter(AccountingJournalLine.id.in_(set(ids))).with_for_update().all()
    if len(lines) != len(set(ids)):
        db.session.rollback()
        return _error("Every journal_line_id must exist")
    invalid_posted = []
    for line in lines:
        if not is_effectively_posted_journal(line.journal_entry):
            invalid_posted.append({"journal_line_id": line.id,
                "journal_number": line.journal_entry.journal_no,
                "description": line.description or line.journal_entry.description,
                "journal_status": line.journal_entry.status,
                "reason": "Only posted journal lines can be reconciled."})
    if invalid_posted:
        db.session.rollback()
        return jsonify({"error": "unposted_journal_lines",
            "message": "One or more selected journal lines are not posted.",
            "invalid_lines": invalid_posted}), 422
    for line in lines:
        if (line.account_id != rec.bank_account_id or str(line.account.account_subtype).upper() != "BANK"
                or not rec.statement_date_from <= line.journal_entry.journal_date <= rec.statement_date_to):
            db.session.rollback()
            return _error("Journal line must belong to the bank account and reconciliation period")
        if line.bank_reconciliation_id not in (None, rec.id):
            db.session.rollback()
            return _error("Journal line is already reconciled in another reconciliation")
    for line in lines:
        allocation = BankReconciliationLine.query.filter_by(bank_reconciliation_id=rec.id, journal_line_id=line.id).one_or_none()
        if not allocation:
            db.session.add(BankReconciliationLine(bank_reconciliation_id=rec.id, journal_line_id=line.id,
                debit=line.debit, credit=line.credit, statement_reference=reference,
                reconciled_date=reconciled_date, created_by_id=_uid()))
        line.is_reconciled = True; line.bank_reconciliation_id = rec.id
        line.reconciled_date = reconciled_date; line.reconciled_at = datetime.utcnow(); line.reconciled_by_id = _uid()
        line.bank_statement_reference = reference
        if not allocation:
            _audit(rec, "LINE_ADDED", line.id)
    rec.status = "IN_PROGRESS"; db.session.flush(); _refresh(rec); db.session.commit()
    result = _serialize(rec)
    result.update(success=True, matched_count=len(rec.allocations),
                  matched_transaction_count=len(rec.allocations),
                  transactions=_transaction_rows(rec))
    return jsonify(result)


def _remove_reconciliation_match(rec_id, line_id):
    """Undo add_line's reconciliation metadata without changing accounting data."""
    rec = BankReconciliation.query.filter_by(id=rec_id).with_for_update().one_or_none()
    if not rec:
        return _remove_error("reconciliation_not_found", "Bank reconciliation not found.", 404)
    if rec.status not in EDITABLE:
        return _remove_error(
            "reconciliation_locked", "Completed reconciliations cannot be modified.", 409)

    line = db.session.get(AccountingJournalLine, line_id)
    if not line:
        return _remove_error("match_not_found", "Transaction match not found.", 404)
    if line.bank_reconciliation_id not in (None, rec.id):
        return _remove_error(
            "match_conflict", "Journal line belongs to another reconciliation.", 409)
    allocation = BankReconciliationLine.query.filter_by(
        bank_reconciliation_id=rec.id, journal_line_id=line.id).one_or_none()
    if line.bank_reconciliation_id != rec.id or not allocation:
        return _remove_error("match_not_found", "Transaction match not found.", 404)

    line.is_reconciled = False; line.bank_reconciliation_id = None; line.reconciled_date = None
    line.reconciled_at = None; line.reconciled_by_id = None; line.bank_statement_reference = None; line.reconciliation_note = None
    db.session.delete(allocation)
    _audit(rec, "LINE_REMOVED", line.id, request.args.get("reason")); _refresh(rec); db.session.commit()
    summary = _serialize(rec)
    response = dict(summary)
    response.update(success=True, message="Transaction match removed.",
                    reconciliation_id=rec.id, journal_line_id=line.id,
                    summary={
                        "matched_transaction_count": summary["matched_transaction_count"],
                        "statement_closing_balance": summary["statement_closing_balance"],
                        "gl_balance": summary["gl_closing_balance"],
                        "reconciled_debits": summary["reconciled_debits"],
                        "reconciled_credits": summary["reconciled_credits"],
                        "unreconciled_debits": summary["unreconciled_debits"],
                        "unreconciled_credits": summary["unreconciled_credits"],
                        "difference": summary["difference"],
                    }, transactions=_transaction_rows(rec))
    return jsonify(response)


@bank_reconciliation_bp.route("/bank-reconciliations/<int:rec_id>/lines/<int:line_id>", methods=["DELETE"])
@bank_reconciliation_legacy_bp.route("/bank-reconciliations/<int:rec_id>/lines/<int:line_id>", methods=["DELETE"])
@role_required(["admin"])
def remove_line(rec_id, line_id):
    return _remove_reconciliation_match(rec_id, line_id)


@bank_reconciliation_bp.route("/bank-reconciliations/<int:rec_id>/matches", methods=["DELETE"])
@bank_reconciliation_legacy_bp.route("/bank-reconciliations/<int:rec_id>/matches", methods=["DELETE"])
@role_required(["admin"])
def remove_match(rec_id):
    """Compatibility route used by the deployed Bank Reconciliation screen."""
    data = request.get_json(silent=True) or {}
    line_id = data.get("journal_line_id")
    if not isinstance(line_id, int):
        return _remove_error(
            "invalid_journal_line_id", "journal_line_id must be a journal-line ID.", 422)
    return _remove_reconciliation_match(rec_id, line_id)


@bank_reconciliation_bp.route("/bank-reconciliations/<int:rec_id>/matches/<int:line_id>", methods=["DELETE"])
@bank_reconciliation_legacy_bp.route("/bank-reconciliations/<int:rec_id>/matches/<int:line_id>", methods=["DELETE"])
@role_required(["admin"])
def remove_match_by_line(rec_id, line_id):
    """Path-parameter alias for clients which identify the matched journal line in the URL."""
    return _remove_reconciliation_match(rec_id, line_id)


@bank_reconciliation_bp.route("/bank-reconciliations/<int:rec_id>/complete", methods=["POST"])
@bank_reconciliation_legacy_bp.route("/bank-reconciliations/<int:rec_id>/complete", methods=["POST"])
@role_required(["admin"])
def complete(rec_id):
    rec = BankReconciliation.query.with_for_update().get_or_404(rec_id)
    if rec.status not in {"DRAFT", "IN_PROGRESS"}:
        return _error("Reconciliation is not editable")
    data = request.get_json(silent=True) or {}
    # Support the frontend's one-shot completion payload while retaining the
    # persisted-lines workflow. The same validation and transaction are used.
    if "journal_line_ids" in data:
        supplied = data["journal_line_ids"]
        persisted = {allocation.journal_line_id for allocation in rec.allocations}
        if not isinstance(supplied, list) or set(supplied) != persisted:
            db.session.rollback()
            return _error("All journal_line_ids must be persisted with the lines endpoint before completion")
    _refresh(rec)
    eligible_count = len(get_gl_lines_for_account(account_id=rec.bank_account_id,
        date_from=rec.statement_date_from, date_to=rec.statement_date_to)["lines"])
    if eligible_count and not rec.allocations:
        db.session.rollback()
        return jsonify({
            "error": "no_transactions_matched",
            "message": "Mark bank transactions as reconciled before completing the reconciliation.",
        }), 422
    if rec.allocations and rec.total_reconciled_debits == ZERO and rec.total_reconciled_credits == ZERO:
        db.session.rollback(); return _error("Selected transactions must have a non-zero value")
    # Adjust the GL balance for unmatched in-period deposits/payments; completion
    # is therefore based on the matched population, not merely the full GL total.
    adjusted_gl = (Decimal(rec.gl_closing_balance) - Decimal(rec.total_unreconciled_debits)
                   + Decimal(rec.total_unreconciled_credits))
    difference = Decimal(rec.statement_closing_balance) - adjusted_gl
    if abs(difference) > Decimal("0.005"):
        db.session.rollback()
        return _error(f"Reconciliation difference must be 0.00 (current difference {difference:.2f})")
    rec.status = "COMPLETED"; rec.completed_at = datetime.utcnow(); rec.approved_by_id = _uid()
    _audit(rec, "COMPLETED", reason=data.get("reason")); db.session.commit()
    result = _serialize(rec); result["difference"] = f"{difference:.2f}"; return jsonify(result)


@bank_reconciliation_bp.route("/bank-reconciliations/<int:rec_id>/reopen", methods=["POST"])
@bank_reconciliation_legacy_bp.route("/bank-reconciliations/<int:rec_id>/reopen", methods=["POST"])
@role_required(["admin"])
def reopen(rec_id):
    rec = BankReconciliation.query.with_for_update().get_or_404(rec_id); data = request.get_json() or {}; reason = str(data.get("reason") or "").strip()
    if rec.status != "COMPLETED": return _error("Only a completed reconciliation can be reopened")
    if not reason: return _error("A reopen reason is required", "reason")
    line_ids = [line.id for line in rec.lines]
    for line in rec.lines:
        line.is_reconciled = False; line.bank_reconciliation_id = None; line.reconciled_date = None; line.reconciled_at = None; line.reconciled_by_id = None
    BankReconciliationLine.query.filter_by(bank_reconciliation_id=rec.id).delete()
    rec.status = "REOPENED"; rec.completed_at = None; _audit(rec, "REOPENED", reason=reason)
    for line_id in line_ids: _audit(rec, "LINE_UNRECONCILED", line_id, reason)
    _refresh(rec); db.session.commit(); return jsonify(_serialize(rec))


@bank_reconciliation_bp.route("/bank-reconciliations/<int:rec_id>/cancel", methods=["POST"])
@bank_reconciliation_legacy_bp.route("/bank-reconciliations/<int:rec_id>/cancel", methods=["POST"])
@role_required(["admin"])
def cancel(rec_id):
    rec = BankReconciliation.query.with_for_update().get_or_404(rec_id); data = request.get_json() or {}; reason = str(data.get("reason") or "").strip()
    if rec.status not in EDITABLE: return _error("Reconciliation cannot be cancelled")
    if not reason: return _error("A cancellation reason is required", "reason")
    for line in list(rec.lines):
        line.is_reconciled = False; line.bank_reconciliation_id = None; line.reconciled_date = None; line.reconciled_at = None; line.reconciled_by_id = None
    BankReconciliationLine.query.filter_by(bank_reconciliation_id=rec.id).delete()
    rec.status = "CANCELLED"; _audit(rec, "CANCELLED", reason=reason); _refresh(rec); db.session.commit()
    return jsonify(_serialize(rec))
