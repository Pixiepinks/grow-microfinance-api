from datetime import date, timedelta
from decimal import Decimal

from app.collection_sheets import (clear_reconciled_payments, recalculate,
                                   valid_bank, valid_expense_account)
from app.extensions import db
from app.models import (AccountingAccount, AccountingJournalEntry, AccountingJournalLine, CollectionSheet,
                        CollectionSheetExpense, CollectionSheetItem, Customer, Loan,
                        Payment, User)


def test_draft_sheet_recalculates_server_totals_without_posting(app):
    with app.app_context():
        creator = User(email="admin@sheet.test", name="Admin", role="admin", password_hash="x")
        collector = User(email="collector@sheet.test", name="Collector", role="staff", password_hash="x",
                         is_collector=True, can_collect_cash=True)
        customer_user = User(email="customer@sheet.test", name="Customer", role="customer", password_hash="x")
        db.session.add_all([creator, collector, customer_user]); db.session.flush()
        customer = Customer(user_id=customer_user.id, customer_code="CUS-SHEET", full_name="Sheet Customer")
        db.session.add(customer); db.session.flush()
        loan = Loan(loan_number="LN-SHEET", customer_id=customer.id, principal_amount=Decimal("8000"),
                    interest_rate=Decimal("10"), total_days=30, payment_interval_days=7,
                    daily_installment=Decimal("300"), total_payable=Decimal("9000"), start_date=date.today(),
                    end_date=date.today() + timedelta(days=30), created_by_id=creator.id)
        expense_account = AccountingAccount(account_code="5901", account_name="Route Expense", account_type="EXPENSE",
                                            normal_balance="DEBIT", account_subtype="OPERATING_EXPENSE",
                                            is_active=True, allow_manual_posting=True)
        bank = AccountingAccount(account_code="1901", account_name="Sheet Bank", account_type="ASSET",
                                 normal_balance="DEBIT", account_subtype="BANK", is_active=True, allow_manual_posting=True)
        db.session.add_all([loan, expense_account, bank]); db.session.flush()
        sheet = CollectionSheet(sheet_number="CS-20260726-0001", collector_id=collector.id,
                                collection_date=date.today(), actual_deposit=Decimal("7300"), created_by_id=creator.id)
        sheet.items.extend([CollectionSheetItem(loan_id=loan.id, customer_id=customer.id, amount=Decimal("8000"))])
        sheet.expenses.extend([CollectionSheetExpense(expense_account_id=expense_account.id, amount=Decimal("700"), description="Route")])
        db.session.add(sheet); db.session.flush(); recalculate(sheet)

        assert sheet.gross_collection == Decimal("8000.00")
        assert sheet.total_expenses == Decimal("700.00")
        assert sheet.expected_deposit == Decimal("7300.00")
        assert sheet.difference == Decimal("0.00")
        assert sheet.items[0].payment_id is None
        assert Payment.query.count() == 0
        assert valid_expense_account(expense_account)
        assert valid_bank(bank)


def test_reconciled_sheet_clears_all_receipts_without_faking_expense_as_bank_cash(app):
    with app.app_context():
        collector = User(email="clear@sheet.test", name="Collector", role="staff", password_hash="x")
        customer_user = User(email="clear-customer@sheet.test", name="Customer", role="customer", password_hash="x")
        db.session.add_all([collector, customer_user]); db.session.flush()
        customer = Customer(user_id=customer_user.id, customer_code="CUS-CLEAR", full_name="Clear Customer")
        db.session.add(customer); db.session.flush()
        loan = Loan(loan_number="LN-CLEAR", customer_id=customer.id, principal_amount=Decimal("8000"),
                    interest_rate=Decimal("10"), total_days=30, payment_interval_days=7,
                    daily_installment=Decimal("300"), total_payable=Decimal("9000"), start_date=date.today(),
                    end_date=date.today() + timedelta(days=30), created_by_id=collector.id)
        db.session.add(loan); db.session.flush()
        sheet = CollectionSheet(sheet_number="CS-CLEAR", collector_id=collector.id, collection_date=date.today(),
                                gross_collection=Decimal("8000"), total_expenses=Decimal("700"),
                                actual_deposit=Decimal("7300"), difference=Decimal("0"), status="RECONCILED",
                                bank_journal_id=77, created_by_id=collector.id)
        item = CollectionSheetItem(loan_id=loan.id, customer_id=customer.id, amount=Decimal("8000"))
        sheet.items.append(item); db.session.add(sheet); db.session.flush()
        payment = Payment(loan_id=loan.id, amount_collected=Decimal("8000"), collected_by_id=collector.id,
                          collector_id=collector.id, collection_method="CASH_COLLECTOR", deposit_status="UNDEPOSITED")
        db.session.add(payment); db.session.flush(); item.payment_id = payment.id; db.session.flush()

        clear_reconciled_payments(sheet)

        assert payment.collection_clearance_status == "CLEARED"
        assert payment.collection_sheet_id == sheet.id
        assert payment.collection_sheet_deposit_journal_id == 77
        assert payment.deposited_amount == Decimal("7300.00")
        assert payment.undeposited_amount == Decimal("700.00")  # expense, not fictitious bank cash


def test_unreconciled_sheet_does_not_clear_receipts(app):
    with app.app_context():
        sheet = CollectionSheet(status="POSTED")
        clear_reconciled_payments(sheet)
        assert sheet.status == "POSTED"


def test_historical_clearance_command_defaults_to_preview_and_is_idempotent(app):
    with app.app_context():
        admin = User(email="repair@sheet.test", name="Repair Admin", role="admin", password_hash="x")
        collector = User(email="repair-collector@sheet.test", name="Repair Collector", role="staff", password_hash="x")
        bank = AccountingAccount(account_code="1998", account_name="Repair Bank", account_type="ASSET",
                                 normal_balance="DEBIT", account_subtype="BANK", is_active=True, allow_manual_posting=True)
        clearing = AccountingAccount(account_code="1997", account_name="Repair Clearing", account_type="ASSET",
                                     normal_balance="DEBIT", account_subtype="COLLECTION_CLEARING", is_active=True,
                                     allow_manual_posting=True, is_collection_account=True)
        db.session.add_all([admin, collector, bank, clearing]); db.session.flush()
        collector.default_collection_account_id = clearing.id
        sheet = CollectionSheet(sheet_number="CS-20260313-0001", collector_id=collector.id,
                                collection_date=date(2026, 3, 13), gross_collection=Decimal("12600"),
                                total_expenses=Decimal("0"), expected_deposit=Decimal("12600"),
                                actual_deposit=Decimal("12600"), difference=Decimal("0"),
                                bank_account_id=bank.id, status="RECONCILED", created_by_id=admin.id)
        db.session.add(sheet); db.session.flush()
        deposit = AccountingJournalEntry(journal_no="JE-CS-REPAIR", journal_date=date(2026, 3, 13),
                                         description="Existing collection sheet deposit", status="POSTED",
                                         source_type="COLLECTION_SHEET_DEPOSIT", source_id=sheet.id,
                                         total_debit=Decimal("12600"), total_credit=Decimal("12600"))
        db.session.add(deposit); db.session.flush(); sheet.bank_journal_id = deposit.id
        db.session.add_all([
            AccountingJournalLine(journal_entry_id=deposit.id, line_no=1, account_id=bank.id,
                                  debit=Decimal("12600"), credit=Decimal("0")),
            AccountingJournalLine(journal_entry_id=deposit.id, line_no=2, account_id=clearing.id,
                                  debit=Decimal("0"), credit=Decimal("12600")),
        ])
        for index, amount in enumerate(("4200", "2100", "2100", "2100", "2100"), 1):
            user = User(email=f"repair-customer-{index}@sheet.test", name=f"Customer {index}", role="customer", password_hash="x")
            db.session.add(user); db.session.flush()
            customer = Customer(user_id=user.id, customer_code=f"CUS-REPAIR-{index}", full_name=f"Customer {index}")
            db.session.add(customer); db.session.flush()
            loan = Loan(loan_number=f"LN-REPAIR-{index}", customer_id=customer.id, principal_amount=Decimal("5000"),
                        interest_rate=Decimal("10"), total_days=30, payment_interval_days=7,
                        daily_installment=Decimal("200"), total_payable=Decimal("6000"), start_date=date(2026, 3, 1),
                        end_date=date(2026, 3, 31), created_by_id=admin.id)
            db.session.add(loan); db.session.flush()
            payment = Payment(loan_id=loan.id, collection_date=sheet.collection_date, amount_collected=Decimal(amount),
                              principal_paid=Decimal(amount), collected_by_id=collector.id, collector_id=collector.id,
                              collection_method="CASH_COLLECTOR", receipt_number=f"RCP-REPAIR-{index}",
                              journal_id=deposit.id, status="POSTED", deposited_amount=Decimal("0"),
                              deposit_status="UNDEPOSITED", collection_clearance_status="UNDEPOSITED")
            db.session.add(payment); db.session.flush()
            db.session.add(CollectionSheetItem(sheet=sheet, loan_id=loan.id, customer_id=customer.id,
                                                amount=Decimal(amount), payment_id=payment.id, posting_status="POSTED"))
        db.session.commit()

        runner = app.test_cli_runner()
        preview = runner.invoke(args=["repair-collection-sheet-clearance", "--sheet-number", sheet.sheet_number])
        assert preview.exit_code == 0
        assert '"mode": "preview"' in preview.output
        assert '"currently_undeposited": "12600.00"' in preview.output
        assert Payment.query.filter_by(collection_clearance_status="UNDEPOSITED").count() == 5

        applied = runner.invoke(args=["repair-collection-sheet-clearance", "--sheet-number", sheet.sheet_number, "--apply"])
        assert applied.exit_code == 0
        assert '"financial_state_unchanged": true' in applied.output
        assert all(p.collection_clearance_status == "CLEARED" and p.undeposited_amount == 0
                   for p in Payment.query.order_by(Payment.id).all())
        assert AccountingJournalEntry.query.count() == 1

        repeated = runner.invoke(args=["repair-collection-sheet-clearance", "--sheet-number", sheet.sheet_number, "--apply"])
        assert repeated.exit_code == 0
        assert "No repair required" in repeated.output
        assert AccountingJournalEntry.query.count() == 1
