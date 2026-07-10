from datetime import date
from flask import Blueprint, Response, jsonify, request
from flask_jwt_extended import get_jwt_identity

from ..extensions import db
from ..models import AccountingAccount, AccountingJournalEntry
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
    seed_default_accounts()
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
    return jsonify([{"id":a.id,"account_code":a.account_code,"account_name":a.account_name,"account_type":a.account_type,"normal_balance":a.normal_balance,"parent_id":a.parent_id,"description":a.description,"is_system_account":a.is_system_account,"is_active":a.is_active,"allow_manual_posting":a.allow_manual_posting,"cash_flow_category":a.cash_flow_category} for a in q.order_by(AccountingAccount.account_code).all()])

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

@accounting_bp.route("/journals", methods=["GET"])
@role_required(["admin"])
def list_journals():
    q=AccountingJournalEntry.query
    if request.args.get("date_from"): q=q.filter(AccountingJournalEntry.journal_date>=date.fromisoformat(request.args["date_from"]))
    if request.args.get("date_to"): q=q.filter(AccountingJournalEntry.journal_date<=date.fromisoformat(request.args["date_to"]))
    for f in ["status","reference_type"]:
        if request.args.get(f): q=q.filter(getattr(AccountingJournalEntry,f)==request.args[f])
    if request.args.get("search"):
        s=f"%{request.args['search']}%"; q=q.filter((AccountingJournalEntry.journal_no.ilike(s)) | (AccountingJournalEntry.description.ilike(s)))
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

@accounting_bp.route("/general-ledger", methods=["GET"])
@role_required(["admin"])
def get_gl():
    if not request.args.get("account_id"): return jsonify({"message":"account_id is required"}), 400
    try:
        return jsonify(general_ledger(int(request.args["account_id"]), date.fromisoformat(request.args["date_from"]) if request.args.get("date_from") else None, date.fromisoformat(request.args["date_to"]) if request.args.get("date_to") else None, request.args.get("customer_id"), request.args.get("loan_id")))
    except Exception as exc: return _error(exc)

@accounting_bp.route("/general-ledger/export.csv", methods=["GET"])
@role_required(["admin"])
def export_gl():
    if not request.args.get("account_id"): return jsonify({"message":"account_id is required"}), 400
    data=general_ledger(int(request.args["account_id"]), date.fromisoformat(request.args["date_from"]) if request.args.get("date_from") else None, date.fromisoformat(request.args["date_to"]) if request.args.get("date_to") else None, request.args.get("customer_id"), request.args.get("loan_id"))
    return Response(ledger_csv(data), mimetype="text/csv", headers={"Content-Disposition":"attachment; filename=general-ledger.csv"})

@accounting_bp.route("/reconciliation/issues", methods=["GET"])
@role_required(["admin"])
def issues():
    return jsonify({"issues": reconciliation_issues()})
