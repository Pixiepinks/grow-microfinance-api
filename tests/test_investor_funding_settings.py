from flask_jwt_extended import create_access_token

from app.extensions import db
from app.models import AccountingAccount, AccountingSetting, User
from app.accounting import seed_default_accounts


def _admin():
    user = User(email="investor-settings-admin@example.com", name="Investor Settings Admin", role="admin")
    user.set_password("pass")
    db.session.add(user)
    db.session.commit()
    return user


def _headers(app, user):
    with app.app_context():
        token = create_access_token(identity=str(user.id), additional_claims={"role": user.role})
    return {"Authorization": f"Bearer {token}"}


def test_get_investor_funding_settings_route_exists_and_configured(app, client):
    admin = _admin()
    seed_default_accounts()
    db.session.commit()

    resp = client.get("/admin/accounting/settings/investor-funding", headers=_headers(app, admin))

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["configured"] is True
    assert body["investor_borrowings_control_account_id"] == AccountingAccount.query.filter_by(account_code="2300").first().id
    assert body["accounts"]["investor_interest_expense"]["account_code"] == "5100"
    assert body["missing_settings"] == []


def test_get_investor_funding_settings_reports_missing_mapping(app, client):
    admin = _admin()
    seed_default_accounts()
    expense = AccountingAccount.query.filter_by(account_role="INVESTOR_INTEREST_EXPENSE").first()
    db.session.delete(expense)
    AccountingSetting.query.filter_by(setting_key="investor_interest_expense_account").delete()
    AccountingSetting.query.filter_by(setting_key="INVESTOR_INTEREST_EXPENSE").delete()
    db.session.commit()

    resp = client.get("/admin/accounting/settings/investor-funding", headers=_headers(app, admin))

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["configured"] is False
    assert "investor_interest_expense_account" in body["missing_settings"]
    assert body["message"] == "Investor funding accounting configuration is incomplete."


def test_get_investor_funding_settings_requires_auth(client):
    resp = client.get("/admin/accounting/settings/investor-funding")
    assert resp.status_code == 401


def test_investor_funding_settings_options_preflight(client):
    resp = client.options("/admin/accounting/settings/investor-funding")
    assert resp.status_code == 204


def test_patch_investor_funding_settings_validates_and_saves(app, client):
    admin = _admin()
    seed_default_accounts()
    db.session.commit()
    accounts = {a.account_code: a for a in AccountingAccount.query.all()}
    payload = {
        "investor_borrowings_control_account_id": accounts["2300"].id,
        "investor_interest_expense_account_id": accounts["5100"].id,
        "investor_interest_payable_account_id": accounts["2310"].id,
        "investor_withholding_tax_payable_account_id": accounts["2320"].id,
        "default_investor_funding_bank_account_id": accounts["1010"].id,
        "allow_interest_capitalization": False,
    }

    resp = client.patch("/admin/accounting/settings/investor-funding", headers=_headers(app, admin), json=payload)

    assert resp.status_code == 200
    assert resp.get_json()["configured"] is True
    assert AccountingSetting.query.filter_by(setting_key="investor_interest_expense_account").first().setting_value == str(accounts["5100"].id)


def test_patch_investor_funding_settings_rejects_asset_expense_account(app, client):
    admin = _admin()
    seed_default_accounts()
    db.session.commit()
    asset = AccountingAccount.query.filter_by(account_code="1010").first()

    resp = client.patch(
        "/admin/accounting/settings/investor-funding",
        headers=_headers(app, admin),
        json={"investor_interest_expense_account_id": asset.id},
    )

    assert resp.status_code == 422
