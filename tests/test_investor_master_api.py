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


def test_list_masks_bank_account_number(app, client):
    admin = _admin()
    headers = _headers(app, admin)
    client.post("/admin/investors", headers=headers, json=_payload())

    resp = client.get("/admin/investors", headers=headers)

    assert resp.status_code == 200
    assert resp.get_json()["items"][0]["bank_account_number"] == "***8235"
