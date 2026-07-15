from datetime import date
from decimal import Decimal

from flask_jwt_extended import create_access_token

from app.accounting import seed_disbursement_settings, is_funding_account, preview_loan_disbursement
from app.extensions import db
from app.models import AccountingAccount, Customer, DisbursementChargeType, Loan, LoanApplication, User


def _admin_headers(app):
    admin = User(email="admin-disb@example.com", name="Admin", role="admin")
    admin.set_password("password")
    db.session.add(admin)
    db.session.commit()
    with app.app_context():
        token = create_access_token(identity=str(admin.id), additional_claims={"role": "admin"})
    return {"Authorization": f"Bearer {token}"}


def _application():
    user = User(email="customer-disb@example.com", name="Jane Customer", role="customer")
    user.set_password("password")
    db.session.add(user)
    db.session.flush()
    customer = Customer(user_id=user.id, customer_code="CUST-DISB", full_name="Jane Customer", nic_number="123456789V", mobile="0700000000", address="123 Street", business_type="Retail")
    db.session.add(customer)
    db.session.flush()
    application = LoanApplication(application_number="APP-DISB", customer_id=customer.id, loan_type="GROW", status="APPROVED", applied_amount=Decimal("15000"), approved_amount=Decimal("15000"), tenure_months=3, full_name="Jane Customer", nic_number="123456789V", mobile_number="0700000000")
    db.session.add(application)
    db.session.commit()
    return application, customer


def test_seed_repairs_accounts_doc_fee_and_options(app, client):
    with app.app_context():
        seed_disbursement_settings()
        application, _customer = _application()
        application_id = application.id
        bank = AccountingAccount.query.filter_by(account_code="1010").one()
        doc_income = AccountingAccount.query.filter_by(account_code="4030").one()
        doc_fee = DisbursementChargeType.query.filter_by(code="DOC_FEE").one()
        assert bank.account_name == "Main Bank Account"
        assert bank.is_active is True
        assert bank.allow_manual_posting is True
        assert bank.account_subtype == "BANK"
        assert is_funding_account(bank) is True
        assert doc_income.account_role == "DOCUMENTATION_FEE_INCOME"
        assert doc_fee.income_account_id == doc_income.id
        assert doc_fee.default_amount == Decimal("400.00")
        headers = _admin_headers(app)

    response = client.get(f"/admin/loan-applications/{application_id}/disbursement-options", headers=headers)
    assert response.status_code == 200
    payload = response.get_json()
    assert any(account["code"] == "1010" and account["account_subtype"] == "BANK" for account in payload["funding_accounts"])
    doc_payload = next(charge for charge in payload["charge_types"] if charge["code"] == "DOC_FEE")
    assert doc_payload["destination_account"]["code"] == "4030"
    assert {"charge_type_id": doc_payload["id"], "amount": 400.0} in payload["default_charges"]


def test_funding_helper_includes_system_bank_and_excludes_inactive_and_clearing(app):
    with app.app_context():
        seed_disbursement_settings()
        bank = AccountingAccount.query.filter_by(account_code="1010").one()
        bank.is_system_account = True
        inactive = AccountingAccount(account_code="1099", account_name="Inactive Bank", account_type="ASSET", account_subtype="BANK", normal_balance="DEBIT", allow_manual_posting=True, is_active=False)
        clearing = AccountingAccount(account_code="1051", account_name="Clearing", account_type="ASSET", account_subtype="COLLECTION_CLEARING", normal_balance="DEBIT", allow_manual_posting=True, is_active=True)
        db.session.add_all([inactive, clearing])
        db.session.commit()
        assert is_funding_account(bank) is True
        assert is_funding_account(inactive) is False
        assert is_funding_account(clearing) is False


def test_preview_balances_15000_principal_400_doc_fee(app):
    with app.app_context():
        seed_disbursement_settings()
        _application_obj, customer = _application()
        admin = User(email="creator-disb@example.com", name="Creator", role="admin")
        admin.set_password("password")
        db.session.add(admin)
        db.session.flush()
        loan = Loan(loan_number="LN-DISB", customer_id=customer.id, principal_amount=Decimal("15000"), gross_principal_amount=Decimal("15000"), interest_rate=Decimal("0"), total_days=63, payment_interval_days=7, daily_installment=Decimal("0"), total_payable=Decimal("15000"), start_date=date(2026, 1, 1), end_date=date(2026, 3, 5), created_by_id=admin.id, status="APPROVED")
        db.session.add(loan)
        db.session.commit()
        bank = AccountingAccount.query.filter_by(account_code="1010").one()
        doc_fee = DisbursementChargeType.query.filter_by(code="DOC_FEE").one()
        preview = preview_loan_disbursement(loan, [{"charge_type_id": doc_fee.id, "amount": "400"}], bank)
        assert preview["net_disbursed_amount"] == Decimal("14600.00")
        assert preview["journal_preview"]["balanced"] is True
        credits = {line["account_code"]: line["amount"] for line in preview["journal_preview"]["credits"]}
        assert credits["1010"] == Decimal("14600.00")
        assert credits["4030"] == Decimal("400.00")


def test_missing_charge_account_excluded_from_valid_options_with_warning(app, client):
    with app.app_context():
        seed_disbursement_settings()
        application, _customer = _application()
        application_id = application.id
        doc_fee = DisbursementChargeType.query.filter_by(code="DOC_FEE").one()
        doc_fee.income_account_id = None
        db.session.commit()
        headers = _admin_headers(app)

    response = client.get(f"/admin/loan-applications/{application_id}/disbursement-options", headers=headers)
    assert response.status_code == 200
    payload = response.get_json()
    assert not any(charge["code"] == "DOC_FEE" for charge in payload["charge_types"])
    assert any(warning["code"] == "CHARGE_ACCOUNT_MISSING" and warning["charge_code"] == "DOC_FEE" for warning in payload["warnings"])


def test_disbursement_configuration_status_reports_ready(app, client):
    with app.app_context():
        seed_disbursement_settings()
        headers = _admin_headers(app)

    response = client.get("/admin/disbursement-configuration/status", headers=headers)
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ready"] is True
    assert payload["funding_accounts"] >= 1
    assert payload["required_charge_mappings"]["DOC_FEE"] == {
        "configured": True,
        "destination_account_code": "4030",
    }
    assert payload["missing"] == []
