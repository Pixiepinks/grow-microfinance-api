from datetime import date
from decimal import Decimal

from flask_jwt_extended import create_access_token

from app.extensions import db
from app.models import (AccountingAccount, AccountingJournalEntry, AccountingJournalLine,
                        BankReconciliation, BankReconciliationAudit, User)


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


def test_create_route_registration_and_production_compatibility_alias(app, client):
    rules = {(rule.rule, method) for rule in app.url_map.iter_rules() for method in rule.methods}
    assert ("/admin/accounting/bank-reconciliations", "POST") in rules
    assert ("/admin/accounting/bank-reconciliations", "GET") in rules
    assert ("/admin/accounting/bank-reconciliations/<int:rec_id>", "GET") in rules
    assert ("/admin/accounting/bank-reconciliations/<int:rec_id>/lines", "POST") in rules
    assert ("/admin/accounting/bank-reconciliations/<int:rec_id>/lines/<int:line_id>", "DELETE") in rules
    assert ("/admin/accounting/bank-reconciliations/<int:rec_id>/complete", "POST") in rules
    assert ("/admin/accounting/bank-reconciliations/<int:rec_id>/reopen", "POST") in rules
    assert ("/admin/bank-reconciliations", "POST") in rules
    assert ("/bank-reconciliations", "POST") in rules
    assert ("/admin/bank-reconciliations", "GET") in rules
    assert ("/admin/bank-reconciliations/<int:rec_id>", "GET") in rules
    assert ("/admin/bank-reconciliations/<int:rec_id>/lines", "POST") in rules
    assert ("/admin/bank-reconciliations/<int:rec_id>/complete", "POST") in rules

    _, headers = _headers(app)
    bank = AccountingAccount(account_code="1010", account_name="Bank", account_type="ASSET",
        normal_balance="DEBIT", account_subtype="BANK", is_active=True, allow_manual_posting=True)
    db.session.add(bank); db.session.commit()
    journal_count = AccountingJournalEntry.query.count()
    response = client.post("/admin/accounting/bank-reconciliations", headers=headers, json={"bank_account_id": bank.id,
        "statement_date_from": "2026-01-01", "statement_date_to": "2026-02-28",
        "statement_opening_balance": "0.00", "statement_closing_balance": "81565.54", "notes": "Draft"})
    assert response.status_code == 201
    assert response.get_json()["success"] is True
    assert response.get_json()["reconciliation_number"] == "BR-20260228-0001"
    assert response.get_json()["status"] == "DRAFT"
    assert BankReconciliation.query.count() == 1
    assert AccountingJournalEntry.query.count() == journal_count

    # Cross-origin POST callers must not rely on Flask's slash redirect. Both
    # deployed and canonical create paths accept either spelling directly.
    slash_response = client.post("/admin/accounting/bank-reconciliations/", headers=headers, json={
        "bank_account_id": bank.id, "statement_date_from": "2026-03-01",
        "statement_date_to": "2026-03-31", "statement_opening_balance": "0.00",
        "statement_closing_balance": "0.00"})
    assert slash_response.status_code == 201
    assert client.options("/admin/accounting/bank-reconciliations").status_code == 204
    assert client.options("/admin/accounting/bank-reconciliations/").status_code == 204


def test_create_validation_and_authorization(app, client):
    _, headers = _headers(app)
    bank = AccountingAccount(account_code="1010", account_name="Bank", account_type="ASSET",
        normal_balance="DEBIT", account_subtype="BANK", is_active=True, allow_manual_posting=True)
    expense = AccountingAccount(account_code="5000", account_name="Expense", account_type="EXPENSE",
        normal_balance="DEBIT", account_subtype="OPERATING_EXPENSE")
    db.session.add_all([bank, expense]); db.session.commit()
    payload = {"bank_account_id": bank.id, "statement_date_from": "2026-01-01",
        "statement_date_to": "2026-02-28", "statement_opening_balance": "0", "statement_closing_balance": "1"}
    route = "/admin/accounting/bank-reconciliations"
    assert client.post(route, json=payload).status_code == 401
    staff = User(email="staff@example.com", name="Staff", role="staff")
    staff.set_password("password"); db.session.add(staff); db.session.commit()
    with app.app_context():
        staff_token = create_access_token(identity=str(staff.id), additional_claims={"role": "staff"})
    assert client.post(route, headers={"Authorization": f"Bearer {staff_token}"},
        json=payload).status_code == 403
    missing = dict(payload); missing.pop("statement_date_from")
    assert client.post(route, headers=headers, json=missing).status_code == 422
    missing_closing = dict(payload); missing_closing.pop("statement_closing_balance")
    assert client.post(route, headers=headers, json=missing_closing).status_code == 422
    invalid_range = dict(payload, statement_date_from="2026-03-01")
    assert client.post(route, headers=headers, json=invalid_range).status_code == 422
    invalid = dict(payload, bank_account_id=999999)
    assert client.post(route, headers=headers, json=invalid).status_code == 422
    non_bank = dict(payload, bank_account_id=expense.id)
    assert client.post(route, headers=headers, json=non_bank).status_code == 422
    bank.is_active = False; db.session.commit()
    assert client.post(route, headers=headers, json=payload).status_code == 422
