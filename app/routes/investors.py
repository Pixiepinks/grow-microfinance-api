from datetime import date
from decimal import Decimal
from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import or_
from flask_jwt_extended import get_jwt_identity
from ..extensions import db
from ..models import AccountingJournalEntry, Investor, InvestorFundingAgreement, InvestorFundingTransaction, InvestorInterestAccrual
from ..investor_funding import INCREASE_TYPES, DECREASE_TYPES, create_investor, create_agreement, record_funding, principal_repayment, calculate_investor_interest, post_investor_interest_accrual, pay_interest, capitalize_interest, month_bounds, completed_periods_for, reverse_investor_transaction, investor_reconciliation, reverse_interest_accrual, catch_up_investor_interest, investor_interest_summary
from ..accounting import ValidationError, AccountingError
from .utils import role_required

investors_bp = Blueprint("investors", __name__, url_prefix="/admin")

def uid():
    ident = get_jwt_identity(); return int(ident) if ident else None

def dec(v): return float(v) if v is not None else None

def mask_bank_account(value):
    if not value:
        return None
    value = str(value)
    return "*" * max(len(value) - 4, 0) + value[-4:]


def normalize_status(value):
    return str(value or "").strip().upper()


def normalize_investor_status(investor):
    return normalize_status(investor.status)


def investor_display_name(investor):
    investor_type = str(investor.investor_type or "").strip().upper()
    full_name = (investor.full_name or "").strip()
    company_name = (investor.company_name or "").strip()
    investor_number = (investor.investor_number or "").strip()
    if investor_type == "INDIVIDUAL" and full_name:
        return full_name
    if investor_type == "COMPANY" and company_name:
        return company_name
    return full_name or company_name or investor_number


def investor_is_active(investor):
    if normalize_investor_status(investor) != "ACTIVE":
        return False
    if getattr(investor, "deleted_at", None) is not None:
        return False
    if getattr(investor, "is_deleted", False):
        return False
    if getattr(investor, "suspended_at", None) is not None:
        return False
    return True


def agreement_is_fundable(agreement):
    if normalize_status(agreement.status) != "ACTIVE":
        return False
    if not agreement.investor or not investor_is_active(agreement.investor):
        return False
    principal = float(agreement.current_principal_balance or 0)
    if principal > 0 and not agreement.allow_additional_funding:
        return False
    return True


def inv_dict(i, include_sensitive=False):
    display_name = investor_display_name(i)
    data = {"id":i.id,"investor_id":i.id,"investor_number":i.investor_number,"investor_type":i.investor_type,"full_name":i.full_name,"company_name":i.company_name,"display_name":display_name,"nic":i.nic,"company_registration_number":i.company_registration_number,"tax_identification_number":i.tax_identification_number,"mobile":i.mobile,"email":i.email,"address":i.address,"bank_name":i.bank_name,"bank_branch":i.bank_branch,"bank_account_name":i.bank_account_name,"bank_account_number":i.bank_account_number if include_sensitive else mask_bank_account(i.bank_account_number),"notes":i.notes,"status":normalize_investor_status(i),"created_at":i.created_at.isoformat() if i.created_at else None}
    return data



def investor_summary_dict(investor, aggregates=None):
    aggregates = aggregates or {}
    display_name = investor_display_name(investor)
    return {
        "id": investor.id,
        "investor_id": investor.id,
        "investor_number": investor.investor_number,
        "investor_type": investor.investor_type,
        "full_name": investor.full_name,
        "company_name": investor.company_name,
        "display_name": display_name,
        "nic": investor.nic,
        "company_registration_number": investor.company_registration_number,
        "mobile": investor.mobile,
        "active_agreements": int(aggregates.get("active_agreements") or 0),
        "principal_balance": dec(aggregates.get("principal_balance") or 0),
        "accrued_interest": dec(aggregates.get("accrued_interest") or 0),
        "status": normalize_investor_status(investor),
    }


def investor_list_aggregates():
    active_agreement_totals = (
        db.session.query(
            InvestorFundingAgreement.investor_id.label("investor_id"),
            db.func.count(InvestorFundingAgreement.id).label("active_agreements"),
            db.func.coalesce(db.func.sum(InvestorFundingAgreement.current_principal_balance), 0).label("principal_balance"),
        )
        .filter(InvestorFundingAgreement.status == "ACTIVE")
        .group_by(InvestorFundingAgreement.investor_id)
        .subquery()
    )
    accrual_totals = (
        db.session.query(
            InvestorInterestAccrual.investor_id.label("investor_id"),
            db.func.coalesce(
                db.func.sum(
                    InvestorInterestAccrual.net_interest_payable
                    - InvestorInterestAccrual.payment_amount
                    - InvestorInterestAccrual.capitalization_amount
                ),
                0,
            ).label("accrued_interest"),
        )
        .filter(InvestorInterestAccrual.status.in_(["POSTED", "PARTIALLY_PAID"]))
        .group_by(InvestorInterestAccrual.investor_id)
        .subquery()
    )
    return active_agreement_totals, accrual_totals

def investor_option_dict(i):
    display_name = investor_display_name(i)
    return {"id":i.id,"investor_id":i.id,"investor_number":i.investor_number,"investor_type":i.investor_type,"display_name":display_name,"full_name":i.full_name,"company_name":i.company_name,"nic":i.nic,"status":normalize_investor_status(i),"label":f"{i.investor_number} — {display_name}"}


def agreement_option_dict(a):
    investor = a.investor
    investor_name = investor_display_name(investor) if investor else None
    return {
        "id": a.id,
        "agreement_id": a.id,
        "agreement_number": a.agreement_number,
        "agreement_name": a.agreement_name,
        "investor_id": a.investor_id,
        "investor_number": investor.investor_number if investor else None,
        "investor_name": investor_name,
        "status": normalize_status(a.status),
        "start_date": a.start_date.isoformat() if a.start_date else None,
        "maturity_date": a.maturity_date.isoformat() if a.maturity_date else None,
        "original_principal_amount": dec(a.original_principal_amount),
        "current_principal_balance": dec(a.current_principal_balance),
        "allow_additional_funding": bool(a.allow_additional_funding),
        "funding_account_id": a.funding_account_id,
        "investor_liability_account_id": a.investor_liability_account_id,
        "label": f"{a.agreement_number} — {a.agreement_name or investor_name or ''}".rstrip(),
    }

def agreement_list_dict(a, accrued_interest=0):
    investor = a.investor
    investor_name = investor_display_name(investor) if investor else None
    return {
        "id": a.id,
        "agreement_id": a.id,
        "agreement_number": a.agreement_number,
        "agreement_name": a.agreement_name,
        "investor_id": a.investor_id,
        "investor": {
            "id": investor.id,
            "investor_number": investor.investor_number,
            "display_name": investor_name,
        } if investor else None,
        "investor_name": investor_name,
        "start_date": a.start_date.isoformat() if a.start_date else None,
        "maturity_date": a.maturity_date.isoformat() if a.maturity_date else None,
        "original_principal_amount": dec(a.original_principal_amount) or 0,
        "current_principal_balance": dec(a.current_principal_balance) or 0,
        "interest_rate": dec(a.interest_rate) or 0,
        "interest_rate_period": a.interest_rate_period,
        "calculation_method": a.calculation_method,
        "accrued_interest": dec(accrued_interest) or 0,
        "status": normalize_status(a.status),
    }


def agr_dict(a):
    posted = [x for x in a.interest_accruals if x.status in {"POSTED","PARTIALLY_PAID","PAID","CAPITALIZED"}]
    paid = sum((x.payment_amount for x in posted), 0); cap = sum((x.capitalization_amount for x in posted), 0)
    summary = investor_interest_summary(a.id)
    last = summary["last_accrued_through"]
    next_date = summary["next_accrual_date"]
    return {"id":a.id,"agreement_id":a.id,"agreement_number":a.agreement_number,"investor_id":a.investor_id,"investor":inv_dict(a.investor),"agreement_name":a.agreement_name,"agreement_date":a.agreement_date.isoformat() if a.agreement_date else None,"start_date":a.start_date.isoformat() if a.start_date else None,"original_principal":dec(a.original_principal_amount),"original_principal_amount":dec(a.original_principal_amount),"current_principal":dec(a.current_principal_balance),"current_principal_balance":dec(a.current_principal_balance),"interest_rate":dec(a.interest_rate),"interest_rate_period":a.interest_rate_period,"interest_rate_label":f"{a.interest_rate:.2f}% per {a.interest_rate_period.lower()}","calculation_method":a.calculation_method,"accrued_interest":dec(summary["accrued_interest"]),"accrued_unpaid_interest":dec(summary["accrued_unpaid_interest"]),"interest_paid":dec(paid),"capitalized_interest":dec(cap),"maturity_date":a.maturity_date.isoformat() if a.maturity_date else None,"next_accrual_date":next_date.isoformat() if next_date else None,"last_accrued_through":last.isoformat() if last else None,"missing_accrual_periods":summary["missing_accrual_periods"],"interest_catch_up_required":summary["interest_catch_up_required"],"accrual_status":summary["accrual_status"],"status":a.status,"created_at":a.created_at.isoformat() if a.created_at else None,"account_mappings":{"funding_account_id":a.funding_account_id,"investor_liability_account_id":a.investor_liability_account_id,"interest_expense_account_id":a.interest_expense_account_id,"accrued_interest_payable_account_id":a.accrued_interest_payable_account_id,"withholding_tax_account_id":a.withholding_tax_account_id},"warnings":[],"reconciliation_status":"OK"}

def tx_dict(t): return {"id":t.id,"transaction_number":t.transaction_number,"transaction_date":t.transaction_date.isoformat(),"accounting_date":t.accounting_date.isoformat(),"transaction_type":t.transaction_type,"amount":dec(t.amount),"reference":t.reference,"status":t.status,"journal_entry_id":t.journal_entry_id}
def ac_dict(a): return {"id":a.id,"agreement_id":a.agreement_id,"period_start":a.accrual_period_start.isoformat(),"period_end":a.accrual_period_end.isoformat(),"average_daily_balance":dec(a.average_daily_balance),"gross_interest_amount":dec(a.gross_interest_amount),"withholding_tax_amount":dec(a.withholding_tax_amount),"net_interest_payable":dec(a.net_interest_payable),"payment_amount":dec(a.payment_amount),"capitalization_amount":dec(a.capitalization_amount),"status":a.status,"journal_entry_id":a.journal_entry_id}
def error(exc):
    db.session.rollback()
    if isinstance(exc, (ValidationError, AccountingError)):
        payload = dict(getattr(exc, "payload", {"message": str(exc)}))
        status_code = payload.pop("status_code", 422)
        return jsonify(payload), status_code
    current_app.logger.exception("Unexpected investor API error", exc_info=exc)
    return jsonify({"error": "investor_creation_failed", "message": "The investor could not be created."}), 500


def investor_not_found():
    return jsonify({"error":"investor_not_found","message":"The investor was not found."}), 404


def investor_funding_not_found():
    return jsonify({"error":"investor_funding_not_found","message":"The investor funding record was not found."}), 404


def investor_agreement_not_found():
    return jsonify({"error":"investor_agreement_not_found","message":"The investor funding agreement was not found."}), 404

@investors_bp.route("/investors", methods=["GET"], strict_slashes=False)
@role_required(["admin"])
def list_investors():
    status = normalize_status(request.args.get("status") or "ALL")
    search = (request.args.get("q") or "").strip()

    active_agreement_totals, accrual_totals = investor_list_aggregates()
    query = (
        db.session.query(
            Investor,
            db.func.coalesce(active_agreement_totals.c.active_agreements, 0).label("active_agreements"),
            db.func.coalesce(active_agreement_totals.c.principal_balance, 0).label("principal_balance"),
            db.func.coalesce(accrual_totals.c.accrued_interest, 0).label("accrued_interest"),
        )
        .outerjoin(active_agreement_totals, active_agreement_totals.c.investor_id == Investor.id)
        .outerjoin(accrual_totals, accrual_totals.c.investor_id == Investor.id)
    )

    if status in {"ACTIVE", "INACTIVE"}:
        query = query.filter(db.func.upper(Investor.status) == status)
    elif status != "ALL":
        return jsonify({"error": "invalid_status", "message": "status must be ACTIVE, INACTIVE, or ALL."}), 422

    if search:
        pattern = f"%{search.lower()}%"
        query = query.filter(
            or_(
                db.func.lower(Investor.investor_number).like(pattern),
                db.func.lower(Investor.full_name).like(pattern),
                db.func.lower(Investor.company_name).like(pattern),
                db.func.lower(Investor.nic).like(pattern),
                db.func.lower(Investor.mobile).like(pattern),
            )
        )

    rows = query.order_by(Investor.id.desc()).all()
    items = [
        investor_summary_dict(
            investor,
            {
                "active_agreements": active_agreements,
                "principal_balance": principal_balance,
                "accrued_interest": accrued_interest,
            },
        )
        for investor, active_agreements, principal_balance, accrued_interest in rows
    ]
    return jsonify({"items": items, "total": len(items)})


@investors_bp.route("/investors/options", methods=["GET"], strict_slashes=False)
@role_required(["admin"])
def investor_options():
    investors = Investor.query.order_by(Investor.investor_number.asc(), Investor.id.asc()).all()
    return jsonify({"items":[investor_option_dict(i) for i in investors if investor_is_active(i)]})


@investors_bp.route("/investors", methods=["POST"], strict_slashes=False)
@role_required(["admin"])
def post_investor():
    payload = request.get_json(silent=True) or {}
    current_app.logger.info(
        "Investor request method=%s path=%s payload_keys=%s",
        request.method,
        request.path,
        sorted(payload.keys()),
    )
    try:
        i=create_investor(payload, uid()); db.session.commit(); return jsonify(inv_dict(i, include_sensitive=True)),201
    except Exception as e: return error(e)
@investors_bp.route("/investors/<int:iid>", methods=["GET"], strict_slashes=False)
@role_required(["admin"])
def get_investor(iid):
    i = db.session.get(Investor, iid)
    if not i: return investor_not_found()
    return jsonify(inv_dict(i, include_sensitive=True))
@investors_bp.route("/investors/<int:iid>", methods=["PATCH"])
@role_required(["admin"])
def patch_investor(iid):
    i=db.session.get(Investor, iid)
    if not i: return investor_not_found()
    data=request.get_json() or {}
    for f in ["full_name","company_name","mobile","email","address","notes","status"]:
        if f in data: setattr(i,f,data[f])
    db.session.commit(); return jsonify(inv_dict(i))
@investors_bp.route("/investors/<int:iid>/activate", methods=["POST"])
@role_required(["admin"])
def activate_investor(iid):
    i=db.session.get(Investor, iid)
    if not i: return investor_not_found()
    i.status="ACTIVE"; db.session.commit(); return jsonify(inv_dict(i))
@investors_bp.route("/investors/<int:iid>/deactivate", methods=["POST"])
@role_required(["admin"])
def deactivate_investor(iid):
    i=db.session.get(Investor, iid)
    if not i: return investor_not_found()
    i.status="INACTIVE"; db.session.commit(); return jsonify(inv_dict(i))

@investors_bp.route("/investor-agreements/options", methods=["GET"], strict_slashes=False)
@role_required(["admin"])
def agreement_options():
    investor_id = request.args.get("investor_id")
    query = InvestorFundingAgreement.query.join(Investor)
    if investor_id not in (None, ""):
        try:
            investor_id = int(investor_id)
        except (TypeError, ValueError):
            return jsonify({"error":"invalid_investor_id","message":"investor_id must be an integer."}), 422
        query = query.filter(InvestorFundingAgreement.investor_id == investor_id)
    agreements = query.order_by(InvestorFundingAgreement.agreement_number.asc(), InvestorFundingAgreement.id.asc()).all()
    return jsonify({"items":[agreement_option_dict(a) for a in agreements if agreement_is_fundable(a)]})


def agreement_list_query():
    accrued_interest_totals = (
        db.session.query(
            InvestorInterestAccrual.agreement_id.label("agreement_id"),
            db.func.coalesce(
                db.func.sum(
                    InvestorInterestAccrual.net_interest_payable
                    - InvestorInterestAccrual.payment_amount
                    - InvestorInterestAccrual.capitalization_amount
                ),
                0,
            ).label("accrued_interest"),
        )
        .filter(InvestorInterestAccrual.status.in_(["POSTED", "PARTIALLY_PAID"]))
        .group_by(InvestorInterestAccrual.agreement_id)
        .subquery()
    )
    return (
        db.session.query(
            InvestorFundingAgreement,
            db.func.coalesce(accrued_interest_totals.c.accrued_interest, 0).label("accrued_interest"),
        )
        .outerjoin(Investor, InvestorFundingAgreement.investor_id == Investor.id)
        .outerjoin(accrued_interest_totals, accrued_interest_totals.c.agreement_id == InvestorFundingAgreement.id)
    )


def list_agreements():
    status = normalize_status(request.args.get("status") or "ALL")
    investor_id = request.args.get("investor_id")
    search = (request.args.get("q") or "").strip()
    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")

    query = agreement_list_query()

    if status in {"ACTIVE", "DRAFT", "CLOSED"}:
        query = query.filter(db.func.upper(InvestorFundingAgreement.status) == status)
    elif status != "ALL":
        return jsonify({"error": "invalid_status", "message": "status must be ACTIVE, DRAFT, CLOSED, or ALL."}), 422

    if investor_id not in (None, ""):
        try:
            investor_id = int(investor_id)
        except (TypeError, ValueError):
            return jsonify({"error": "invalid_investor_id", "message": "investor_id must be an integer."}), 422
        query = query.filter(InvestorFundingAgreement.investor_id == investor_id)

    if date_from:
        try:
            query = query.filter(InvestorFundingAgreement.agreement_date >= date.fromisoformat(date_from))
        except ValueError:
            return jsonify({"error": "invalid_date_from", "message": "date_from must be YYYY-MM-DD."}), 422
    if date_to:
        try:
            query = query.filter(InvestorFundingAgreement.agreement_date <= date.fromisoformat(date_to))
        except ValueError:
            return jsonify({"error": "invalid_date_to", "message": "date_to must be YYYY-MM-DD."}), 422

    if search:
        pattern = f"%{search.lower()}%"
        query = query.filter(
            or_(
                db.func.lower(InvestorFundingAgreement.agreement_number).like(pattern),
                db.func.lower(InvestorFundingAgreement.agreement_name).like(pattern),
                db.func.lower(Investor.investor_number).like(pattern),
                db.func.lower(Investor.full_name).like(pattern),
                db.func.lower(Investor.company_name).like(pattern),
            )
        )

    rows = query.order_by(InvestorFundingAgreement.id.desc()).all()
    items = [agreement_list_dict(agreement, accrued_interest) for agreement, accrued_interest in rows]
    return jsonify({"items": items, "total": len(items)})


@investors_bp.route("/investor-agreements", methods=["GET","POST"], strict_slashes=False)
@role_required(["admin"])
def agreements():
    if request.method=="GET":
        try:
            return list_agreements()
        except Exception as exc:
            current_app.logger.exception("Unexpected investor agreement list error", exc_info=exc)
            return jsonify({"error": "investor_agreement_list_failed", "message": "Funding agreements could not be listed."}), 500
    payload = request.get_json(silent=True) or {}
    current_app.logger.info(
        "Investor agreement request method=%s path=%s payload_keys=%s",
        request.method,
        request.path,
        sorted(payload.keys()),
    )
    try: a=create_agreement(payload, uid()); db.session.commit(); return jsonify(agr_dict(a)),201
    except Exception as e: return error(e)
@investors_bp.route("/investor-agreements/<int:aid>", methods=["GET","PATCH"], strict_slashes=False)
@role_required(["admin"])
def agreement(aid):
    a=db.session.get(InvestorFundingAgreement, aid)
    if not a: return investor_agreement_not_found()
    if request.method=="PATCH":
        data=request.get_json() or {}
        for f in ["agreement_name","maturity_date","interest_rate","interest_rate_period","calculation_method","status"]:
            if f in data: setattr(a,f,date.fromisoformat(data[f]) if f=="maturity_date" and data[f] else data[f])
        db.session.commit()
    return jsonify(agr_dict(a))
@investors_bp.route("/investor-agreements/<int:aid>/activate", methods=["POST"])
@role_required(["admin"])
def activate_agreement(aid):
    a=InvestorFundingAgreement.query.get_or_404(aid); a.status="ACTIVE"
    catchup=catch_up_investor_interest(aid, date.today(), post=True, requested_by=uid())
    db.session.commit(); body=agr_dict(a); body["interest_catch_up"]=catchup; return jsonify(body)
@investors_bp.route("/investor-agreements/<int:aid>/close", methods=["POST"])
@role_required(["admin"])
def close_agreement(aid):
    a=InvestorFundingAgreement.query.get_or_404(aid)
    if float(a.current_principal_balance or 0) != 0: return jsonify({"message":"Cannot close agreement with principal balance"}),422
    a.status="CLOSED"; db.session.commit(); return jsonify(agr_dict(a))

@investors_bp.route("/investor-agreements/<int:aid>/funding", methods=["POST"])
@role_required(["admin"])
def funding(aid):
    try:
        t=record_funding(aid, request.get_json() or {}, uid())
        db.session.commit()
        catchup=catch_up_investor_interest(aid, date.today(), post=True, requested_by=uid())
        db.session.commit()
        body=tx_dict(t); body["interest_catch_up"]=catchup; return jsonify(body),201
    except Exception as e: return error(e)
@investors_bp.route("/investor-agreements/<int:aid>/principal-repayment", methods=["POST"])
@role_required(["admin"])
def repay(aid):
    try: t=principal_repayment(aid, request.get_json() or {}, uid()); db.session.commit(); return jsonify(tx_dict(t)),201
    except Exception as e: return error(e)
@investors_bp.route("/investor-agreements/<int:aid>/transactions")
@role_required(["admin"])
def txs(aid): return jsonify({"items":[tx_dict(t) for t in InvestorFundingTransaction.query.filter_by(agreement_id=aid).order_by(InvestorFundingTransaction.transaction_date).all()]})

@investors_bp.route("/investor-transactions/<int:tid>", methods=["GET"], strict_slashes=False)
@role_required(["admin"])
def get_transaction(tid):
    tx = db.session.get(InvestorFundingTransaction, tid)
    if not tx: return investor_funding_not_found()
    return jsonify(tx_dict(tx))

@investors_bp.route("/investor-funding/<int:tid>", methods=["GET"], strict_slashes=False)
@role_required(["admin"])
def get_investor_funding(tid):
    tx = db.session.get(InvestorFundingTransaction, tid)
    if not tx: return investor_funding_not_found()
    return jsonify(tx_dict(tx))
@investors_bp.route("/investor-transactions/<int:tid>/reverse", methods=["POST"])
@role_required(["admin"])
def reverse_tx(tid):
    data=request.get_json() or {}
    try: rev=reverse_investor_transaction(tid, date.fromisoformat(data["reversal_date"]), data.get("reason","Investor transaction reversal"), uid()); db.session.commit(); return jsonify({"reversal_journal_id":rev.id}),201
    except Exception as e: return error(e)


@investors_bp.route("/investor-agreements/<int:aid>/interest-catch-up", methods=["POST"])
@role_required(["admin"])
def interest_catch_up(aid):
    data=request.get_json() or {}; as_of=date.fromisoformat(data.get("as_of_date")) if data.get("as_of_date") else date.today()
    post=not bool(data.get("preview_only", False))
    try:
        result=catch_up_investor_interest(aid, as_of, post=post, requested_by=uid(), include_partial=bool(data.get("include_partial", False)))
        if post: db.session.commit()
        else: db.session.rollback()
        return jsonify(result)
    except Exception as e: return error(e)

@investors_bp.route("/investor-agreements/<int:aid>/interest-catch-up/preview", methods=["POST"])
@role_required(["admin"])
def interest_catch_up_preview(aid):
    data=request.get_json() or {}; as_of=date.fromisoformat(data.get("as_of_date")) if data.get("as_of_date") else date.today()
    try:
        result=catch_up_investor_interest(aid, as_of, post=False, requested_by=uid(), include_partial=bool(data.get("include_partial", False)))
        db.session.rollback(); return jsonify(result)
    except Exception as e: return error(e)

@investors_bp.route("/investor-agreements/<int:aid>/interest-preview", methods=["POST"])
@role_required(["admin"])
def preview_interest(aid):
    data=request.get_json() or {}; ps=date.fromisoformat(data["period_start"]); pe=date.fromisoformat(data["period_end"]); c=calculate_investor_interest(aid,ps,pe); return jsonify({k:(v.isoformat() if hasattr(v,"isoformat") else dec(v) if hasattr(v,"quantize") else v) for k,v in c.items() if k!="daily_balances"})
@investors_bp.route("/investor-agreements/<int:aid>/accrue-interest", methods=["POST"])
@role_required(["admin"])
def accrue(aid):
    data=request.get_json() or {}; ps=date.fromisoformat(data["period_start"]); pe=date.fromisoformat(data["period_end"])
    try: a=post_investor_interest_accrual(aid,ps,pe,uid()); db.session.commit(); return jsonify(ac_dict(a)),201
    except Exception as e: return error(e)
@investors_bp.route("/investor-agreements/<int:aid>/interest-accruals")
@role_required(["admin"])
def accruals(aid): return jsonify({"items":[ac_dict(a) for a in InvestorInterestAccrual.query.filter_by(agreement_id=aid).order_by(InvestorInterestAccrual.accrual_period_end).all()]})
@investors_bp.route("/investor-interest-accruals/<int:accrual_id>/pay", methods=["POST"])
@role_required(["admin"])
def pay(accrual_id):
    try: a=pay_interest(accrual_id, request.get_json() or {}, uid()); db.session.commit(); return jsonify(ac_dict(a))
    except Exception as e: return error(e)
@investors_bp.route("/investor-interest-accruals/<int:accrual_id>/capitalize", methods=["POST"])
@role_required(["admin"])
def cap(accrual_id):
    try: a=capitalize_interest(accrual_id, uid()); db.session.commit(); return jsonify(ac_dict(a))
    except Exception as e: return error(e)
@investors_bp.route("/investor-interest-accruals/<int:accrual_id>/reverse", methods=["POST"])
@role_required(["admin"])
def rev_accrual(accrual_id):
    data=request.get_json() or {}
    try: rev=reverse_interest_accrual(accrual_id, date.fromisoformat(data["reversal_date"]), data.get("reason","Investor accrual reversal"), uid()); db.session.commit(); return jsonify({"reversal_journal_id": rev.id if rev else None}),201
    except Exception as e: return error(e)

@investors_bp.route("/reports/investor-funding")
@role_required(["admin"])
def rep_funding(): return jsonify({"items":[tx_dict(t) for t in InvestorFundingTransaction.query.all()]})
@investors_bp.route("/reports/investor-interest")
@role_required(["admin"])
def rep_interest(): return jsonify({"items":[ac_dict(a) for a in InvestorInterestAccrual.query.all()]})
def _optional_int_filter(name):
    raw = request.args.get(name)
    if raw is None or str(raw).strip() == "":
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValidationError("invalid_filter", message=f"{name} must be an integer.")
    if value <= 0:
        raise ValidationError("invalid_filter", message=f"{name} must be a positive integer.")
    return value


def _parse_balance_report_as_of_date(as_of_date):
    raw = (as_of_date or "").strip()
    if not raw:
        raise ValidationError("invalid_as_of_date", message="Enter a valid as-of date.")
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise ValidationError("invalid_as_of_date", message="Enter a valid as-of date.")


def _balance_report_as_of_date():
    return _parse_balance_report_as_of_date(request.args.get("as_of_date"))


def _posted_transaction_query(as_of):
    return (
        InvestorFundingTransaction.query.outerjoin(
            AccountingJournalEntry,
            InvestorFundingTransaction.journal_entry_id == AccountingJournalEntry.id,
        )
        .filter(
            InvestorFundingTransaction.status.in_(["POSTED", "APPROVED"]),
            InvestorFundingTransaction.reversed_at.is_(None),
            InvestorFundingTransaction.transaction_date <= as_of,
            InvestorFundingTransaction.accounting_date <= as_of,
            or_(
                InvestorFundingTransaction.journal_entry_id.is_(None),
                db.and_(
                    AccountingJournalEntry.status == "POSTED",
                    AccountingJournalEntry.reversed_at.is_(None),
                    AccountingJournalEntry.journal_date <= as_of,
                ),
            ),
        )
    )


def _investor_balance_item(agreement, as_of, txs, accruals):
    investor = agreement.investor
    total_funding = Decimal("0.00")
    principal_repayments = Decimal("0.00")
    current_principal = Decimal("0.00")
    last_funding_date = None
    for tx in txs:
        amount = Decimal(tx.amount or 0)
        if tx.transaction_type in INCREASE_TYPES:
            current_principal += amount
            if tx.transaction_type in {"INITIAL_FUNDING", "ADDITIONAL_FUNDING"}:
                total_funding += amount
                if last_funding_date is None or tx.transaction_date > last_funding_date:
                    last_funding_date = tx.transaction_date
        elif tx.transaction_type in DECREASE_TYPES:
            current_principal -= amount
            if tx.transaction_type in {"PRINCIPAL_REPAYMENT", "PRINCIPAL_WITHDRAWAL"}:
                principal_repayments += amount

    accrued_interest = Decimal("0.00")
    interest_paid = Decimal("0.00")
    withholding_tax = Decimal("0.00")
    last_accrual_date = None
    for accrual in accruals:
        gross = Decimal(accrual.gross_interest_amount or 0)
        tax = Decimal(accrual.withholding_tax_amount or 0)
        paid = Decimal(accrual.payment_amount or 0)
        capitalized = Decimal(accrual.capitalization_amount or 0)
        accrued_interest += gross - tax - paid - capitalized
        interest_paid += paid
        withholding_tax += tax
        if last_accrual_date is None or accrual.accrual_period_end > last_accrual_date:
            last_accrual_date = accrual.accrual_period_end

    total_payable = current_principal + accrued_interest
    return {
        "investor_id": investor.id if investor else agreement.investor_id,
        "investor_number": investor.investor_number if investor else None,
        "investor_name": investor_display_name(investor) if investor else None,
        "agreement_id": agreement.id,
        "agreement_number": agreement.agreement_number,
        "agreement_name": agreement.agreement_name,
        "agreement_status": normalize_status(agreement.status),
        "status": normalize_status(agreement.status),
        "original_principal": dec(agreement.original_principal_amount) or 0,
        "total_funding": dec(total_funding) or 0,
        "principal_repayments": dec(principal_repayments) or 0,
        "current_principal": dec(current_principal) or 0,
        "gross_interest_accrued": dec(sum((Decimal(a.gross_interest_amount or 0) for a in accruals), Decimal("0.00"))) or 0,
        "interest_paid": dec(interest_paid) or 0,
        "withholding_tax": dec(withholding_tax) or 0,
        "accrued_interest": dec(accrued_interest) or 0,
        "total_payable": dec(total_payable) or 0,
        "total_amount_payable": dec(total_payable) or 0,
        "last_funding_date": last_funding_date.isoformat() if last_funding_date else None,
        "last_accrual_date": last_accrual_date.isoformat() if last_accrual_date else None,
        "as_of_date": as_of.isoformat(),
    }


def build_investor_balance_report(as_of_date, investor_id=None, agreement_id=None, status=None):
    as_of = _parse_balance_report_as_of_date(as_of_date)

    def parse_optional_int(value, name):
        if value is None or str(value).strip() == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            raise ValidationError("invalid_filter", message=f"{name} must be an integer.")

    investor_id = parse_optional_int(investor_id, "investor_id")
    agreement_id = parse_optional_int(agreement_id, "agreement_id")
    status = normalize_status(status or "ALL") or "ALL"

    if investor_id is not None and db.session.get(Investor, investor_id) is None:
        raise ValidationError("investor_not_found", message="The selected investor was not found.", status_code=404)
    if agreement_id is not None and db.session.get(InvestorFundingAgreement, agreement_id) is None:
        raise ValidationError("agreement_not_found", message="The selected funding agreement was not found.", status_code=404)

    query = InvestorFundingAgreement.query.join(Investor)
    if investor_id is not None:
        query = query.filter(InvestorFundingAgreement.investor_id == investor_id)
    if agreement_id is not None:
        query = query.filter(InvestorFundingAgreement.id == agreement_id)
    if status != "ALL":
        query = query.filter(db.func.upper(InvestorFundingAgreement.status) == status)
    agreements = query.order_by(Investor.investor_number, InvestorFundingAgreement.agreement_number).all()
    agreement_ids = [a.id for a in agreements]

    txs_by_agreement = {aid: [] for aid in agreement_ids}
    accruals_by_agreement = {aid: [] for aid in agreement_ids}
    if agreement_ids:
        for tx in _posted_transaction_query(as_of).filter(InvestorFundingTransaction.agreement_id.in_(agreement_ids)).all():
            txs_by_agreement.setdefault(tx.agreement_id, []).append(tx)
        for accrual in InvestorInterestAccrual.query.filter(
            InvestorInterestAccrual.agreement_id.in_(agreement_ids),
            InvestorInterestAccrual.accrual_period_end <= as_of,
            InvestorInterestAccrual.status.in_(["POSTED", "APPROVED", "PARTIALLY_PAID", "PAID", "CAPITALIZED"]),
            InvestorInterestAccrual.reversed_at.is_(None),
        ).all():
            accruals_by_agreement.setdefault(accrual.agreement_id, []).append(accrual)

    items = [_investor_balance_item(a, as_of, txs_by_agreement.get(a.id, []), accruals_by_agreement.get(a.id, [])) for a in agreements]
    summary = {
        "total_investors": len({item["investor_id"] for item in items}),
        "total_agreements": len(items),
        "original_principal": dec(sum((Decimal(str(item["original_principal"])) for item in items), Decimal("0.00"))) or 0,
        "total_funding": dec(sum((Decimal(str(item["total_funding"])) for item in items), Decimal("0.00"))) or 0,
        "current_principal": dec(sum((Decimal(str(item["current_principal"])) for item in items), Decimal("0.00"))) or 0,
        "accrued_interest": dec(sum((Decimal(str(item["accrued_interest"])) for item in items), Decimal("0.00"))) or 0,
        "total_payable": dec(sum((Decimal(str(item["total_payable"])) for item in items), Decimal("0.00"))) or 0,
    }
    summary["total_principal"] = summary["current_principal"]
    summary["total_accrued_interest"] = summary["accrued_interest"]
    return {
        "as_of_date": as_of.isoformat(),
        "filters": {"investor_id": investor_id, "agreement_id": agreement_id, "status": status},
        "items": items,
        "summary": summary,
    }


@investors_bp.route("/investor-funding/reports/balances", methods=["GET"], strict_slashes=False)
@role_required(["admin"])
def investor_balance_report():
    try:
        result = build_investor_balance_report(
            as_of_date=request.args.get("as_of_date"),
            investor_id=request.args.get("investor_id"),
            agreement_id=request.args.get("agreement_id"),
            status=request.args.get("status"),
        )
    except ValidationError as exc:
        return error(exc)
    return jsonify(result), 200


@investors_bp.route("/reports/investor-balances")
@role_required(["admin"])
def rep_balances():
    try:
        return jsonify(build_investor_balance_report(
            as_of_date=request.args.get("as_of_date"),
            investor_id=request.args.get("investor_id"),
            agreement_id=request.args.get("agreement_id"),
            status=request.args.get("status"),
        ))
    except ValidationError as exc:
        return error(exc)
@investors_bp.route("/reports/investor-reconciliation")
@role_required(["admin"])
def rep_recon(): return jsonify(investor_reconciliation())
