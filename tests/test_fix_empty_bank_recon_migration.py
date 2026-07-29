"""Regression tests for the narrowly-scoped bank reconciliation data repair."""
import importlib.util
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from app.extensions import db
from app.models import (AccountingAccount, AccountingJournalEntry,
                        AccountingJournalLine, BankReconciliation,
                        BankReconciliationAudit, BankReconciliationLine)


MIGRATION_PATH = (Path(__file__).resolve().parents[1] / "migrations" / "versions" /
                  "0050_fix_empty_bank_recon.py")
spec = importlib.util.spec_from_file_location("fix_empty_bank_recon", MIGRATION_PATH)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def _target(status="COMPLETED", with_match=False):
    bank = AccountingAccount(account_code="1010", account_name="NDB Current Account",
        account_type="ASSET", normal_balance="DEBIT", account_subtype="BANK")
    offset = AccountingAccount(account_code="1050", account_name="Offset",
        account_type="ASSET", normal_balance="DEBIT", account_subtype="OTHER")
    db.session.add_all([bank, offset]); db.session.flush()
    entry = AccountingJournalEntry(journal_no="MIGRATION-GL-1",
        journal_date=date(2026, 2, 1), description="Existing bank transaction",
        status="POSTED", total_debit=Decimal("125.50"), total_credit=Decimal("125.50"))
    bank_line = AccountingJournalLine(line_no=1, account_id=bank.id,
        debit=Decimal("125.50"), credit=Decimal("0"))
    entry.lines = [bank_line, AccountingJournalLine(line_no=2, account_id=offset.id,
        debit=Decimal("0"), credit=Decimal("125.50"))]
    rec = BankReconciliation(reconciliation_number="BR-20260228-0001",
        bank_account_id=bank.id, statement_date_from=date(2026, 1, 1),
        statement_date_to=date(2026, 2, 28),
        statement_opening_balance=Decimal("10.00"),
        statement_closing_balance=Decimal("135.50"), status=status,
        completed_at=datetime(2026, 2, 28, 12, 0), approved_by_id=None)
    db.session.add_all([entry, rec]); db.session.flush()
    if with_match:
        bank_line.is_reconciled = True
        bank_line.bank_reconciliation_id = rec.id
        db.session.add(BankReconciliationLine(bank_reconciliation_id=rec.id,
            journal_line_id=bank_line.id, debit=bank_line.debit, credit=bank_line.credit,
            reconciled_date=date(2026, 2, 28)))
    db.session.commit()
    return rec, entry, bank_line


def test_migration_reopens_confirmed_empty_completion_without_accounting_changes(app):
    rec, entry, bank_line = _target()
    statement_before = (rec.statement_opening_balance, rec.statement_closing_balance)
    journal_before = (entry.total_debit, entry.total_credit,
                      bank_line.debit, bank_line.credit)

    report = migration._repair_target(db.session.connection())
    db.session.commit()
    db.session.refresh(rec); db.session.refresh(entry); db.session.refresh(bank_line)

    assert report["changed"] is True and report["eligible_count"] == 1
    assert rec.status == "IN_PROGRESS"
    assert rec.completed_at is None and rec.approved_by_id is None
    assert len(rec.allocations) == 0
    assert (rec.statement_opening_balance, rec.statement_closing_balance) == statement_before
    assert (entry.total_debit, entry.total_credit,
            bank_line.debit, bank_line.credit) == journal_before
    assert bank_line.is_reconciled is False and bank_line.bank_reconciliation_id is None
    audit = BankReconciliationAudit.query.filter_by(
        action=migration.AUDIT_ACTION).one()
    assert audit.user_id is None
    assert audit.reason == migration.AUDIT_REASON
    assert audit.reconciliation_number == "BR-20260228-0001"

    # Deployment retry is harmless and cannot duplicate the audit record.
    retry = migration._repair_target(db.session.connection())
    db.session.commit()
    assert retry["changed"] is False
    assert BankReconciliationAudit.query.filter_by(
        action=migration.AUDIT_ACTION).count() == 1


def test_migration_does_not_change_reconciliation_with_persisted_match(app):
    rec, _, bank_line = _target(with_match=True)
    completed_at = rec.completed_at

    report = migration._repair_target(db.session.connection())
    db.session.commit(); db.session.refresh(rec)

    assert report["changed"] is False
    assert report["matched_count"] == 1
    assert rec.status == "COMPLETED" and rec.completed_at == completed_at
    assert bank_line.is_reconciled is True
    assert BankReconciliationAudit.query.filter_by(
        action=migration.AUDIT_ACTION).count() == 0


def test_migration_leaves_already_in_progress_record_unchanged(app):
    rec, _, _ = _target(status="IN_PROGRESS")
    completed_at = rec.completed_at

    first = migration._repair_target(db.session.connection())
    second = migration._repair_target(db.session.connection())
    db.session.commit(); db.session.refresh(rec)

    assert first["changed"] is second["changed"] is False
    assert rec.status == "IN_PROGRESS" and rec.completed_at == completed_at
    assert BankReconciliationAudit.query.filter_by(
        action=migration.AUDIT_ACTION).count() == 0
