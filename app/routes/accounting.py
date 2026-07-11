from datetime import date
from flask import Blueprint, Response, jsonify, request
from flask_jwt_extended import get_jwt_identity

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
    reverse_journal,
    seed_default_accounts,
    serialize_journal,
    update_account,
    accounting_settings_payload,
    update_accounting_settings,
    account_subtype,
    validate_funding_account,
    resolve_system_account,
    trial_balance_report,
    income_statement_report,
    statement_of_financial_position_report,
    reports_summary,
    report_csv,
    seed_default_report_classifications,
)
from .utils import role_required

accounting_bp = Blueprint("accounting", __name__, url_prefix="/admin/accounting")

def _uid():
    ident = get_jwt_identity()
    return int(ident) if ident else None

def _error(exc):
    db.session.rollback()
    return jsonify({"message": str(exc)}), 400

@accounting_bp.before_request
def _ensure_seeded():
    seed_default_report_classifications()
    db.session.flush()

@accounting_bp.route("/accounts", methods=["GET"])
@role_required(["admin"])
def list_accounts():
    q = AccountingAccount.query
    if request.args.get("account_type"): q=q.filter_by(account_type=request.args["account_type"])
    if request.args.get("active") is not None: q=q.filter_by(is_active=request.args.get("active").lower()=="true")
    if request.args.get("parent_id"): q=q.filter_by(parent_id=request.args.get("parent_id"))
    if request.args.get("search"):
        s=f"%{request.args['search']}%"; q=q.filter((AccountingAccount.account_code.ilike(s)) | (AccountingAccount.account_name.ilike(s)))
    return jsonify([{"id":a.id,"account_code":a.account_code,"account_name":a.account_name,"account_type":a.account_type,"normal_balance":a.normal_balance,"parent_id":a.parent_id,"description":a.description,"is_system_account":a.is_system_account,"is_active":a.is_active,"allow_manual_posting":a.allow_manual_posting,"cash_flow_category":a.cash_flow_category,"account_subtype":account_subtype(a),"financial_statement_group":a.financial_statement_group,"financial_statement_order":a.financial_statement_order,"cash_flow_group":a.cash_flow_group} for a in q.order_by(AccountingAccount.account_code).all()])

@accounting_bp.route("/accounts", methods=["POST"])
@role_required(["admin"])
def add_account():
    try:
        acct=create_account(request.get_json() or {}, _uid()); db.session.commit()
        return jsonify({"id":acct.id,"account_code":acct.account_code}), 201
    except Exception as exc: return _error(exc)

@accounting_bp.route("/accounts/<int:account_id>", methods=["PUT"])
@role_required(["admin"])
def edit_account(account_id):
    acct=AccountingAccount.query.get_or_404(account_id)
    try:
        update_account(acct, request.get_json() or {}, _uid()); db.session.commit()
        return jsonify({"id":acct.id,"account_code":acct.account_code})
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
    return jsonify({"settings": accounting_settings_payload()})

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
    accounts=AccountingAccount.query.filter_by(is_active=True, account_type="ASSET", allow_manual_posting=True).order_by(AccountingAccount.account_code).all()
    return jsonify({"accounts":[{"id":a.id,"account_code":a.account_code,"account_name":a.account_name,"account_subtype":account_subtype(a),"is_default":a.id in default_ids} for a in accounts if account_subtype(a) in subtypes]})

@accounting_bp.route("/journals", methods=["GET"])
@role_required(["admin"])
def list_journals():
    q=AccountingJournalEntry.query
    if request.args.get("date_from"): q=q.filter(AccountingJournalEntry.journal_date>=date.fromisoformat(request.args["date_from"]))
    if request.args.get("date_to"): q=q.filter(AccountingJournalEntry.journal_date<=date.fromisoformat(request.args["date_to"]))
    for f in ["status","reference_type"]:
        if request.args.get(f): q=q.filter(getattr(AccountingJournalEntry,f)==request.args[f])
    if request.args.get("journal_no"): q=q.filter(AccountingJournalEntry.journal_no.ilike(f"%{request.args['journal_no']}%"))
    if request.args.get("reference_id"): q=q.filter(AccountingJournalEntry.reference_id==request.args["reference_id"])
    if request.args.get("search"):
        s=f"%{request.args['search']}%"; q=q.filter((AccountingJournalEntry.journal_no.ilike(s)) | (AccountingJournalEntry.description.ilike(s)))
    if request.args.get("loan_number"):
        q=q.join(AccountingJournalEntry.lines).join(Loan, AccountingJournalLine.loan_id==Loan.id).filter(Loan.loan_number.ilike(f"%{request.args['loan_number']}%"))
    if request.args.get("customer_name"):
        q=q.join(AccountingJournalEntry.lines).join(Customer, AccountingJournalLine.customer_id==Customer.id).filter(Customer.full_name.ilike(f"%{request.args['customer_name']}%"))
    if request.args.get("account_id"): q=q.join(AccountingJournalEntry.lines).filter_by(account_id=request.args.get("account_id"))
    if request.args.get("customer_id"): q=q.join(AccountingJournalEntry.lines).filter_by(customer_id=request.args.get("customer_id"))
    if request.args.get("loan_id"): q=q.join(AccountingJournalEntry.lines).filter_by(loan_id=request.args.get("loan_id"))
    page=int(request.args.get("page",1)); per=int(request.args.get("per_page",50))
    items=q.order_by(AccountingJournalEntry.journal_date.desc(), AccountingJournalEntry.id.desc()).paginate(page=page, per_page=per, error_out=False)
    return jsonify({"items":[serialize_journal(e) for e in items.items],"total":items.total,"page":page,"per_page":per})

@accounting_bp.route("/journals/<int:journal_id>", methods=["GET"])
@role_required(["admin"])
def get_journal(journal_id):
    return jsonify(serialize_journal(AccountingJournalEntry.query.get_or_404(journal_id)))

@accounting_bp.route("/journals", methods=["POST"])
@role_required(["admin"])
def create_journal():
    data=request.get_json() or {}
    try:
        entry=create_draft_journal(date.fromisoformat(data["journal_date"]), data["description"], data.get("lines") or [], "MANUAL_JOURNAL", None, "ACCOUNTING", _uid())
        if data.get("status","DRAFT") == "POSTED": post_journal(entry, _uid())
        db.session.commit(); return jsonify(serialize_journal(entry)), 201
    except Exception as exc: return _error(exc)

@accounting_bp.route("/journals/<int:journal_id>/post", methods=["POST"])
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
    return jsonify({"issues": reconciliation_issues()})
