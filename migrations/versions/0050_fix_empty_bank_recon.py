"""reopen the confirmed empty completed bank reconciliation

Revision ID: 0050_fix_empty_bank_recon
Revises: 0049_bank_reconciliation_lines

This is deliberately a narrowly-scoped data repair.  It does not select or
alter journal lines and it does not recalculate any accounting amount.
"""
import logging

from alembic import op
import sqlalchemy as sa


revision = "0050_fix_empty_bank_recon"
down_revision = "0049_bank_reconciliation_lines"
branch_labels = None
depends_on = None

TARGET_NUMBER = "BR-20260228-0001"
TARGET_ACCOUNT_CODE = "1010"
TARGET_ACCOUNT_NAME = "NDB Current Account"
TARGET_DATE_FROM = "2026-01-01"
TARGET_DATE_TO = "2026-02-28"
AUDIT_ACTION = "EMPTY_COMPLETION_REOPENED"
AUDIT_REASON = (
    "Automatically reopened because reconciliation was completed with zero "
    "matched bank GL lines."
)

logger = logging.getLogger("alembic.runtime.migration")


def _repair_target(bind):
    """Validate and repair only the confirmed production reconciliation."""
    target = bind.execute(sa.text("""
        SELECT br.id, br.status, br.completed_at, br.approved_by_id,
               br.bank_account_id, aa.account_code, aa.account_name,
               br.statement_date_from, br.statement_date_to,
               (SELECT COUNT(*) FROM bank_reconciliation_lines brl
                 WHERE brl.bank_reconciliation_id = br.id) AS matched_count,
               (SELECT COUNT(*)
                  FROM accounting_journal_lines ajl
                  JOIN accounting_journal_entries aje
                    ON aje.id = ajl.journal_entry_id
                 WHERE ajl.account_id = br.bank_account_id
                   AND UPPER(aje.status) IN ('POSTED', 'REVERSED')
                   AND aje.journal_date >= br.statement_date_from
                   AND aje.journal_date <= br.statement_date_to) AS eligible_count
          FROM bank_reconciliations br
          JOIN accounting_accounts aa ON aa.id = br.bank_account_id
         WHERE br.reconciliation_number = :number
    """), {"number": TARGET_NUMBER}).mappings().one_or_none()

    if target is None:
        logger.warning("Bank reconciliation repair skipped: %s does not exist", TARGET_NUMBER)
        return {"changed": False, "reason": "not_found"}

    valid = (
        target["status"] == "COMPLETED"
        and target["account_code"] == TARGET_ACCOUNT_CODE
        and target["account_name"] == TARGET_ACCOUNT_NAME
        and str(target["statement_date_from"]) == TARGET_DATE_FROM
        and str(target["statement_date_to"]) == TARGET_DATE_TO
        and target["matched_count"] == 0
        and target["eligible_count"] > 0
    )
    if not valid:
        logger.warning(
            "Bank reconciliation repair skipped for %s: validation failed "
            "(status=%s, account=%s/%s, period=%s..%s, matched=%s, eligible=%s)",
            TARGET_NUMBER, target["status"], target["account_code"],
            target["account_name"], target["statement_date_from"],
            target["statement_date_to"], target["matched_count"],
            target["eligible_count"],
        )
        return {"changed": False, "reason": "validation_failed", **dict(target)}

    # Repeat the safety predicates in the UPDATE so a concurrent match or
    # status change cannot be overwritten after the validation query.
    result = bind.execute(sa.text("""
        UPDATE bank_reconciliations
           SET status = 'IN_PROGRESS',
               completed_at = NULL,
               approved_by_id = NULL,
               updated_at = CURRENT_TIMESTAMP
         WHERE id = :id
           AND status = 'COMPLETED'
           AND NOT EXISTS (
               SELECT 1 FROM bank_reconciliation_lines
                WHERE bank_reconciliation_id = :id)
           AND EXISTS (
               SELECT 1
                 FROM accounting_journal_lines ajl
                 JOIN accounting_journal_entries aje
                   ON aje.id = ajl.journal_entry_id
                WHERE ajl.account_id = :account_id
                  AND UPPER(aje.status) IN ('POSTED', 'REVERSED')
                  AND aje.journal_date >= :date_from
                  AND aje.journal_date <= :date_to)
    """), {"id": target["id"], "account_id": target["bank_account_id"],
             "date_from": TARGET_DATE_FROM, "date_to": TARGET_DATE_TO})

    if result.rowcount != 1:
        logger.warning("Bank reconciliation repair skipped for %s: record changed concurrently",
                       TARGET_NUMBER)
        return {"changed": False, "reason": "concurrent_change", **dict(target)}

    # A nullable user_id is the audit model's system-actor representation.  The
    # action encodes COMPLETED -> IN_PROGRESS, while the immutable audit row
    # records the target identity, exact repair reason, and database timestamp.
    bind.execute(sa.text("""
        INSERT INTO bank_reconciliation_audits
            (bank_reconciliation_id, action, user_id, bank_account_id,
             journal_line_id, reconciliation_number, reason, created_at)
        SELECT :id, :action, NULL, :account_id, NULL, :number, :reason,
               CURRENT_TIMESTAMP
         WHERE NOT EXISTS (
             SELECT 1 FROM bank_reconciliation_audits
              WHERE bank_reconciliation_id = :id
                AND action = :action
                AND reason = :reason)
    """), {"id": target["id"], "action": AUDIT_ACTION,
             "account_id": target["bank_account_id"], "number": TARGET_NUMBER,
             "reason": AUDIT_REASON})
    logger.info(
        "Reopened %s from COMPLETED to IN_PROGRESS; matched lines=%s, eligible lines=%s; "
        "journal lines and accounting amounts were not changed",
        TARGET_NUMBER, target["matched_count"], target["eligible_count"],
    )
    return {"changed": True, **dict(target)}


def upgrade():
    _repair_target(op.get_bind())


def downgrade():
    # Restoring COMPLETED with no persisted matches would recreate invalid
    # accounting state.  This irreversible data repair is intentionally a no-op.
    pass
