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


def _journal(bank, other, journal_no="J-BANK-1"):
    entry = AccountingJournalEntry(journal_no=journal_no, journal_date=date(2026, 2, 10),
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
    assert ("/admin/accounting/bank-reconciliations/<int:rec_id>/transactions", "GET") in rules
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


def test_reconciliation_transactions_reuse_gl_lines_and_calculate_summary(app, client):
    _, headers = _headers(app)
    bank = AccountingAccount(account_code="1010", account_name="NDB Current Account",
        account_type="ASSET", normal_balance="DEBIT", account_subtype="BANK")
    other = AccountingAccount(account_code="1050", account_name="Clearing",
        account_type="ASSET", normal_balance="DEBIT", account_subtype="COLLECTION_CLEARING")
    db.session.add_all([bank, other]); db.session.flush()

    amounts = [("Investor funding", date(2026, 1, 1), "Posted", "30000", "0"),
               ("Loan disbursement", date(2026, 1, 31), "POSTED", "0", "10000"),
               ("Collector deposit", date(2026, 2, 15), "posted", "5000", "0"),
               ("Bank charge", date(2026, 2, 28), "REVERSED", "0", "250")]
    bank_lines = []
    for index, (description, journal_date, status, debit, credit) in enumerate(amounts, 1):
        amount = Decimal(debit) or Decimal(credit)
        entry = AccountingJournalEntry(journal_no=f"J-BANK-{index}", journal_date=journal_date,
            description=description, reference=f"REF-{index}", status=status,
            total_debit=amount, total_credit=amount)
        bank_line = AccountingJournalLine(line_no=1, account=bank,
            debit=Decimal(debit), credit=Decimal(credit))
        entry.lines = [bank_line, AccountingJournalLine(line_no=2, account=other,
            debit=Decimal(credit), credit=Decimal(debit))]
        db.session.add(entry); bank_lines.append(bank_line)
    db.session.flush()
    rec = BankReconciliation(reconciliation_number="BR-20260228-0001", bank_account_id=bank.id,
        statement_date_from=date(2026, 1, 1), statement_date_to=date(2026, 2, 28),
        statement_opening_balance=Decimal("0"), statement_closing_balance=Decimal("24750"))
    db.session.add(rec); db.session.commit()

    response = client.get(f"/admin/accounting/bank-reconciliations/{rec.id}/transactions",
                          headers=headers)
    assert response.status_code == 200
    body = response.get_json()
    gl = client.get(f"/admin/accounting/general-ledger?account_id={bank.id}"
                    "&date_from=2026-01-01&date_to=2026-02-28", headers=headers).get_json()
    assert [row["journal_line_id"] for row in body["transactions"]] == [
        row["journal_line_id"] for row in gl["transactions"]]
    assert {row["journal_line_id"] for row in body["transactions"]} == {line.id for line in bank_lines}
    assert body["transactions"][-1]["posting_date"] == "2026-02-28"
    assert body["summary"] == {"gl_opening_balance": "0.00", "gl_closing_balance": "24750.00",
        "gl_balance": "24750.00", "reconciled_debits": "0.00",
        "reconciled_credits": "0.00", "unreconciled_debits": "35000.00",
        "unreconciled_credits": "10250.00", "difference": "0.00"}
    assert body["reconciliation"]["bank_account_code"] == "1010"


def test_reconciliation_visibility_excludes_other_completed_and_keeps_current_draft(app, client):
    _, headers = _headers(app)
    bank = AccountingAccount(account_code="1010", account_name="Bank", account_type="ASSET",
        normal_balance="DEBIT", account_subtype="BANK")
    other = AccountingAccount(account_code="1050", account_name="Other", account_type="ASSET",
        normal_balance="DEBIT", account_subtype="OTHER")
    db.session.add_all([bank, other]); db.session.commit()
    entries = [_journal(bank, other, "J-BANK-1"), _journal(bank, other, "J-BANK-2")]
    current = BankReconciliation(reconciliation_number="BR-CURRENT", bank_account_id=bank.id,
        statement_date_from=date(2026, 2, 1), statement_date_to=date(2026, 2, 28),
        statement_opening_balance=0, statement_closing_balance=4200)
    completed = BankReconciliation(reconciliation_number="BR-COMPLETE", bank_account_id=bank.id,
        statement_date_from=date(2026, 2, 1), statement_date_to=date(2026, 2, 28),
        statement_opening_balance=0, statement_closing_balance=2100, status="COMPLETED")
    db.session.add_all([current, completed]); db.session.flush()
    entries[0].lines[0].is_reconciled = True
    entries[0].lines[0].bank_reconciliation_id = current.id
    entries[1].lines[0].is_reconciled = True
    entries[1].lines[0].bank_reconciliation_id = completed.id
    db.session.commit()
    body = client.get(f"/admin/accounting/bank-reconciliations/{current.id}/transactions",
                      headers=headers).get_json()
    assert [row["journal_line_id"] for row in body["transactions"]] == [entries[0].lines[0].id]
    assert body["transactions"][0]["is_reconciled"] is True


def test_create_accepts_explicit_account_code_and_persists_database_id(app, client):
    _, headers = _headers(app)
    bank = AccountingAccount(account_code="1010", account_name="Bank", account_type="ASSET",
        normal_balance="DEBIT", account_subtype="BANK")
    db.session.add(bank); db.session.commit()
    response = client.post("/admin/accounting/bank-reconciliations", headers=headers, json={
        "bank_account_id": "1010", "statement_date_from": "2026-01-01",
        "statement_date_to": "2026-02-28", "statement_opening_balance": "0",
        "statement_closing_balance": "0"})
    assert response.status_code == 201
    assert response.get_json()["bank_account_id"] == bank.id
    assert BankReconciliation.query.one().bank_account_id == bank.id


def test_serializer_account_shape_invalid_contract_and_idempotent_repair(app, client):
    _, headers = _headers(app)
    bank = AccountingAccount(account_code="1010", account_name="NDB Current Account",
        account_type="ASSET", normal_balance="DEBIT", account_subtype="BANK")
    db.session.add(bank); db.session.commit()
    payload = {"bank_account_id": {"code": "1010"}, "statement_date_from": "2026-01-01",
        "statement_date_to": "2026-02-28", "statement_opening_balance": "0",
        "statement_closing_balance": "0"}
    created = client.post("/admin/accounting/bank-reconciliations", headers=headers, json=payload)
    assert created.status_code == 201
    body = client.get(f"/admin/accounting/bank-reconciliations/{created.get_json()['id']}",
                      headers=headers).get_json()
    assert body["bank_account"] == {"id": bank.id, "code": "1010", "name": "NDB Current Account"}
    before = BankReconciliation.query.count()
    invalid = dict(payload, bank_account_id={"code": "missing"})
    response = client.post("/admin/accounting/bank-reconciliations", headers=headers, json=invalid)
    assert response.status_code == 422
    assert response.get_json()["error"] == "invalid_bank_account"
    assert response.get_json()["message"] == "A valid accounting bank account is required."
    assert BankReconciliation.query.count() == before

    runner = app.test_cli_runner()
    journal_count = AccountingJournalEntry.query.count()
    first = runner.invoke(args=["repair-bank-reconciliation", "--number",
        body["reconciliation_number"], "--account-code", "1010", "--apply"])
    second = runner.invoke(args=["repair-bank-reconciliation", "--number",
        body["reconciliation_number"], "--account-code", "1010", "--apply"])
    assert first.exit_code == second.exit_code == 0
    assert '"changed": false' in first.output and '"changed": false' in second.output
    assert '"accounting_journal_changes": "NONE"' in first.output
    assert AccountingJournalEntry.query.count() == journal_count
