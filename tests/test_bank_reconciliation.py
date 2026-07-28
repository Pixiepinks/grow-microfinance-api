from datetime import date
from decimal import Decimal

from flask_jwt_extended import create_access_token

from app.extensions import db
from app.models import (AccountingAccount, AccountingJournalEntry, AccountingJournalLine,
                        BankReconciliationAudit, User)


def _headers(app):
    user = User(email="reconciler@example.com", name="Reconciler", role="admin")
    user.set_password("password"); db.session.add(user); db.session.commit()
    with app.app_context():
        token = create_access_token(identity=str(user.id), additional_claims={"role": "admin"})
    return user, {"Authorization": f"Bearer {token}"}


def _journal(bank, other):
    entry = AccountingJournalEntry(journal_no="J-BANK-1", journal_date=date(2026, 2, 10),
        description="Collector deposit", status="POSTED", total_debit=Decimal("2100"), total_credit=Decimal("2100"))
    entry.lines = [AccountingJournalLine(line_no=1, account=bank, debit=Decimal("2100"), credit=Decimal("0")),
                   AccountingJournalLine(line_no=2, account=other, debit=Decimal("0"), credit=Decimal("2100"))]
    db.session.add(entry); db.session.commit(); return entry


def test_reconcile_only_bank_line_complete_gl_and_reopen_audit(app, client):
    user, headers = _headers(app)
    bank = AccountingAccount(account_code="1010", account_name="NDB Current Account", account_type="ASSET", normal_balance="DEBIT", account_subtype="BANK")
    clearing = AccountingAccount(account_code="1050", account_name="Collector Clearing", account_type="ASSET", normal_balance="DEBIT", account_subtype="COLLECTION_CLEARING")
    db.session.add_all([bank, clearing]); db.session.commit(); entry = _journal(bank, clearing)
    created = client.post("/admin/bank-reconciliations", headers=headers, json={"bank_account_id": bank.id,
        "statement_date_from": "2026-02-01", "statement_date_to": "2026-02-28",
        "statement_opening_balance": "0.00", "statement_closing_balance": "2100.00"})
    assert created.status_code == 201
    rec_id = created.get_json()["id"]
    added = client.post(f"/admin/bank-reconciliations/{rec_id}/lines", headers=headers,
                        json={"journal_line_id": entry.lines[0].id, "bank_statement_reference": "NDB-42"})
    assert added.status_code == 200
    assert entry.lines[0].is_reconciled is True
    assert entry.lines[1].is_reconciled is False and entry.lines[1].bank_reconciliation_id is None
    duplicate = client.post("/admin/bank-reconciliations", headers=headers, json={"bank_account_id": bank.id,
        "statement_date_from": "2026-02-01", "statement_date_to": "2026-02-28",
        "statement_opening_balance": "0", "statement_closing_balance": "2100"}).get_json()["id"]
    assert client.post(f"/admin/bank-reconciliations/{duplicate}/lines", headers=headers,
                       json={"journal_line_id": entry.lines[0].id}).status_code == 422
    assert client.post(f"/admin/bank-reconciliations/{rec_id}/complete", headers=headers).status_code == 200
    ledger = client.get(f"/admin/accounting/general-ledger?account_id={bank.id}", headers=headers).get_json()
    assert ledger["transactions"][0]["is_reconciled"] is True
    assert ledger["transactions"][0]["reconciliation_number"].startswith("BR-")
    reopened = client.post(f"/admin/bank-reconciliations/{rec_id}/reopen", headers=headers, json={"reason": "Statement correction"})
    assert reopened.status_code == 200 and reopened.get_json()["status"] == "REOPENED"
    assert entry.lines[0].is_reconciled is False
    assert {audit.action for audit in BankReconciliationAudit.query.all()} >= {"CREATED", "LINE_ADDED", "COMPLETED", "REOPENED"}


def test_completion_rejects_difference_and_non_bank_account(app, client):
    _, headers = _headers(app)
    expense = AccountingAccount(account_code="5000", account_name="Expense", account_type="EXPENSE", normal_balance="DEBIT", account_subtype="OPERATING_EXPENSE")
    db.session.add(expense); db.session.commit()
    response = client.post("/admin/bank-reconciliations", headers=headers, json={"bank_account_id": expense.id,
        "statement_date_from": "2026-02-01", "statement_date_to": "2026-02-28",
        "statement_opening_balance": "0", "statement_closing_balance": "1"})
    assert response.status_code == 422
    bank = AccountingAccount(account_code="1010", account_name="Bank", account_type="ASSET", normal_balance="DEBIT", account_subtype="BANK")
    db.session.add(bank); db.session.commit()
    rec_id = client.post("/admin/bank-reconciliations", headers=headers, json={"bank_account_id": bank.id,
        "statement_date_from": "2026-02-01", "statement_date_to": "2026-02-28",
        "statement_opening_balance": "0", "statement_closing_balance": "1"}).get_json()["id"]
    incomplete = client.post(f"/admin/bank-reconciliations/{rec_id}/complete", headers=headers)
    assert incomplete.status_code == 422
    assert incomplete.get_json()["message"].endswith("1.00)")
