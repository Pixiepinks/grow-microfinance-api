from datetime import date

from datetime import timedelta

from flask_jwt_extended import create_access_token

from app.extensions import db
from app.models import Investor, InvestorFundingAgreement, InvestorFundingTransaction, User


def _admin():
    user = User(email="investor-admin@example.com", name="Investor Admin", role="admin")
    user.set_password("password")
    db.session.add(user)
    db.session.commit()
    return user


def _headers(app, user):
    with app.app_context():
        token = create_access_token(identity=str(user.id), additional_claims={"role": user.role})
    return {"Authorization": f"Bearer {token}"}


def _payload(**overrides):
    payload = {
        "investor_type": "INDIVIDUAL",
        "full_name": "Prakash Vijayanga Withana",
        "company_name": None,
        "nic": "198900701481",
        "company_registration_number": None,
        "tax_identification_number": "000",
        "mobile": "0703322111",
        "email": "prakashvijayanga@gmail.com",
        "address": "No:213/5, Magammana, Homagama",
        "bank_name": "Bank of Ceylon",
        "bank_branch": "Homagama",
        "bank_account_name": "P V Withanachchi",
        "bank_account_number": "5958235",
        "notes": "Nil",
        "status": "ACTIVE",
    }
    payload.update(overrides)
    return payload


def test_create_individual_investor_without_funding_dependencies(app, client, caplog):
    admin = _admin()
    headers = _headers(app, admin)

    resp = client.post("/admin/investors", headers=headers, json=_payload())

    assert resp.status_code == 201
    body = resp.get_json()
    assert body["id"] == body["investor_id"]
    assert body["investor_number"] == "GROW-INV-000001"
    assert body["display_name"] == "Prakash Vijayanga Withana"
    assert body["bank_account_number"] == "5958235"
    assert "funding_transaction_id" not in body
    assert Investor.query.count() == 1
    assert InvestorFundingAgreement.query.count() == 0
    assert InvestorFundingTransaction.query.count() == 0
    assert "Investor request method=POST path=/admin/investors" in caplog.text


def test_duplicate_nic_returns_structured_validation_not_not_found(app, client):
    admin = _admin()
    headers = _headers(app, admin)
    assert client.post("/admin/investors", headers=headers, json=_payload()).status_code == 201

    resp = client.post("/admin/investors", headers=headers, json=_payload(email="other@example.com"))

    assert resp.status_code == 422
    body = resp.get_json()
    assert body["error"] == "Investor validation failed"
    assert body["fields"]["nic"] == "An investor with this NIC already exists."
    assert "funding record" not in str(body).lower()


def test_get_created_and_unknown_investor_use_investor_messages(app, client):
    admin = _admin()
    headers = _headers(app, admin)
    created = client.post("/admin/investors", headers=headers, json=_payload()).get_json()

    got = client.get(f"/admin/investors/{created['id']}", headers=headers)
    missing = client.get("/admin/investors/999", headers=headers)

    assert got.status_code == 200
    assert got.get_json()["id"] == created["id"]
    assert missing.status_code == 404
    assert missing.get_json() == {"error": "investor_not_found", "message": "The investor was not found."}


def test_unknown_funding_transaction_uses_funding_message(app, client):
    admin = _admin()
    headers = _headers(app, admin)

    resp = client.get("/admin/investor-funding/999", headers=headers)

    assert resp.status_code == 404
    assert resp.get_json() == {
        "error": "investor_funding_not_found",
        "message": "The investor funding record was not found.",
    }


def test_list_investors_returns_compact_summary_without_bank_details(app, client):
    admin = _admin()
    headers = _headers(app, admin)
    client.post("/admin/investors", headers=headers, json=_payload())

    resp = client.get("/admin/investors", headers=headers)

    assert resp.status_code == 200
    item = resp.get_json()["items"][0]
    assert item["investor_number"] == "GROW-INV-000001"
    assert item["active_agreements"] == 0
    assert item["principal_balance"] == 0
    assert item["accrued_interest"] == 0
    assert "bank_account_number" not in item


def test_generate_investor_number_continues_after_existing_highest(app):
    from app.investor_funding import generate_investor_number

    existing = Investor(
        investor_number="GROW-INV-000004",
        investor_type="INDIVIDUAL",
        full_name="Existing Investor",
        status="ACTIVE",
    )
    db.session.add(existing)
    db.session.flush()

    assert generate_investor_number() == "GROW-INV-000005"


def test_generate_investor_number_ignores_malformed_legacy_number(app):
    from app.investor_funding import generate_investor_number

    db.session.add(
        Investor(
            investor_number="MANUAL-LEGACY",
            investor_type="INDIVIDUAL",
            full_name="Legacy Investor",
            status="ACTIVE",
        )
    )
    db.session.add(
        Investor(
            investor_number="GROW-INV-000004",
            investor_type="INDIVIDUAL",
            full_name="Valid Investor",
            status="ACTIVE",
        )
    )
    db.session.flush()

    assert generate_investor_number() == "GROW-INV-000005"


def test_investor_options_returns_active_individual_without_agreement_or_balance(app, client):
    admin = _admin()
    headers = _headers(app, admin)
    created = client.post("/admin/investors", headers=headers, json=_payload()).get_json()

    resp = client.get("/admin/investors/options", headers=headers)

    assert resp.status_code == 200
    assert resp.get_json() == {
        "items": [
            {
                "id": created["id"],
                "investor_id": created["id"],
                "investor_number": "GROW-INV-000001",
                "investor_type": "INDIVIDUAL",
                "display_name": "Prakash Vijayanga Withana",
                "full_name": "Prakash Vijayanga Withana",
                "company_name": None,
                "nic": "198900701481",
                "status": "ACTIVE",
                "label": "GROW-INV-000001 — Prakash Vijayanga Withana",
            }
        ]
    }
    assert InvestorFundingAgreement.query.count() == 0


def test_investor_options_excludes_inactive_investors(app, client):
    admin = _admin()
    headers = _headers(app, admin)
    client.post("/admin/investors", headers=headers, json=_payload(status="INACTIVE"))

    resp = client.get("/admin/investors/options", headers=headers)

    assert resp.status_code == 200
    assert resp.get_json() == {"items": []}


def test_investor_options_company_label_uses_company_name(app, client):
    admin = _admin()
    headers = _headers(app, admin)
    resp = client.post(
        "/admin/investors",
        headers=headers,
        json=_payload(
            investor_type="COMPANY",
            full_name=None,
            company_name="Grow Capital Pvt Ltd",
            nic=None,
            company_registration_number="PV-123",
        ),
    )
    assert resp.status_code == 201

    options = client.get("/admin/investors/options", headers=headers).get_json()["items"]

    assert options[0]["display_name"] == "Grow Capital Pvt Ltd"
    assert options[0]["label"] == "GROW-INV-000001 — Grow Capital Pvt Ltd"


def test_list_investors_includes_total_and_normalized_contract(app, client):
    admin = _admin()
    headers = _headers(app, admin)
    created = client.post("/admin/investors", headers=headers, json=_payload()).get_json()

    resp = client.get("/admin/investors", headers=headers)

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["total"] == 1
    item = body["items"][0]
    for field in ["id", "investor_id", "investor_number", "investor_type", "display_name", "full_name", "company_name", "nic", "mobile", "status"]:
        assert field in item
    assert item["id"] == created["id"]


def test_list_investors_includes_investor_without_agreement(app, client):
    admin = _admin()
    headers = _headers(app, admin)
    created = client.post("/admin/investors", headers=headers, json=_payload()).get_json()

    body = client.get("/admin/investors", headers=headers).get_json()

    assert body["total"] == 1
    assert body["items"][0]["id"] == created["id"]
    assert body["items"][0]["active_agreements"] == 0


def test_list_investors_includes_zero_principal_investor(app, client):
    admin = _admin()
    headers = _headers(app, admin)
    _seed_investor_agreement_dependencies()
    investor = client.post("/admin/investors", headers=headers, json=_payload()).get_json()
    _create_active_agreement(client, headers, investor["id"], original_principal_amount=0)

    items = client.get("/admin/investors", headers=headers).get_json()["items"]

    assert items[0]["investor_id"] == investor["id"]
    assert items[0]["principal_balance"] == 0


def test_list_investors_includes_active_agreement_count(app, client):
    admin = _admin()
    headers = _headers(app, admin)
    _seed_investor_agreement_dependencies()
    investor = client.post("/admin/investors", headers=headers, json=_payload()).get_json()
    _create_active_agreement(client, headers, investor["id"])

    item = client.get("/admin/investors", headers=headers).get_json()["items"][0]

    assert item["active_agreements"] == 1


def test_list_investors_defaults_to_all_statuses(app, client):
    admin = _admin()
    headers = _headers(app, admin)
    client.post("/admin/investors", headers=headers, json=_payload(nic="1", email="active@example.com"))
    client.post("/admin/investors", headers=headers, json=_payload(nic="2", email="inactive@example.com", status="INACTIVE"))

    items = client.get("/admin/investors", headers=headers).get_json()["items"]

    assert {item["status"] for item in items} == {"ACTIVE", "INACTIVE"}


def test_list_investors_filters_active_status(app, client):
    admin = _admin()
    headers = _headers(app, admin)
    active = client.post("/admin/investors", headers=headers, json=_payload(nic="1", email="active@example.com")).get_json()
    client.post("/admin/investors", headers=headers, json=_payload(nic="2", email="inactive@example.com", status="INACTIVE"))

    items = client.get("/admin/investors?status=ACTIVE", headers=headers).get_json()["items"]

    assert [item["id"] for item in items] == [active["id"]]
    assert all(item["status"] == "ACTIVE" for item in items)


def test_list_investors_searches_by_investor_number(app, client):
    admin = _admin()
    headers = _headers(app, admin)
    created = client.post("/admin/investors", headers=headers, json=_payload()).get_json()

    items = client.get("/admin/investors?q=grow-inv-000001", headers=headers).get_json()["items"]

    assert [item["id"] for item in items] == [created["id"]]


def test_list_investors_expired_token_returns_401_json(app, client):
    admin = _admin()
    with app.app_context():
        token = create_access_token(
            identity=str(admin.id),
            additional_claims={"role": admin.role},
            expires_delta=timedelta(seconds=-1),
        )

    resp = client.get("/admin/investors", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert resp.is_json
    assert "items" not in resp.get_json()


def test_agreement_create_resolves_active_investor_by_investor_id(app, client):
    from app.accounting import seed_default_accounts
    from app.investor_funding import seed_investor_accounts

    admin = _admin()
    headers = _headers(app, admin)
    seed_default_accounts()
    seed_investor_accounts()
    db.session.commit()
    investor = client.post("/admin/investors", headers=headers, json=_payload()).get_json()

    resp = client.post(
        "/admin/investor-agreements",
        headers=headers,
        json={"investor_id": investor["id"], "agreement_name": "Test Agreement", "interest_rate": "12.5"},
    )

    assert resp.status_code == 201
    assert resp.get_json()["investor"]["id"] == investor["id"]


def test_agreement_create_returns_contract_for_missing_or_inactive_investor(app, client):
    from app.accounting import seed_default_accounts
    from app.investor_funding import seed_investor_accounts

    admin = _admin()
    headers = _headers(app, admin)
    seed_default_accounts()
    seed_investor_accounts()
    db.session.commit()

    missing = client.post("/admin/investor-agreements", headers=headers, json={"investor_id": 999})
    assert missing.status_code == 404
    assert missing.get_json() == {"error": "investor_not_found", "message": "The selected investor was not found."}

    inactive = client.post("/admin/investors", headers=headers, json=_payload(status="INACTIVE")).get_json()
    inactive_agreement = client.post("/admin/investor-agreements", headers=headers, json={"investor_id": inactive["id"]})
    assert inactive_agreement.status_code == 422
    assert inactive_agreement.get_json() == {"error": "investor_inactive", "message": "The selected investor is not active."}


def test_create_agreement_does_not_require_funding_transaction_or_post_journal(app, client, caplog):
    from app.accounting import seed_default_accounts
    from app.investor_funding import seed_investor_accounts
    from app.models import AccountingJournalEntry

    admin = _admin()
    headers = _headers(app, admin)
    seed_default_accounts()
    seed_investor_accounts()
    db.session.commit()
    investor = client.post("/admin/investors", headers=headers, json=_payload(nic="198900701482", email="agreement@example.com")).get_json()

    resp = client.post(
        "/admin/investor-agreements",
        headers=headers,
        json={
            "investor_id": investor["id"],
            "agreement_name": "Monthly Investor Funding Agreement",
            "agreement_date": "2026-02-20",
            "start_date": "2026-02-20",
            "original_expected_principal": 50000,
            "interest_rate": 2,
            "interest_rate_period": "MONTHLY",
            "calculation_method": "MONTHLY_AVERAGE_DAILY_BALANCE",
            "compounding_method": "CAPITALIZE_MONTHLY",
            "allow_partial_repayment": True,
            "status": "ACTIVE",
        },
    )

    assert resp.status_code == 201
    body = resp.get_json()
    assert body["id"] == body["agreement_id"]
    assert body["investor_id"] == investor["id"]
    assert body["original_principal_amount"] == 50000.0
    assert body["current_principal_balance"] == 0.0
    assert body["interest_rate"] == 2.0
    assert body["interest_rate_period"] == "MONTHLY"
    assert body["agreement_number"].startswith("GROW-IFA-20260220-")
    assert InvestorFundingAgreement.query.count() == 1
    assert InvestorFundingTransaction.query.count() == 0
    assert AccountingJournalEntry.query.count() == 0
    assert "Investor agreement request method=POST path=/admin/investor-agreements" in caplog.text


def test_create_agreement_invalid_liability_account_is_account_specific(app, client):
    from app.accounting import seed_default_accounts
    from app.investor_funding import seed_investor_accounts
    from app.models import AccountingAccount

    admin = _admin()
    headers = _headers(app, admin)
    seed_default_accounts()
    seed_investor_accounts()
    db.session.commit()
    investor = client.post("/admin/investors", headers=headers, json=_payload(nic="198900701483", email="badaccount@example.com")).get_json()
    asset = AccountingAccount.query.filter_by(account_type="ASSET").first()

    resp = client.post(
        "/admin/investor-agreements",
        headers=headers,
        json={"investor_id": investor["id"], "investor_liability_account_id": asset.id},
    )

    assert resp.status_code == 422
    assert resp.get_json() == {
        "error": "account_mapping_invalid",
        "message": "investor_liability_account_id must be a liability account.",
    }


def _seed_investor_agreement_dependencies():
    from app.accounting import seed_default_accounts
    from app.investor_funding import seed_investor_accounts

    seed_default_accounts()
    seed_investor_accounts()
    db.session.commit()


def _create_active_agreement(client, headers, investor_id, **overrides):
    payload = {
        "investor_id": investor_id,
        "agreement_name": "Monthly Investor Funding Agreement",
        "agreement_date": "2026-02-20",
        "start_date": "2026-02-20",
        "original_principal_amount": 50000,
        "interest_rate": 2,
        "status": "ACTIVE",
    }
    payload.update(overrides)
    return client.post("/admin/investor-agreements", headers=headers, json=payload)


def test_agreement_options_returns_zero_principal_active_agreement_without_transactions(app, client):
    admin = _admin()
    headers = _headers(app, admin)
    _seed_investor_agreement_dependencies()
    investor = client.post("/admin/investors", headers=headers, json=_payload()).get_json()
    agreement = _create_active_agreement(client, headers, investor["id"]).get_json()

    resp = client.get("/admin/investor-agreements/options", headers=headers)

    assert resp.status_code == 200
    assert InvestorFundingTransaction.query.count() == 0
    assert resp.get_json()["items"] == [
        {
            "id": agreement["id"],
            "agreement_id": agreement["id"],
            "agreement_number": agreement["agreement_number"],
            "agreement_name": "Monthly Investor Funding Agreement",
            "investor_id": investor["id"],
            "investor_number": "GROW-INV-000001",
            "investor_name": "Prakash Vijayanga Withana",
            "status": "ACTIVE",
            "start_date": "2026-02-20",
            "maturity_date": None,
            "original_principal_amount": 50000.0,
            "current_principal_balance": 0.0,
            "allow_additional_funding": True,
            "funding_account_id": agreement["account_mappings"]["funding_account_id"],
            "investor_liability_account_id": agreement["account_mappings"]["investor_liability_account_id"],
            "label": f"{agreement['agreement_number']} — Monthly Investor Funding Agreement",
        }
    ]


def test_agreement_options_filters_by_investor_id(app, client):
    admin = _admin()
    headers = _headers(app, admin)
    _seed_investor_agreement_dependencies()
    first = client.post("/admin/investors", headers=headers, json=_payload(nic="1", email="one@example.com")).get_json()
    second = client.post("/admin/investors", headers=headers, json=_payload(nic="2", email="two@example.com", mobile="0700000002")).get_json()
    first_agreement = _create_active_agreement(client, headers, first["id"], agreement_name="First").get_json()
    _create_active_agreement(client, headers, second["id"], agreement_name="Second")

    resp = client.get(f"/admin/investor-agreements/options?investor_id={first['id']}", headers=headers)

    assert resp.status_code == 200
    items = resp.get_json()["items"]
    assert [item["agreement_id"] for item in items] == [first_agreement["id"]]
    assert items[0]["investor_id"] == first["id"]


def test_agreement_options_excludes_closed_agreement_and_inactive_investor(app, client):
    admin = _admin()
    headers = _headers(app, admin)
    _seed_investor_agreement_dependencies()
    active = client.post("/admin/investors", headers=headers, json=_payload(nic="3", email="active@example.com")).get_json()
    inactive = client.post("/admin/investors", headers=headers, json=_payload(nic="4", email="inactive@example.com", status="INACTIVE")).get_json()
    _create_active_agreement(client, headers, active["id"], status="CLOSED")
    inactive_agreement = InvestorFundingAgreement(
        agreement_number="GROW-IFA-20260220-9999",
        investor_id=inactive["id"],
        agreement_name="Inactive investor agreement",
        agreement_date=date(2026, 2, 20),
        start_date=date(2026, 2, 20),
        original_principal_amount=50000,
        current_principal_balance=0,
        investor_liability_account_id=1,
        interest_expense_account_id=1,
        accrued_interest_payable_account_id=1,
        status="ACTIVE",
    )
    db.session.add(inactive_agreement)
    db.session.commit()

    resp = client.get("/admin/investor-agreements/options", headers=headers)

    assert resp.status_code == 200
    assert resp.get_json() == {"items": []}


def test_agreement_options_preflight_then_get_returns_items(app, client):
    admin = _admin()
    headers = _headers(app, admin)
    _seed_investor_agreement_dependencies()
    investor = client.post("/admin/investors", headers=headers, json=_payload()).get_json()
    _create_active_agreement(client, headers, investor["id"])

    preflight = client.open("/admin/investor-agreements/options", method="OPTIONS")
    resp = client.get("/admin/investor-agreements/options", headers=headers)

    assert preflight.status_code == 204
    assert resp.status_code == 200
    assert len(resp.get_json()["items"]) == 1


def test_agreement_list_returns_zero_principal_null_maturity_agreement_without_transactions(app, client):
    admin = _admin()
    headers = _headers(app, admin)
    _seed_investor_agreement_dependencies()
    investor = client.post("/admin/investors", headers=headers, json=_payload()).get_json()
    agreement = _create_active_agreement(
        client,
        headers,
        investor["id"],
        agreement_name="Monthly Investor Funding Agreement - Prakash",
        calculation_method="MONTHLY_AVERAGE_DAILY_BALANCE",
        maturity_date=None,
    ).get_json()

    resp = client.get("/admin/investor-agreements", headers=headers)

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["id"] == agreement["id"]
    assert item["agreement_id"] == agreement["id"]
    assert item["agreement_number"] == agreement["agreement_number"]
    assert item["agreement_name"] == "Monthly Investor Funding Agreement - Prakash"
    assert item["investor_id"] == investor["id"]
    assert item["investor"] == {
        "id": investor["id"],
        "investor_number": "GROW-INV-000001",
        "display_name": "Prakash Vijayanga Withana",
    }
    assert item["investor_name"] == "Prakash Vijayanga Withana"
    assert item["start_date"] == "2026-02-20"
    assert item["maturity_date"] is None
    assert item["original_principal_amount"] == 50000.0
    assert item["current_principal_balance"] == 0
    assert item["interest_rate"] == 2.0
    assert item["interest_rate_period"] == "MONTHLY"
    assert item["calculation_method"] == "MONTHLY_AVERAGE_DAILY_BALANCE"
    assert item["accrued_interest"] == 0
    assert item["status"] == "ACTIVE"
    assert InvestorFundingTransaction.query.count() == 0


def test_agreement_list_defaults_to_all_statuses_and_can_filter_active(app, client):
    admin = _admin()
    headers = _headers(app, admin)
    _seed_investor_agreement_dependencies()
    investor = client.post("/admin/investors", headers=headers, json=_payload()).get_json()
    active = _create_active_agreement(client, headers, investor["id"], agreement_name="Active", status="ACTIVE").get_json()
    draft = _create_active_agreement(client, headers, investor["id"], agreement_name="Draft", status="DRAFT").get_json()
    closed = _create_active_agreement(client, headers, investor["id"], agreement_name="Closed", status="CLOSED").get_json()

    default_resp = client.get("/admin/investor-agreements", headers=headers)
    active_resp = client.get("/admin/investor-agreements?status=ACTIVE", headers=headers)

    assert default_resp.status_code == 200
    assert {item["agreement_id"] for item in default_resp.get_json()["items"]} == {active["id"], draft["id"], closed["id"]}
    assert active_resp.status_code == 200
    assert [item["agreement_id"] for item in active_resp.get_json()["items"]] == [active["id"]]


def test_agreement_list_optional_filters_search_investor_and_date(app, client):
    admin = _admin()
    headers = _headers(app, admin)
    _seed_investor_agreement_dependencies()
    investor = client.post("/admin/investors", headers=headers, json=_payload()).get_json()
    agreement = _create_active_agreement(client, headers, investor["id"], agreement_name="Searchable Agreement").get_json()

    resp = client.get(
        f"/admin/investor-agreements?q=Prakash&investor_id={investor['id']}&date_from=2026-02-20&date_to=2026-02-20",
        headers=headers,
    )

    assert resp.status_code == 200
    assert resp.get_json()["total"] == 1
    assert resp.get_json()["items"][0]["agreement_id"] == agreement["id"]
