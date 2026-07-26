from datetime import date, datetime

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import AccountingAccount, CollectionSheet, CollectionSheetExpense, CollectionSheetItem, Loan, User
from ..accounting import log_audit
from ..collection_sheets import (ELIGIBLE_LOANS, SheetError, approve_and_post, decimal_amount,
                                 ensure_draft, preview, recalculate, reverse, search_loans,
                                 serialize, sheet_number, valid_bank, valid_expense_account, validate)
from .utils import role_required

collection_sheets_bp = Blueprint("collection_sheets", __name__, url_prefix="/admin/collection-sheets")


@collection_sheets_bp.errorhandler(SheetError)
def sheet_error(exc):
    db.session.rollback()
    return jsonify({"error": str(exc), "message": str(exc), **exc.details}), exc.status


def actor(): return int(get_jwt_identity())


@collection_sheets_bp.post("")
@role_required(["admin"])
def create_sheet():
    data = request.get_json(silent=True) or {}
    try: collection_date = date.fromisoformat(str(data.get("collection_date")))
    except ValueError: raise SheetError("collection_date must be a valid ISO date")
    collector = db.session.get(User, data.get("collector_id"))
    if not collector: raise SheetError("Collector not found", 404)
    sheet = CollectionSheet(sheet_number=sheet_number(collection_date), collector_id=collector.id,
                            collection_date=collection_date, notes=data.get("notes"), created_by_id=actor())
    db.session.add(sheet)
    try:
        db.session.flush(); log_audit("COLLECTION_SHEET_CREATE", "CollectionSheet", sheet.id, actor()); db.session.commit()
    except IntegrityError:
        db.session.rollback(); raise SheetError("Could not allocate a unique sheet number; retry request", 409)
    return jsonify({"id": sheet.id, "sheet_number": sheet.sheet_number, "status": sheet.status}), 201


@collection_sheets_bp.get("")
@role_required(["admin"])
def list_sheets():
    query = CollectionSheet.query
    if request.args.get("date_from"): query = query.filter(CollectionSheet.collection_date >= date.fromisoformat(request.args["date_from"]))
    if request.args.get("date_to"): query = query.filter(CollectionSheet.collection_date <= date.fromisoformat(request.args["date_to"]))
    if request.args.get("collector_id"): query = query.filter_by(collector_id=int(request.args["collector_id"]))
    if request.args.get("status"): query = query.filter_by(status=request.args["status"].upper())
    if request.args.get("sheet_number"): query = query.filter(CollectionSheet.sheet_number.ilike(f"%{request.args['sheet_number']}%"))
    return jsonify({"items": [serialize(s) for s in query.order_by(CollectionSheet.collection_date.desc(), CollectionSheet.id.desc()).all()]})


@collection_sheets_bp.get("/loan-search")
@role_required(["admin"])
def loan_search(): return jsonify({"items": search_loans(request.args.get("q"))})


@collection_sheets_bp.get("/<int:sheet_id>")
@role_required(["admin"])
def detail(sheet_id): return jsonify(serialize(CollectionSheet.query.get_or_404(sheet_id), True))


@collection_sheets_bp.get("/<int:sheet_id>/print")
@role_required(["admin"])
def printable(sheet_id):
    sheet = CollectionSheet.query.get_or_404(sheet_id)
    return jsonify({"organisation": "GROW Microfinance", "title": "Daily Collection Sheet", **serialize(sheet, True),
                    "prepared_by": sheet.created_by.name if sheet.created_by else None,
                    "approved_by": sheet.approved_by.name if sheet.approved_by else None})


@collection_sheets_bp.post("/<int:sheet_id>/items")
@role_required(["admin"])
def add_item(sheet_id):
    sheet = CollectionSheet.query.get_or_404(sheet_id); ensure_draft(sheet); data = request.get_json(silent=True) or {}
    loan = db.session.get(Loan, data.get("loan_id"))
    if not loan or not loan.customer: raise SheetError("Loan/customer not found", 404)
    if (loan.status or "").upper() not in ELIGIBLE_LOANS: raise SheetError("Loan is not eligible for collection")
    amount = decimal_amount(data.get("amount"))
    item = CollectionSheetItem.query.filter_by(collection_sheet_id=sheet.id, loan_id=loan.id).first()
    if item: item.amount = amount  # Chosen duplicate policy: merge by replacing the line amount.
    else: item = CollectionSheetItem(sheet=sheet, loan_id=loan.id, customer_id=loan.customer_id, amount=amount); db.session.add(item)
    db.session.flush(); recalculate(sheet); log_audit("COLLECTION_SHEET_ITEM_UPSERT", "CollectionSheet", sheet.id, actor(), {"item_id": item.id}); db.session.commit()
    return jsonify(serialize(sheet, True)), 201


@collection_sheets_bp.delete("/<int:sheet_id>/items/<int:item_id>")
@role_required(["admin"])
def remove_item(sheet_id, item_id):
    sheet = CollectionSheet.query.get_or_404(sheet_id); ensure_draft(sheet)
    item = CollectionSheetItem.query.filter_by(id=item_id, collection_sheet_id=sheet.id).first_or_404()
    db.session.delete(item); db.session.flush(); recalculate(sheet); log_audit("COLLECTION_SHEET_ITEM_REMOVE", "CollectionSheet", sheet.id, actor(), {"item_id": item_id}); db.session.commit()
    return "", 204


@collection_sheets_bp.post("/<int:sheet_id>/expenses")
@role_required(["admin"])
def add_expense(sheet_id):
    sheet = CollectionSheet.query.get_or_404(sheet_id); ensure_draft(sheet); data = request.get_json(silent=True) or {}
    account = db.session.get(AccountingAccount, data.get("expense_account_id"))
    if not valid_expense_account(account): raise SheetError("Only active, posting-enabled expense GL accounts are allowed")
    expense = CollectionSheetExpense(sheet=sheet, expense_account_id=account.id, amount=decimal_amount(data.get("amount")),
                                     description=(data.get("description") or "").strip(), reference=data.get("reference"))
    if not expense.description: raise SheetError("Expense description is required")
    db.session.add(expense); db.session.flush(); recalculate(sheet); log_audit("COLLECTION_SHEET_EXPENSE_ADD", "CollectionSheet", sheet.id, actor(), {"expense_id": expense.id}); db.session.commit()
    return jsonify(serialize(sheet, True)), 201


@collection_sheets_bp.delete("/<int:sheet_id>/expenses/<int:expense_id>")
@role_required(["admin"])
def remove_expense(sheet_id, expense_id):
    sheet = CollectionSheet.query.get_or_404(sheet_id); ensure_draft(sheet)
    expense = CollectionSheetExpense.query.filter_by(id=expense_id, collection_sheet_id=sheet.id).first_or_404()
    db.session.delete(expense); db.session.flush(); recalculate(sheet); log_audit("COLLECTION_SHEET_EXPENSE_REMOVE", "CollectionSheet", sheet.id, actor(), {"expense_id": expense_id}); db.session.commit()
    return "", 204


@collection_sheets_bp.patch("/<int:sheet_id>")
@role_required(["admin"])
def update_sheet(sheet_id):
    sheet = CollectionSheet.query.get_or_404(sheet_id); ensure_draft(sheet); data = request.get_json(silent=True) or {}; before = serialize(sheet)
    if "bank_account_id" in data:
        account = db.session.get(AccountingAccount, data["bank_account_id"])
        if not valid_bank(account): raise SheetError("Selected bank account is not a valid posting bank/cash account")
        sheet.bank_account_id = account.id
    if "deposit_date" in data: sheet.deposit_date = date.fromisoformat(data["deposit_date"]) if data["deposit_date"] else None
    if "actual_deposit" in data: sheet.actual_deposit = decimal_amount(data["actual_deposit"], "actual_deposit", allow_zero=True)
    for field in ("deposit_reference", "notes"):
        if field in data: setattr(sheet, field, data[field])
    recalculate(sheet); log_audit("COLLECTION_SHEET_EDIT", "CollectionSheet", sheet.id, actor(), {"before": before, "after": serialize(sheet)}); db.session.commit()
    return jsonify(serialize(sheet, True))


@collection_sheets_bp.post("/<int:sheet_id>/submit")
@role_required(["admin"])
def submit(sheet_id):
    sheet = CollectionSheet.query.get_or_404(sheet_id); ensure_draft(sheet); validate(sheet, submitted=True)
    sheet.status = "SUBMITTED"; sheet.submitted_at = datetime.utcnow(); sheet.submitted_by_id = actor()
    log_audit("COLLECTION_SHEET_SUBMIT", "CollectionSheet", sheet.id, actor()); db.session.commit(); return jsonify(serialize(sheet, True))


@collection_sheets_bp.get("/<int:sheet_id>/posting-preview")
@role_required(["admin"])
def posting_preview(sheet_id): return jsonify(preview(CollectionSheet.query.get_or_404(sheet_id)))


@collection_sheets_bp.post("/<int:sheet_id>/approve-post")
@role_required(["admin"])
def approve(sheet_id): return jsonify(approve_and_post(sheet_id, actor()))


@collection_sheets_bp.post("/<int:sheet_id>/reverse")
@role_required(["admin"])
def reverse_sheet(sheet_id):
    data = request.get_json(silent=True) or {}; when = date.fromisoformat(data["reversal_date"]) if data.get("reversal_date") else date.today()
    return jsonify(reverse(CollectionSheet.query.get_or_404(sheet_id), actor(), data.get("reason"), when))
