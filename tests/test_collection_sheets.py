from datetime import date, timedelta
from decimal import Decimal

from app.collection_sheets import recalculate, valid_bank, valid_expense_account
from app.extensions import db
from app.models import (AccountingAccount, CollectionSheet, CollectionSheetExpense,
                        CollectionSheetItem, Customer, Loan, Payment, User)


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
