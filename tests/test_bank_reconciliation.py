from datetime import date
from decimal import Decimal

from flask_jwt_extended import create_access_token

from app.extensions import db
from app.models import (AccountingAccount, AccountingJournalEntry, AccountingJournalLine,
                        BankReconciliation, BankReconciliationAudit, BankReconciliationLine, User)


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
    assert ("/admin/accounting/bank-reconciliations/<int:rec_id>/matches", "DELETE") in rules
    assert ("/admin/accounting/bank-reconciliations/<int:rec_id>/matches/<int:line_id>", "DELETE") in rules
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


def test_remove_match_compatibility_route_unmatches_only_selected_line(app, client):
    _, headers = _headers(app)
    bank = AccountingAccount(account_code="1010", account_name="Bank", account_type="ASSET",
        normal_balance="DEBIT", account_subtype="BANK")
    other = AccountingAccount(account_code="1050", account_name="Other", account_type="ASSET",
        normal_balance="DEBIT", account_subtype="OTHER")
    db.session.add_all([bank, other]); db.session.commit()
    debit_entry = _journal(bank, other, "J-REMOVE-DEBIT")
    credit_entry = AccountingJournalEntry(journal_no="J-KEEP-CREDIT",
        journal_date=date(2026, 2, 11), description="Bank payment", status="POSTED",
        total_debit=Decimal("100"), total_credit=Decimal("100"))
    credit_entry.lines = [AccountingJournalLine(line_no=1, account=bank,
        debit=Decimal("0"), credit=Decimal("100")),
        AccountingJournalLine(line_no=2, account=other,
        debit=Decimal("100"), credit=Decimal("0"))]
    db.session.add(credit_entry); db.session.commit()
    rec_id = client.post("/admin/accounting/bank-reconciliations", headers=headers, json={
        "bank_account_id": bank.id, "statement_date_from": "2026-02-01",
        "statement_date_to": "2026-02-28", "statement_opening_balance": "0",
        "statement_closing_balance": "2000"}).get_json()["id"]
    line_ids = [debit_entry.lines[0].id, credit_entry.lines[0].id]
    marked = client.post(f"/admin/accounting/bank-reconciliations/{rec_id}/lines",
        headers=headers, json={"journal_line_ids": line_ids})
    assert marked.status_code == 200
    gl_before = client.get(f"/admin/accounting/general-ledger?account_id={bank.id}",
                           headers=headers).get_json()
    entry_count = AccountingJournalEntry.query.count()
    line_count = AccountingJournalLine.query.count()

    route = f"/admin/accounting/bank-reconciliations/{rec_id}/matches"
    assert client.options(route).status_code == 204
    removed = client.delete(route, headers=headers,
                            json={"journal_line_id": debit_entry.lines[0].id})
    assert removed.status_code == 200
    body = removed.get_json()
    assert body["success"] is True and body["message"] == "Transaction match removed."
    assert body["reconciliation_id"] == rec_id
    assert body["journal_line_id"] == debit_entry.lines[0].id
    assert body["summary"] == {
        "matched_transaction_count": 1, "statement_closing_balance": "2000.00",
        "gl_balance": "2000.00", "reconciled_debits": "0.00",
        "reconciled_credits": "100.00", "unreconciled_debits": "2100.00",
        "unreconciled_credits": "0.00", "difference": "0.00"}
    assert body["matched_transaction_count"] == 1
    assert AccountingJournalEntry.query.count() == entry_count
    assert AccountingJournalLine.query.count() == line_count
    assert debit_entry.lines[0].debit == Decimal("2100")
    assert debit_entry.lines[0].bank_reconciliation_id is None
    assert debit_entry.lines[0].is_reconciled is False
    assert debit_entry.lines[0].reconciled_date is None
    assert debit_entry.lines[0].reconciled_at is None
    assert debit_entry.lines[0].reconciled_by_id is None
    assert credit_entry.lines[0].bank_reconciliation_id == rec_id
    assert credit_entry.lines[0].is_reconciled is True
    assert BankReconciliationLine.query.filter_by(bank_reconciliation_id=rec_id).count() == 1
    gl_after = client.get(f"/admin/accounting/general-ledger?account_id={bank.id}",
                          headers=headers).get_json()
    assert [(row["journal_line_id"], row["debit"], row["credit"], row["running_balance"])
            for row in gl_after["transactions"]] == [
        (row["journal_line_id"], row["debit"], row["credit"], row["running_balance"])
        for row in gl_before["transactions"]]

    repeated = client.delete(route, headers=headers,
                             json={"journal_line_id": debit_entry.lines[0].id})
    assert repeated.status_code == 404
    assert repeated.get_json()["error"] == "match_not_found"


def test_remove_match_conflict_lock_and_round_trip(app, client):
    _, headers = _headers(app)
    bank = AccountingAccount(account_code="1010", account_name="Bank", account_type="ASSET",
        normal_balance="DEBIT", account_subtype="BANK")
    other = AccountingAccount(account_code="1050", account_name="Other", account_type="ASSET",
        normal_balance="DEBIT", account_subtype="OTHER")
    db.session.add_all([bank, other]); db.session.commit()
    entry = _journal(bank, other)
    recs = [BankReconciliation(reconciliation_number=f"BR-REMOVE-{number}",
        bank_account_id=bank.id, statement_date_from=date(2026, 2, 1),
        statement_date_to=date(2026, 2, 28), statement_opening_balance=0,
        statement_closing_balance=2100, status=status)
        for number, status in ((1, "IN_PROGRESS"), (2, "IN_PROGRESS"), (3, "COMPLETED"))]
    db.session.add_all(recs); db.session.flush()
    line = entry.lines[0]
    line.is_reconciled = True; line.bank_reconciliation_id = recs[0].id
    line.reconciled_date = date(2026, 2, 28)
    db.session.add(BankReconciliationLine(bank_reconciliation_id=recs[0].id,
        journal_line_id=line.id, debit=line.debit, credit=line.credit,
        reconciled_date=date(2026, 2, 28)))
    db.session.commit()

    other_rec = client.delete(
        f"/admin/accounting/bank-reconciliations/{recs[1].id}/matches/{line.id}",
        headers=headers)
    assert other_rec.status_code == 409
    assert other_rec.get_json()["error"] == "match_conflict"
    locked = client.delete(
        f"/admin/accounting/bank-reconciliations/{recs[2].id}/matches",
        headers=headers, json={"journal_line_id": line.id})
    assert locked.status_code == 409
    assert locked.get_json() == {"success": False, "error": "reconciliation_locked",
        "message": "Completed reconciliations cannot be modified."}
    assert recs[2].status == "COMPLETED" and line.bank_reconciliation_id == recs[0].id

    removed = client.delete(
        f"/admin/accounting/bank-reconciliations/{recs[0].id}/matches/{line.id}",
        headers=headers)
    assert removed.status_code == 200 and line.is_reconciled is False
    remarked = client.post(f"/admin/accounting/bank-reconciliations/{recs[0].id}/lines",
        headers=headers, json={"journal_line_id": line.id})
    assert remarked.status_code == 200 and line.is_reconciled is True
    assert BankReconciliationLine.query.filter_by(journal_line_id=line.id).count() == 1
    assert client.delete("/admin/accounting/bank-reconciliations/999999/matches",
        headers=headers, json={"journal_line_id": line.id}).status_code == 404


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


def test_complete_requires_persisted_transactions_and_bulk_line_ids(app, client):
    _, headers = _headers(app)
    bank = AccountingAccount(account_code="1010", account_name="Bank", account_type="ASSET",
        normal_balance="DEBIT", account_subtype="BANK")
    other = AccountingAccount(account_code="1050", account_name="Other", account_type="ASSET",
        normal_balance="DEBIT", account_subtype="OTHER")
    db.session.add_all([bank, other]); db.session.commit()
    first = _journal(bank, other, "J-BULK-1")
    second = _journal(bank, other, "J-BULK-2")
    rec_id = client.post("/admin/accounting/bank-reconciliations", headers=headers, json={
        "bank_account_id": bank.id, "statement_date_from": "2026-02-01",
        "statement_date_to": "2026-02-28", "statement_opening_balance": "0",
        "statement_closing_balance": "4200"}).get_json()["id"]

    empty = client.post(f"/admin/accounting/bank-reconciliations/{rec_id}/complete", headers=headers)
    assert empty.status_code == 422
    assert empty.get_json() == {
        "error": "no_transactions_matched",
        "message": "Mark bank transactions as reconciled before completing the reconciliation."
    }
    assert BankReconciliation.query.get(rec_id).status == "DRAFT"

    marked = client.post(f"/admin/accounting/bank-reconciliations/{rec_id}/lines", headers=headers,
        json={"journal_line_ids": [first.lines[0].id, second.lines[0].id],
              "reconciled_date": "2026-02-28", "statement_reference": "STMT-1"})
    assert marked.status_code == 200
    assert marked.get_json()["matched_count"] == 2
    assert marked.get_json()["matched_transaction_count"] == 2
    assert all({"is_reconciled", "reconciliation_number", "reconciled_date"} <= row.keys()
               for row in marked.get_json()["transactions"])
    assert marked.get_json()["reconciled_debits"] == "4200.00"
    assert BankReconciliationLine.query.count() == 2
    assert all(line.is_reconciled for line in (first.lines[0], second.lines[0]))
    assert not first.lines[1].is_reconciled and not second.lines[1].is_reconciled

    completed = client.post(f"/admin/accounting/bank-reconciliations/{rec_id}/complete", headers=headers,
        json={"journal_line_ids": [first.lines[0].id, second.lines[0].id]})
    assert completed.status_code == 200
    assert completed.get_json()["status"] == "COMPLETED"


def test_repair_empty_completed_reconciliation_is_preview_first(app):
    bank = AccountingAccount(account_code="1010", account_name="Bank", account_type="ASSET",
        normal_balance="DEBIT", account_subtype="BANK")
    other = AccountingAccount(account_code="1050", account_name="Other", account_type="ASSET",
        normal_balance="DEBIT", account_subtype="OTHER")
    db.session.add_all([bank, other]); db.session.commit(); entry = _journal(bank, other)
    rec = BankReconciliation(reconciliation_number="BR-20260228-0001", bank_account_id=bank.id,
        statement_date_from=date(2026, 2, 1), statement_date_to=date(2026, 2, 28),
        statement_opening_balance=0, statement_closing_balance=2100, status="COMPLETED",
        completed_at=date(2026, 2, 28))
    db.session.add(rec); db.session.commit()
    runner = app.test_cli_runner()
    preview = runner.invoke(args=["repair-invalid-bank-reconciliation", "--number",
        rec.reconciliation_number, "--preview"])
    assert preview.exit_code == 0 and '"invalid_empty_completion": true' in preview.output
    assert rec.status == "COMPLETED"
    applied = runner.invoke(args=["repair-invalid-bank-reconciliation", "--number",
        rec.reconciliation_number, "--apply"])
    assert applied.exit_code == 0
    assert rec.status == "IN_PROGRESS" and rec.completed_at is None
    assert not entry.lines[0].is_reconciled and entry.lines[0].debit == Decimal("2100")
    audit = BankReconciliationAudit.query.filter_by(action="INVALID_COMPLETION_REPAIRED").one()
    assert audit.reason == "Automatically reopened because reconciliation was completed with zero matched bank GL lines."
    second = runner.invoke(args=["repair-invalid-bank-reconciliation", "--number",
        rec.reconciliation_number, "--apply"])
    assert second.exit_code == 0 and '"changed": false' in second.output
    assert BankReconciliationAudit.query.filter_by(action="INVALID_COMPLETION_REPAIRED").count() == 1


def test_effective_posted_status_is_shared_by_gl_listing_and_marking(app, client):
    _, headers = _headers(app)
    bank = AccountingAccount(account_code="1010", account_name="Bank", account_type="ASSET",
        normal_balance="DEBIT", account_subtype="BANK")
    other = AccountingAccount(account_code="1050", account_name="Other", account_type="ASSET",
        normal_balance="DEBIT", account_subtype="OTHER")
    db.session.add_all([bank, other]); db.session.commit()
    legacy = _journal(bank, other, "J-LEGACY-POSTED")
    legacy.status = " posted "
    finalized = _journal(bank, other, "J-APPROVED-AND-POSTED")
    finalized.status = "APPROVED_AND_POSTED"
    db.session.commit()
    rec_id = client.post("/admin/accounting/bank-reconciliations", headers=headers, json={
        "bank_account_id": bank.id, "statement_date_from": "2026-02-01",
        "statement_date_to": "2026-02-28", "statement_opening_balance": "0",
        "statement_closing_balance": "4200"}).get_json()["id"]

    gl = client.get(f"/admin/accounting/general-ledger?account_id={bank.id}",
                    headers=headers).get_json()
    assert {row["journal_no"] for row in gl["transactions"]} == {
        "J-LEGACY-POSTED", "J-APPROVED-AND-POSTED"}
    listing = client.get(
        f"/admin/accounting/bank-reconciliations/{rec_id}/transactions",
        headers=headers).get_json()["transactions"]
    assert all(row["is_posted"] and row["is_reconcilable"] for row in listing)
    response = client.post(f"/admin/accounting/bank-reconciliations/{rec_id}/lines",
        headers=headers, json={"journal_line_ids": [legacy.lines[0].id, finalized.lines[0].id]})
    assert response.status_code == 200
    assert BankReconciliationLine.query.count() == 2


def test_draft_and_mixed_batch_return_line_details_without_partial_marking(app, client):
    _, headers = _headers(app)
    bank = AccountingAccount(account_code="1010", account_name="Bank", account_type="ASSET",
        normal_balance="DEBIT", account_subtype="BANK")
    other = AccountingAccount(account_code="1050", account_name="Other", account_type="ASSET",
        normal_balance="DEBIT", account_subtype="OTHER")
    db.session.add_all([bank, other]); db.session.commit()
    posted = _journal(bank, other, "J-POSTED")
    draft = _journal(bank, other, "J-DRAFT")
    draft.status = "DRAFT"; db.session.commit()
    rec_id = client.post("/admin/accounting/bank-reconciliations", headers=headers, json={
        "bank_account_id": bank.id, "statement_date_from": "2026-02-01",
        "statement_date_to": "2026-02-28", "statement_opening_balance": "0",
        "statement_closing_balance": "2100"}).get_json()["id"]

    listing = client.get(
        f"/admin/accounting/bank-reconciliations/{rec_id}/transactions",
        headers=headers).get_json()["transactions"]
    draft_row = next(row for row in listing if row["journal_number"] == "J-DRAFT")
    assert draft_row["journal_status"] == "DRAFT"
    assert draft_row["is_posted"] is False and draft_row["is_reconcilable"] is False
    assert draft_row["reconciliation_block_reason"] == "Journal entry is not posted."

    response = client.post(f"/admin/accounting/bank-reconciliations/{rec_id}/lines",
        headers=headers, json={"journal_line_ids": [posted.lines[0].id, draft.lines[0].id]})
    assert response.status_code == 422
    assert response.get_json() == {"error": "unposted_journal_lines",
        "message": "One or more selected journal lines are not posted.",
        "invalid_lines": [{"journal_line_id": draft.lines[0].id,
            "journal_number": "J-DRAFT", "description": "Collector deposit",
            "journal_status": "DRAFT",
            "reason": "Only posted journal lines can be reconciled."}]}
    assert BankReconciliationLine.query.count() == 0
    assert not posted.lines[0].is_reconciled and not draft.lines[0].is_reconciled


def test_posted_status_repair_is_preview_first_and_never_repairs_draft(app):
    bank = AccountingAccount(account_code="1010", account_name="Bank", account_type="ASSET",
        normal_balance="DEBIT", account_subtype="BANK")
    other = AccountingAccount(account_code="1050", account_name="Other", account_type="ASSET",
        normal_balance="DEBIT", account_subtype="OTHER")
    db.session.add_all([bank, other]); db.session.commit()
    legacy = _journal(bank, other, "J-LEGACY"); legacy.status = "posted"
    draft = _journal(bank, other, "J-GENUINE-DRAFT"); draft.status = "DRAFT"
    rec = BankReconciliation(reconciliation_number="BR-20260307-0002", bank_account_id=bank.id,
        statement_date_from=date(2026, 2, 1), statement_date_to=date(2026, 3, 7),
        statement_opening_balance=0, statement_closing_balance=2100, status="DRAFT")
    db.session.add(rec); db.session.commit()
    runner = app.test_cli_runner()
    preview = runner.invoke(args=["repair-posted-journal-status",
        "--reconciliation-number", rec.reconciliation_number, "--preview"])
    assert preview.exit_code == 0
    assert '"journal_number": "J-LEGACY"' in preview.output
    assert "J-GENUINE-DRAFT" not in preview.output
    assert legacy.status == "posted" and legacy.posted_at is None
    applied = runner.invoke(args=["repair-posted-journal-status",
        "--reconciliation-number", rec.reconciliation_number, "--apply"])
    assert applied.exit_code == 0
    assert legacy.status == "POSTED" and legacy.posted_at is not None
    assert draft.status == "DRAFT" and draft.posted_at is None
    assert legacy.total_debit == Decimal("2100") and draft.total_debit == Decimal("2100")
