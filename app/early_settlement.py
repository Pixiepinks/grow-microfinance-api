"""Approved early-loan-settlement concessions (never customer receipts)."""
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from .extensions import db
from .models import Loan, LoanEarlySettlement, AccountingAccount, AccountingSetting
from .accounting import create_draft_journal, post_journal, reverse_journal, resolve_system_account, log_audit

CENT = Decimal("0.01")
def money(value): return Decimal(str(value or 0)).quantize(CENT, rounding=ROUND_HALF_UP)

class EarlySettlementError(ValueError):
    def __init__(self, error, message=None): self.error, self.message = error, message or error; super().__init__(self.message)

def _remaining(entry, amount, paid): return max(Decimal("0"), money(amount) - money(paid))
def _balances(loan):
    rows = list(loan.ledger_entries)
    principal = sum((_remaining(x, x.principal_amount, x.principal_paid) for x in rows), Decimal())
    accrued = sum((_remaining(x, x.interest_amount, x.interest_paid) for x in rows if x.interest_accrued), Decimal())
    future = sum((_remaining(x, x.interest_amount, x.interest_paid) for x in rows if not x.interest_accrued), Decimal())
    delay = sum((_remaining(x, x.delay_interest_accrued, x.delay_interest_paid) for x in rows), Decimal())
    # Legacy ledger rows can lack allocation data.  Infer cash only where no
    # component allocation was ever recorded, using the contractual waterfall.
    if rows and all(money(x.principal_paid) == money(x.interest_paid) == 0 for x in rows):
        cash = money(loan.total_paid)
        principal_paid = min(principal, cash); principal -= principal_paid; cash -= principal_paid
        accrued_paid = min(accrued, cash); accrued -= accrued_paid; cash -= accrued_paid
        future -= min(future, cash)
    return {"principal_outstanding": money(principal), "accrued_interest_outstanding": money(accrued),
            "future_unearned_interest": money(future), "penalty_outstanding": Decimal("0.00"),
            "delay_interest_outstanding": money(delay), "fee_outstanding": Decimal("0.00")}

def preview_early_loan_settlement(loan_id, settlement_date, requested_interest_rebate=None, requested_penalty_waiver=None):
    loan = Loan.query.get(loan_id)
    if not loan: raise LookupError("loan_not_found")
    if (loan.status or "").upper() == "SETTLED": raise EarlySettlementError("loan_already_settled", "A settled loan cannot receive an early-settlement concession.")
    b = _balances(loan); rebate = money(requested_interest_rebate); penalty = money(requested_penalty_waiver)
    if rebate < 0 or penalty < 0: raise EarlySettlementError("invalid_rebate", "Rebates and waivers must not be negative.")
    maximum = money(b["accrued_interest_outstanding"] + b["future_unearned_interest"])
    if rebate > maximum: raise EarlySettlementError("interest_rebate_exceeds_eligible_interest", "Interest rebate cannot exceed eligible unpaid interest.")
    if penalty > b["penalty_outstanding"]: raise EarlySettlementError("penalty_waiver_exceeds_outstanding", "Penalty waiver cannot exceed penalty outstanding.")
    future_rebate = min(rebate, b["future_unearned_interest"]); accrued_rebate = rebate - future_rebate
    final = money(sum(b.values()) - rebate - penalty)
    if final < 0: raise EarlySettlementError("negative_settlement_amount", "Final settlement amount cannot be negative.")
    journal_preview=[]
    if accrued_rebate:
        setting=AccountingSetting.query.filter_by(setting_key="interest_rebate_expense_account_id").first()
        expense=AccountingAccount.query.get(int(setting.setting_value)) if setting and str(setting.setting_value).isdigit() else None
        receivable=resolve_system_account("INTEREST_RECEIVABLE_ACCOUNT")
        journal_preview=[{"debit_account": expense.account_name if expense else "Interest Rebate / Loan Concession Expense", "credit_account": receivable.account_name, "amount": float(accrued_rebate)}]
    return {"loan_id":loan.id,"loan_number":loan.loan_number,"customer_id":loan.customer_id,"current_status":loan.status,"proposed_status":"SETTLED" if final == 0 else loan.status,"original_principal":money(loan.principal_amount),"original_total_payable":money(loan.total_payable),"total_paid":money(loan.total_paid),**b,"maximum_interest_rebate":maximum,"requested_interest_rebate":rebate,"future_interest_rebate":future_rebate,"accrued_interest_rebate":accrued_rebate,"approved_penalty_waiver":penalty,"final_settlement_amount":final,"customer_credit":Decimal("0.00"),"settlement_date":settlement_date,"journal_preview":journal_preview,"warnings":([] if final == 0 else ["Final cash payment is required before the loan can be settled."])}

def _rebate_account():
    setting=AccountingSetting.query.filter_by(setting_key="interest_rebate_expense_account_id").first()
    account=AccountingAccount.query.get(int(setting.setting_value)) if setting and str(setting.setting_value).isdigit() else None
    if not account or not account.is_active or not account.allow_manual_posting or account.account_type not in {"EXPENSE", "INCOME"}:
        raise EarlySettlementError("interest_rebate_account_missing", "Configure the Interest Rebate / Loan Concession account before posting early settlement.")
    return account

def post_early_loan_settlement(loan_id, settlement_date, approved_interest_rebate, approved_penalty_waiver=0, approval_reference=None, reason=None, requested_by=None):
    preview=preview_early_loan_settlement(loan_id, settlement_date, approved_interest_rebate, approved_penalty_waiver)
    loan=Loan.query.get(loan_id)
    if LoanEarlySettlement.query.filter_by(loan_id=loan.id, status="POSTED").first(): raise EarlySettlementError("early_settlement_already_posted", "An early settlement is already posted for this loan.")
    if preview["final_settlement_amount"] > 0: return {**preview,"posted":False,"message":"Final cash payment is required before the loan can be settled."}
    expense = _rebate_account() if preview["accrued_interest_rebate"] else None
    s=LoanEarlySettlement(settlement_number=f"GROW-ES-{loan.id:08d}-{LoanEarlySettlement.query.count()+1:04d}",loan_id=loan.id,customer_id=loan.customer_id,settlement_date=settlement_date,requested_at=datetime.utcnow(),requested_by_id=requested_by,approved_at=datetime.utcnow(),approved_by_id=requested_by,original_principal=loan.principal_amount,original_total_payable=loan.total_payable,total_paid_before_settlement=loan.total_paid,principal_outstanding_before=preview['principal_outstanding'],accrued_interest_outstanding_before=preview['accrued_interest_outstanding'],future_unearned_interest_before=preview['future_unearned_interest'],penalty_outstanding_before=preview['penalty_outstanding'],delay_interest_outstanding_before=preview['delay_interest_outstanding'],fee_outstanding_before=preview['fee_outstanding'],approved_interest_rebate=approved_interest_rebate,future_interest_rebate=preview['future_interest_rebate'],accrued_interest_rebate=preview['accrued_interest_rebate'],approved_penalty_waiver=approved_penalty_waiver,final_settlement_amount=preview['final_settlement_amount'],approval_reference=approval_reference,reason=reason,status='POSTED')
    db.session.add(s); db.session.flush()
    remaining=preview['future_interest_rebate']
    for row in loan.ledger_entries:
        if remaining <= 0 or row.interest_accrued: continue
        waiver=min(remaining, _remaining(row,row.interest_amount,row.interest_paid)); row.waived_interest_amount=money(waiver); row.original_interest_amount=row.interest_amount; row.revised_interest_amount=money(row.interest_amount-waiver); row.waiver_reason='EARLY_SETTLEMENT'; row.early_settlement_id=s.id; row.status='WAIVED' if waiver else row.status; remaining-=waiver
    if expense:
        receivable=resolve_system_account('INTEREST_RECEIVABLE_ACCOUNT')
        j=create_draft_journal(settlement_date, 'Early settlement accrued-interest rebate',[{'account_id':expense.id,'debit':preview['accrued_interest_rebate'],'loan_id':loan.id,'customer_id':loan.customer_id},{'account_id':receivable.id,'credit':preview['accrued_interest_rebate'],'loan_id':loan.id,'customer_id':loan.customer_id}], 'EARLY_SETTLEMENT_REBATE',s.id,'LOANS',requested_by,f'EARLY_SETTLEMENT_REBATE:{s.id}')
        post_journal(j,requested_by); s.rebate_journal_entry_id=j.id
    loan.status='SETTLED'; loan.settled_date=settlement_date; loan.settled_at=datetime.utcnow(); loan.settled_by_id=requested_by; loan.settlement_reason='EARLY_SETTLEMENT'; loan.settlement_type='EARLY_SETTLEMENT'; loan.early_settlement_id=s.id; loan.interest_rebate_amount=money(approved_interest_rebate); loan.penalty_waiver_amount=money(approved_penalty_waiver); loan.outstanding_amount=Decimal('0.00'); loan.accrual_processed_through=settlement_date
    log_audit('EARLY_LOAN_SETTLEMENT_POSTED','LoanEarlySettlement',s.id,requested_by,{'loan_id':loan.id,'rebate':str(approved_interest_rebate)})
    return {**preview,'posted':True,'settlement_id':s.id,'settlement_number':s.settlement_number}

def reverse_early_loan_settlement(loan_id, settlement_id, user_id=None, reason=None):
    s=LoanEarlySettlement.query.filter_by(id=settlement_id,loan_id=loan_id).first()
    if not s or s.status != 'POSTED': raise EarlySettlementError('settlement_not_reversible','Only a posted early settlement can be reversed.')
    if s.rebate_journal_entry_id: reverse_journal(s.rebate_journal_entry, datetime.utcnow().date(), reason or 'Early settlement reversal', user_id)
    for row in s.loan.ledger_entries:
        if row.early_settlement_id == s.id: row.interest_amount=row.original_interest_amount or row.interest_amount; row.waived_interest_amount=Decimal('0.00'); row.revised_interest_amount=None; row.waiver_reason=None; row.early_settlement_id=None; row.status='PENDING'
    loan=s.loan; loan.status='ACTIVE'; loan.settled_date=loan.settled_at=loan.settled_by_id=None; loan.settlement_reason=loan.settlement_type=None; loan.early_settlement_id=None; loan.interest_rebate_amount=loan.penalty_waiver_amount=Decimal('0.00'); loan.outstanding_amount=None; s.status='REVERSED'; log_audit('EARLY_LOAN_SETTLEMENT_REVERSED','LoanEarlySettlement',s.id,user_id,{'reason':reason}); return s
