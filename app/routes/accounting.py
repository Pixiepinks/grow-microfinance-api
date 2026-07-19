from datetime import date
import re
import time
from flask import Blueprint, Response, jsonify, request, current_app
from flask_jwt_extended import get_jwt_identity
from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload, selectinload

from ..extensions import db
from ..models import AccountingAccount, AccountingJournalEntry, AccountingJournalLine, Customer, Loan
from ..accounting import (
    AccountingError,
    create_account,
    create_draft_journal,
    general_ledger,
    ledger_csv,
    post_journal,
    reconciliation_issues,
    reconciliation_summary,
    reverse_journal,
    seed_default_accounts,
    serialize_journal,
    update_account,
    accounting_settings_payload,
    update_accounting_settings,
    account_subtype,
    validate_funding_account, is_funding_account,
    resolve_system_account,
    trial_balance_report,
    income_statement_report,
    statement_of_financial_position_report,
    reports_summary,
    report_csv,
    seed_default_report_classifications,
    serialize_account,
    ValidationError,
)
from ..investor_funding import (
    load_investor_funding_settings,
    serialize_investor_funding_settings,
    update_investor_funding_settings,
)
from .utils import role_required

accounting_bp = Blueprint("accounting", __name__, url_prefix="/admin/accounting")

def _uid():
    ident = get_jwt_identity()
    return int(ident) if ident else None

def _error(exc):
    db.session.rollback()
    current_app.logger.exception("Accounting API error")
    if isinstance(exc, ValidationError):
        return jsonify(exc.payload), 422
    return jsonify({"success": False, "message": str(exc), "error_code": "ACCOUNTING_ERROR"}), 400

@accounting_bp.before_request
def _log_accounting_request_start():
    request._accounting_started = time.monotonic()
    current_app.logger.info("START request method=%s path=%s", request.method, request.path)


@accounting_bp.after_request
def _log_accounting_request_end(response):
    started = getattr(request, "_accounting_started", None)
    elapsed_ms = round((time.monotonic() - started) * 1000, 2) if started else None
    current_app.logger.info("END request method=%s path=%s status=%s elapsed_ms=%s", request.method, request.path, response.status_code, elapsed_ms)
    return response

def _account_option(account):
    return serialize_account(account)


@accounting_bp.route("/accounts", methods=["GET"])
@role_required(["admin"])
def list_accounts():
    current_app.logger.info("Accounting accounts milestone=loading active accounts")
    q = AccountingAccount.query
    if request.args.get("account_type"): q=q.filter_by(account_type=request.args["account_type"])
    if request.args.get("active") is not None: q=q.filter(AccountingAccount.is_active.is_(request.args.get("active").lower()=="true"))
    if request.args.get("posting_allowed") is not None: q=q.filter(AccountingAccount.allow_manual_posting.is_(request.args.get("posting_allowed").lower()=="true"))
    if request.args.get("parent_id"): q=q.filter_by(parent_id=request.args.get("parent_id"))
    if request.args.get("search"):
        s=f"%{request.args['search']}%"; q=q.filter((AccountingAccount.account_code.ilike(s)) | (AccountingAccount.account_name.ilike(s)))
    current_app.logger.info("Accounting accounts milestone=serializing response")
    accounts = q.order_by(AccountingAccount.account_code).limit(500).all()
    response = [_account_option(a) for a in accounts]
    current_app.logger.info("Accounting accounts milestone=completed count=%s", len(response))
    return jsonify(response)

@accounting_bp.route("/accounts", methods=["POST"])
@role_required(["admin"])
def add_account():
    try:
        acct=create_account(request.get_json() or {}, _uid()); db.session.commit()
        return jsonify({"id":acct.id,"account_code":acct.account_code}), 201
    except Exception as exc: return _error(exc)

@accounting_bp.route("/accounts/<int:account_id>", methods=["GET"])
@role_required(["admin"])
def get_account(account_id):
    return jsonify(serialize_account(AccountingAccount.query.get_or_404(account_id)))

@accounting_bp.route("/accounts/<int:account_id>", methods=["PUT", "PATCH"])
@role_required(["admin"])
def edit_account(account_id):
    acct=AccountingAccount.query.get_or_404(account_id)
    try:
        update_account(acct, request.get_json() or {}, _uid()); db.session.commit()
        return jsonify(serialize_account(acct))
    except Exception as exc: return _error(exc)

@accounting_bp.route("/accounts/<int:account_id>", methods=["DELETE"])
@role_required(["admin"])
def delete_account(account_id):
    acct=AccountingAccount.query.get_or_404(account_id)
    try:
        update_account(acct, {"_delete": True}, _uid())
        db.session.delete(acct); db.session.commit()
        return jsonify({"message":"Account deleted"})
    except Exception as exc: return _error(exc)

@accounting_bp.route("/settings", methods=["GET"])
@role_required(["admin"])
def get_settings():
    current_app.logger.info("Accounting settings milestone=loading accounting settings")
    settings = accounting_settings_payload()
    current_app.logger.info("Accounting settings milestone=completed configured=%s", settings.get("configured"))
    return jsonify({"settings": settings})

@accounting_bp.route("/settings/investor-funding", methods=["GET"], strict_slashes=False)
@role_required(["admin"])
def get_investor_funding_settings():
    settings = load_investor_funding_settings()
    return jsonify(serialize_investor_funding_settings(settings)), 200

@accounting_bp.route("/settings/investor-funding", methods=["PATCH"], strict_slashes=False)
@role_required(["admin"])
def patch_investor_funding_settings():
    try:
        settings = update_investor_funding_settings(request.get_json() or {}, _uid())
        db.session.commit()
        return jsonify(settings), 200
    except Exception as exc:
        return _error(exc)

@accounting_bp.route("/settings", methods=["PUT"])
@role_required(["admin"])
def put_settings():
    try:
        settings=update_accounting_settings(request.get_json() or {}, _uid()); db.session.commit()
        return jsonify({"settings": settings})
    except AccountingError as exc:
        db.session.rollback()
        details = exc.args[0] if exc.args and isinstance(exc.args[0], dict) else {"message": str(exc)}
        return jsonify({"message":"Validation failed", "errors": details}), 400
    except Exception as exc: return _error(exc)

@accounting_bp.route("/funding-accounts", methods=["GET"])
@role_required(["admin"])
def funding_accounts():
    method=(request.args.get("method") or "OTHER").upper()
    subtypes = ["CASH"] if method == "CASH" else ["BANK"] if method in ("BANK_TRANSFER", "CHEQUE") else ["CASH", "BANK"]
    default_ids=set()
    for key in ["DEFAULT_DISBURSEMENT_ACCOUNT", "DEFAULT_CASH_COLLECTION_ACCOUNT", "DEFAULT_BANK_COLLECTION_ACCOUNT"]:
        try: default_ids.add(resolve_system_account(key).id)
        except Exception: pass
    accounts=AccountingAccount.query.order_by(AccountingAccount.account_code).all()
    return jsonify({"accounts":[{"id":a.id,"account_code":a.account_code,"account_name":a.account_name,"account_subtype":account_subtype(a),"is_default":a.id in default_ids} for a in accounts if is_funding_account(a) and account_subtype(a) in subtypes]})

def _journal_filter_error(error, message):
    return jsonify({"error": error, "message": message}), 422

def _journal_arg(name):
    value = request.args.get(name)
    return value.strip() if value and value.strip() else None

def _journal_id_arg(name):
    value = _journal_arg(name)
    if value is None: return None, None
    try: return int(value), None
    except ValueError: return None, _journal_filter_error(f"invalid_{name}", f"{name} must be a numeric ID.")

@accounting_bp.route("/journal-entries", methods=["GET"])
@accounting_bp.route("/journals", methods=["GET"])
@role_required(["admin"])
def list_journals():
    try:
        raw_from, raw_to = _journal_arg("date_from"), _journal_arg("date_to")
        try:
            if any(value and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) for value in (raw_from, raw_to)): raise ValueError
            date_from, date_to = (date.fromisoformat(raw_from) if raw_from else None), (date.fromisoformat(raw_to) if raw_to else None)
        except ValueError: return _journal_filter_error("invalid_date", "Dates must use YYYY-MM-DD format.")
        if date_from and date_to and date_from > date_to: return _journal_filter_error("invalid_date_range", "Date From cannot be later than Date To.")
        ids = {}
        for name in ("account_id", "customer_id", "loan_id"):
            ids[name], error = _journal_id_arg(name)
            if error: return error
        try:
            page = max(1, int(_journal_arg("page") or 1)); page_size = min(100, max(1, int(_journal_arg("page_size") or _journal_arg("per_page") or 25)))
        except ValueError: return _journal_filter_error("invalid_pagination", "page and page_size must be positive integers.")
        status = _journal_arg("status"); status = status.upper() if status and status.upper() != "ALL" else None
        reference_type = _journal_arg("reference_type"); reference_type = reference_type.upper() if reference_type else None
        search = _journal_arg("search"); effective_date = func.coalesce(AccountingJournalEntry.accounting_date, AccountingJournalEntry.journal_date)
        q = AccountingJournalEntry.query.options(selectinload(AccountingJournalEntry.lines).joinedload(AccountingJournalLine.account), selectinload(AccountingJournalEntry.lines).joinedload(AccountingJournalLine.customer), selectinload(AccountingJournalEntry.lines).joinedload(AccountingJournalLine.loan), selectinload(AccountingJournalEntry.reversal_journals), joinedload(AccountingJournalEntry.reversal_of), joinedload(AccountingJournalEntry.created_by), joinedload(AccountingJournalEntry.posted_by))
        if date_from: q = q.filter(effective_date >= date_from)
        if date_to: q = q.filter(effective_date <= date_to)
        if status: q = q.filter(func.upper(func.trim(AccountingJournalEntry.status)) == status)
        if reference_type: q = q.filter(or_(func.upper(func.trim(AccountingJournalEntry.source_type)) == reference_type, func.upper(func.trim(AccountingJournalEntry.reference_type)) == reference_type))
        if ids["account_id"] is not None: q = q.filter(AccountingJournalEntry.lines.any(AccountingJournalLine.account_id == ids["account_id"]))
        if ids["customer_id"] is not None: q = q.filter(or_(AccountingJournalEntry.customer_id == ids["customer_id"], AccountingJournalEntry.lines.any(AccountingJournalLine.customer_id == ids["customer_id"])))
        if ids["loan_id"] is not None: q = q.filter(or_(AccountingJournalEntry.loan_id == ids["loan_id"], AccountingJournalEntry.lines.any(AccountingJournalLine.loan_id == ids["loan_id"])))
        if search:
            pattern = f"%{search}%"
            matching_customers = db.session.query(Customer.id).filter(or_(Customer.full_name.ilike(pattern), Customer.customer_code.ilike(pattern)))
            matching_loans = db.session.query(Loan.id).filter(Loan.loan_number.ilike(pattern))
            q = q.filter(or_(AccountingJournalEntry.journal_no.ilike(pattern), AccountingJournalEntry.description.ilike(pattern), AccountingJournalEntry.reference.ilike(pattern), AccountingJournalEntry.reference_type.ilike(pattern), AccountingJournalEntry.source_type.ilike(pattern), AccountingJournalEntry.reversal_of.has(AccountingJournalEntry.journal_no.ilike(pattern)), AccountingJournalEntry.reversal_journals.any(AccountingJournalEntry.journal_no.ilike(pattern)), AccountingJournalEntry.loan_id.in_(matching_loans), AccountingJournalEntry.customer_id.in_(matching_customers), AccountingJournalEntry.lines.any(AccountingJournalLine.loan.has(Loan.loan_number.ilike(pattern))), AccountingJournalEntry.lines.any(AccountingJournalLine.customer.has(or_(Customer.full_name.ilike(pattern), Customer.customer_code.ilike(pattern))))))
        columns = {"accounting_date": effective_date, "journal_date": effective_date, "journal_number": AccountingJournalEntry.journal_no, "journal_no": AccountingJournalEntry.journal_no, "status": AccountingJournalEntry.status}
        column = columns.get((_journal_arg("sort_by") or "accounting_date").lower(), effective_date)
        ordering = column.asc() if (_journal_arg("sort_direction") or "desc").lower() == "asc" else column.desc()
        total = q.order_by(None).count(); entries = q.order_by(ordering, AccountingJournalEntry.id.desc()).offset((page - 1) * page_size).limit(page_size).all(); total_pages = (total + page_size - 1) // page_size
        return jsonify({"items": [serialize_journal(e) for e in entries], "total": total, "page": page, "per_page": page_size, "pagination": {"page": page, "page_size": page_size, "total_items": total, "total_pages": total_pages, "has_next": page < total_pages, "has_previous": page > 1}, "applied_filters": {"date_from": raw_from, "date_to": raw_to, "status": status, "reference_type": reference_type, **ids, "search": search}})
    except Exception:
        current_app.logger.exception("Journal list query failed")
        return jsonify({"error": "journal_list_error", "message": "Unable to retrieve journal entries."}), 500

@accounting_bp.route("/journal-reference-types", methods=["GET"])
@role_required(["admin"])
def journal_reference_types():
    source = func.coalesce(AccountingJournalEntry.source_type, AccountingJournalEntry.reference_type)
    return jsonify({"items": [value for (value,) in db.session.query(source).filter(source.isnot(None)).distinct().order_by(source).all() if value]})

@accounting_bp.route("/journals/<int:journal_id>", methods=["GET"])
@accounting_bp.route("/journal-entries/<int:journal_id>", methods=["GET"])
@role_required(["admin"])
def get_journal(journal_id):
    return jsonify(serialize_journal(AccountingJournalEntry.query.get_or_404(journal_id)))

def _create_manual_journal_from_request(post_now=False):
    data=request.get_json() or {}
    journal_date = date.fromisoformat(data["journal_date"])
    entry=create_draft_journal(journal_date, data.get("description"), data.get("lines") or [], "MANUAL_JOURNAL", None, "ACCOUNTING", _uid(), reference=data.get("reference"))
    if post_now or str(data.get("status", "DRAFT")).upper() == "POSTED":
        post_journal(entry, _uid())
    db.session.commit()
    return jsonify(serialize_journal(entry)), 201

@accounting_bp.route("/journals", methods=["POST"])
@accounting_bp.route("/journal-entries", methods=["POST"])
@role_required(["admin"])
def create_journal():
    try:
        return _create_manual_journal_from_request(False)
    except Exception as exc: return _error(exc)

@accounting_bp.route("/journal-entries/post", methods=["POST"])
@role_required(["admin"])
def post_manual_journal_direct():
    try:
        return _create_manual_journal_from_request(True)
    except Exception as exc: return _error(exc)

@accounting_bp.route("/journals/<int:journal_id>/post", methods=["POST"])
@accounting_bp.route("/journal-entries/<int:journal_id>/post", methods=["POST"])
@role_required(["admin"])
def post_existing_journal(journal_id):
    try:
        entry=post_journal(AccountingJournalEntry.query.get_or_404(journal_id), _uid()); db.session.commit(); return jsonify(serialize_journal(entry))
    except Exception as exc: return _error(exc)

@accounting_bp.route("/journals/<int:journal_id>/reverse", methods=["POST"])
@role_required(["admin"])
def reverse_existing_journal(journal_id):
    data=request.get_json() or {}
    try:
        rev=reverse_journal(AccountingJournalEntry.query.get_or_404(journal_id), date.fromisoformat(data.get("journal_date") or date.today().isoformat()), data.get("reason","Correction"), _uid()); db.session.commit(); return jsonify(serialize_journal(rev)), 201
    except Exception as exc: return _error(exc)

def _arg(name):
    value = request.args.get(name)
    return None if value is None or not value.strip() else value

def _arg_date(name):
    value = _arg(name)
    return date.fromisoformat(value) if value else None

def _ledger_data():
    return general_ledger(
        account_id=_arg("account_id"),
        account_code=_arg("account_code"),
        date_from=_arg_date("date_from"),
        date_to=_arg_date("date_to"),
        customer_id=_arg("customer_id"),
        loan_id=_arg("loan_id"),
        query_params=request.args.to_dict(flat=True),
    )



def _bool_arg(name, default=False):
    value = request.args.get(name)
    if value is None:
        return default
    return str(value).lower() in ("1", "true", "yes", "on")

def _generated_by():
    user_id = _uid()
    return str(user_id) if user_id else "system"

@accounting_bp.route("/reports/trial-balance", methods=["GET"])
@role_required(["admin"])
def trial_balance():
    try:
        return jsonify(trial_balance_report(as_of_date=_arg_date("as_of_date") or date.today(), date_from=_arg_date("date_from"), include_zero_balances=_bool_arg("include_zero_balances"), account_type=_arg("account_type"), account_id=_arg("account_id"), comparative_as_of_date=_arg_date("comparative_as_of_date")))
    except Exception as exc: return _error(exc)

@accounting_bp.route("/reports/income-statement", methods=["GET"])
@role_required(["admin"])
def income_statement():
    if not _arg("date_from") or not _arg("date_to"):
        return jsonify({"message":"date_from and date_to are required"}), 400
    try:
        return jsonify(income_statement_report(_arg_date("date_from"), _arg_date("date_to"), _arg_date("comparative_date_from"), _arg_date("comparative_date_to"), _bool_arg("include_zero_balances")))
    except Exception as exc: return _error(exc)

@accounting_bp.route("/reports/statement-of-financial-position", methods=["GET"])
@accounting_bp.route("/reports/balance-sheet", methods=["GET"])
@role_required(["admin"])
def statement_of_financial_position():
    try:
        return jsonify(statement_of_financial_position_report(_arg_date("as_of_date") or date.today(), _arg_date("comparative_as_of_date"), _bool_arg("include_zero_balances")))
    except Exception as exc: return _error(exc)

@accounting_bp.route("/reports/account-drilldown", methods=["GET"])
@role_required(["admin"])
def account_drilldown():
    if not _arg("account_id") or not _arg("date_to"):
        return jsonify({"message":"account_id and date_to are required"}), 400
    try:
        return jsonify(general_ledger(account_id=_arg("account_id"), date_from=_arg_date("date_from"), date_to=_arg_date("date_to"), customer_id=_arg("customer_id"), loan_id=_arg("loan_id"), query_params=request.args.to_dict(flat=True)))
    except Exception as exc: return _error(exc)

@accounting_bp.route("/reports/summary", methods=["GET"])
@role_required(["admin"])
def financial_reports_summary():
    try:
        return jsonify(reports_summary(_arg_date("date_from"), _arg_date("date_to"), _arg_date("as_of_date")))
    except Exception as exc: return _error(exc)

@accounting_bp.route("/reports/trial-balance/export.csv", methods=["GET"])
@role_required(["admin"])
def trial_balance_csv():
    data=trial_balance_report(as_of_date=_arg_date("as_of_date") or date.today(), date_from=_arg_date("date_from"), include_zero_balances=_bool_arg("include_zero_balances"), account_type=_arg("account_type"), account_id=_arg("account_id"), comparative_as_of_date=_arg_date("comparative_as_of_date"))
    return Response(report_csv(data, _generated_by()), mimetype="text/csv", headers={"Content-Disposition":"attachment; filename=trial-balance.csv"})

@accounting_bp.route("/reports/income-statement/export.csv", methods=["GET"])
@role_required(["admin"])
def income_statement_csv():
    if not _arg("date_from") or not _arg("date_to"):
        return jsonify({"message":"date_from and date_to are required"}), 400
    data=income_statement_report(_arg_date("date_from"), _arg_date("date_to"), _arg_date("comparative_date_from"), _arg_date("comparative_date_to"), _bool_arg("include_zero_balances"))
    return Response(report_csv(data, _generated_by()), mimetype="text/csv", headers={"Content-Disposition":"attachment; filename=income-statement.csv"})

@accounting_bp.route("/reports/statement-of-financial-position/export.csv", methods=["GET"])
@role_required(["admin"])
def statement_of_financial_position_csv():
    data=statement_of_financial_position_report(_arg_date("as_of_date") or date.today(), _arg_date("comparative_as_of_date"), _bool_arg("include_zero_balances"))
    return Response(report_csv(data, _generated_by()), mimetype="text/csv", headers={"Content-Disposition":"attachment; filename=statement-of-financial-position.csv"})

@accounting_bp.route("/general-ledger", methods=["GET"])
@role_required(["admin"])
def get_gl():
    if not _arg("account_id") and not _arg("account_code"): return jsonify({"message":"account_id is required"}), 400
    try:
        return jsonify(_ledger_data())
    except Exception as exc: return _error(exc)

@accounting_bp.route("/general-ledger/export.csv", methods=["GET"])
@role_required(["admin"])
def export_gl():
    if not _arg("account_id") and not _arg("account_code"): return jsonify({"message":"account_id is required"}), 400
    try:
        data=_ledger_data()
    except Exception as exc: return _error(exc)
    return Response(ledger_csv(data), mimetype="text/csv", headers={"Content-Disposition":"attachment; filename=general-ledger.csv"})

@accounting_bp.route("/reconciliation/issues", methods=["GET"])
@role_required(["admin"])
def issues():
    return jsonify(reconciliation_summary())
