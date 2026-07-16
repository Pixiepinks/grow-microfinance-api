from datetime import date
from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import get_jwt_identity
from ..extensions import db
from ..models import Investor, InvestorFundingAgreement, InvestorFundingTransaction, InvestorInterestAccrual
from ..investor_funding import create_investor, create_agreement, record_funding, principal_repayment, calculate_investor_interest, post_investor_interest_accrual, pay_interest, capitalize_interest, month_bounds, completed_periods_for, reverse_investor_transaction, investor_reconciliation, reverse_interest_accrual
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


def normalize_investor_status(investor):
    return str(investor.status or "").strip().upper()


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
    return normalize_investor_status(investor) == "ACTIVE"


def inv_dict(i, include_sensitive=False):
    display_name = investor_display_name(i)
    data = {"id":i.id,"investor_id":i.id,"investor_number":i.investor_number,"investor_type":i.investor_type,"full_name":i.full_name,"company_name":i.company_name,"display_name":display_name,"nic":i.nic,"company_registration_number":i.company_registration_number,"tax_identification_number":i.tax_identification_number,"mobile":i.mobile,"email":i.email,"address":i.address,"bank_name":i.bank_name,"bank_branch":i.bank_branch,"bank_account_name":i.bank_account_name,"bank_account_number":i.bank_account_number if include_sensitive else mask_bank_account(i.bank_account_number),"notes":i.notes,"status":normalize_investor_status(i),"created_at":i.created_at.isoformat() if i.created_at else None}
    return data


def investor_option_dict(i):
    display_name = investor_display_name(i)
    return {"id":i.id,"investor_id":i.id,"investor_number":i.investor_number,"investor_type":i.investor_type,"display_name":display_name,"full_name":i.full_name,"company_name":i.company_name,"nic":i.nic,"status":normalize_investor_status(i),"label":f"{i.investor_number} — {display_name}"}

def agr_dict(a):
    posted = [x for x in a.interest_accruals if x.status in {"POSTED","PARTIALLY_PAID","PAID","CAPITALIZED"}]
    unpaid = sum((x.net_interest_payable - x.payment_amount - x.capitalization_amount for x in posted), 0)
    paid = sum((x.payment_amount for x in posted), 0); cap = sum((x.capitalization_amount for x in posted), 0)
    last = max([x.accrual_period_end for x in posted], default=None)
    return {"id":a.id,"agreement_id":a.id,"agreement_number":a.agreement_number,"investor_id":a.investor_id,"investor":inv_dict(a.investor),"agreement_name":a.agreement_name,"agreement_date":a.agreement_date.isoformat() if a.agreement_date else None,"start_date":a.start_date.isoformat() if a.start_date else None,"original_principal":dec(a.original_principal_amount),"original_principal_amount":dec(a.original_principal_amount),"current_principal":dec(a.current_principal_balance),"current_principal_balance":dec(a.current_principal_balance),"interest_rate":dec(a.interest_rate),"interest_rate_period":a.interest_rate_period,"interest_rate_label":f"{a.interest_rate:.2f}% per {a.interest_rate_period.lower()}","calculation_method":a.calculation_method,"accrued_unpaid_interest":dec(unpaid),"interest_paid":dec(paid),"capitalized_interest":dec(cap),"maturity_date":a.maturity_date.isoformat() if a.maturity_date else None,"next_accrual_date":None,"last_accrued_through":last.isoformat() if last else None,"status":a.status,"created_at":a.created_at.isoformat() if a.created_at else None,"account_mappings":{"funding_account_id":a.funding_account_id,"investor_liability_account_id":a.investor_liability_account_id,"interest_expense_account_id":a.interest_expense_account_id,"accrued_interest_payable_account_id":a.accrued_interest_payable_account_id,"withholding_tax_account_id":a.withholding_tax_account_id},"warnings":[],"reconciliation_status":"OK"}

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
    items = [inv_dict(i) for i in Investor.query.order_by(Investor.id.desc()).all()]
    return jsonify({"items":items,"total":len(items)})


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

@investors_bp.route("/investor-agreements", methods=["GET","POST"], strict_slashes=False)
@role_required(["admin"])
def agreements():
    if request.method=="GET": return jsonify({"items":[agr_dict(a) for a in InvestorFundingAgreement.query.order_by(InvestorFundingAgreement.id.desc()).all()]})
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
def activate_agreement(aid): a=InvestorFundingAgreement.query.get_or_404(aid); a.status="ACTIVE"; db.session.commit(); return jsonify(agr_dict(a))
@investors_bp.route("/investor-agreements/<int:aid>/close", methods=["POST"])
@role_required(["admin"])
def close_agreement(aid):
    a=InvestorFundingAgreement.query.get_or_404(aid)
    if float(a.current_principal_balance or 0) != 0: return jsonify({"message":"Cannot close agreement with principal balance"}),422
    a.status="CLOSED"; db.session.commit(); return jsonify(agr_dict(a))

@investors_bp.route("/investor-agreements/<int:aid>/funding", methods=["POST"])
@role_required(["admin"])
def funding(aid):
    try: t=record_funding(aid, request.get_json() or {}, uid()); db.session.commit(); return jsonify(tx_dict(t)),201
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
@investors_bp.route("/reports/investor-balances")
@role_required(["admin"])
def rep_balances(): return jsonify({"items":[agr_dict(a) for a in InvestorFundingAgreement.query.all()]})
@investors_bp.route("/reports/investor-reconciliation")
@role_required(["admin"])
def rep_recon(): return jsonify(investor_reconciliation())
