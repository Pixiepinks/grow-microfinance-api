from datetime import date
from decimal import Decimal

from flask_jwt_extended import create_access_token

from app.accounting import seed_default_accounts
from app.extensions import db
from app.investor_funding import create_agreement, create_investor, record_funding
from app.models import AccountingAccount, AccountingJournalEntry, InvestorInterestAccrual, User


def auth_headers(app):
    user = User(email="accrual-admin@example.com", name="Accrual Admin", role="admin")
    user.set_password("password")
    db.session.add(user)
    db.session.commit()
    with app.app_context():
        token = create_access_token(identity=str(user.id), additional_claims={"role": "admin"})
    return {"Authorization": f"Bearer {token}"}


def funded_agreement(name="July Investor", status="ACTIVE", start_date="2026-07-01"):
    seed_default_accounts()
    investor = create_investor({"full_name": name})
    db.session.flush()
    agreement = create_agreement({
        "investor_id": investor.id,
        "agreement_date": start_date,
        "start_date": start_date,
        "interest_rate": "2",
        "status": "ACTIVE",
    })
    db.session.flush()
    record_funding(agreement.id, {"transaction_date": start_date, "amount": "100000"})
    agreement.status = status
    db.session.commit()
    return agreement


def test_canonical_route_is_registered_and_validation_is_json(app, client):
    rule = next(rule for rule in app.url_map.iter_rules() if rule.rule == "/admin/investor-funding/interest-accruals/run")
    assert "POST" in rule.methods
    headers = auth_headers(app)
    response = client.post(rule.rule, json={"accrual_month": "July 2026"}, headers=headers)
    assert response.status_code == 422
    assert response.is_json
    assert response.get_json() == {
        "error": "invalid_accrual_month",
        "message": "Provide the accrual month in YYYY-MM format.",
    }


def test_blank_missing_and_null_agreement_preview_all_without_writes(app, client):
    headers = auth_headers(app)
    first = funded_agreement("First Investor")
    second = funded_agreement("Second Investor")
    journals_before = AccountingJournalEntry.query.count()
    payable = AccountingAccount.query.filter_by(account_code="2310").one()
    payable_lines_before = sum(len(j.lines) for j in payable.journal_lines) if hasattr(payable, "journal_lines") else 0

    payloads = [
        {"accrual_month": "2026-07", "agreement_id": "", "preview_only": True, "post": False},
        {"accrual_month": "2026-07", "preview_only": True},
        {"month": "2026-07", "agreement_id": None, "preview_only": True},
    ]
    for payload in payloads:
        response = client.post("/admin/investor-funding/interest-accruals/run", json=payload, headers=headers)
        assert response.status_code == 200
        body = response.get_json()
        assert body["preview_only"] is True
        assert {row["agreement_id"] for row in body["items"]} == {first.id, second.id}
        assert body["agreements_requiring_accrual"] == 2
        assert body["total_accrued_interest"] == "4000.00"
    assert InvestorInterestAccrual.query.count() == 0
    assert AccountingJournalEntry.query.count() == journals_before
    if hasattr(payable, "journal_lines"):
        assert sum(len(j.lines) for j in payable.journal_lines) == payable_lines_before


def test_specific_agreement_errors_and_filtering(app, client):
    headers = auth_headers(app)
    first = funded_agreement("First Investor")
    funded_agreement("Second Investor")
    response = client.post("/admin/investor-funding/interest-accruals/run", json={
        "accrual_month": "2026-07", "agreement_id": first.id, "preview_only": True,
    }, headers=headers)
    assert response.status_code == 200
    assert [row["agreement_id"] for row in response.get_json()["items"]] == [first.id]

    invalid = client.post("/admin/investor-funding/interest-accruals/run", json={
        "accrual_month": "2026-07", "agreement_id": "trf", "preview_only": True,
    }, headers=headers)
    assert invalid.status_code == 422
    assert invalid.get_json()["error"] == "invalid_agreement"
    missing = client.post("/admin/investor-funding/interest-accruals/run", json={
        "accrual_month": "2026-07", "agreement_id": 999999, "preview_only": True,
    }, headers=headers)
    assert missing.status_code == 404
    assert missing.get_json()["error"] == "funding_agreement_not_found"


def test_post_is_balanced_duplicate_safe_and_visible_in_list_and_summary(app, client):
    headers = auth_headers(app)
    agreement = funded_agreement()
    funding_journals = AccountingJournalEntry.query.count()
    payload = {"accrual_month": "2026-07", "agreement_id": None, "preview_only": False, "post": True}
    response = client.post("/admin/investor-funding/interest-accruals/run", json=payload, headers=headers)
    assert response.status_code == 200
    body = response.get_json()
    assert body["items"][0]["status"] == "POSTED"
    assert InvestorInterestAccrual.query.count() == 1
    assert AccountingJournalEntry.query.count() == funding_journals + 1
    journal = InvestorInterestAccrual.query.one().journal_entry
    assert sum(line.debit for line in journal.lines) == Decimal("2000.00")
    assert sum(line.credit for line in journal.lines) == Decimal("2000.00")
    assert {line.account.account_code for line in journal.lines} == {"2310", "5100"}

    duplicate = client.post("/admin/investor-funding/interest-accruals/run", json=payload, headers=headers)
    assert duplicate.status_code == 200
    assert duplicate.get_json()["items"][0]["status"] == "ALREADY_POSTED"
    assert InvestorInterestAccrual.query.count() == 1
    assert AccountingJournalEntry.query.count() == funding_journals + 1

    listed = client.get("/admin/investor-funding/interest-accruals?month=2026-07", headers=headers).get_json()
    assert listed["total"] == 1
    assert listed["items"][0]["agreement_id"] == agreement.id
    summary = client.get("/admin/investor-funding/interest-accruals/summary?month=2026-07", headers=headers).get_json()
    assert summary["accruals_posted_this_month"] == 1
    assert summary["accruals_awaiting_payment"] == 1


def test_inactive_and_not_started_agreements_are_excluded(app, client):
    headers = auth_headers(app)
    inactive = funded_agreement("Inactive Investor", status="INACTIVE")
    future = funded_agreement("Future Investor", start_date="2026-08-01")
    response = client.post("/admin/investor-funding/interest-accruals/run", json={
        "accrual_month": "2026-07", "preview_only": True,
    }, headers=headers)
    body = response.get_json()
    assert body["items"] == []
    assert {(item["agreement_id"], item["reason_code"]) for item in body["exceptions"]} == {
        (inactive.id, "AGREEMENT_INACTIVE"), (future.id, "AGREEMENT_NOT_STARTED")
    }
