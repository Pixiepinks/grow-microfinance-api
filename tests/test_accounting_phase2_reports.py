from datetime import date
from decimal import Decimal

from flask_jwt_extended import create_access_token

from app.accounting import seed_default_accounts
from app.extensions import db
from app.models import AccountingAccount, User


def _user(role="admin"):
    u = User(email=f"phase2-{role}@example.com", name=role, role=role)
    u.set_password("password")
    db.session.add(u); db.session.commit(); return u


def _headers(app, user):
    with app.app_context():
        token = create_access_token(identity=str(user.id), additional_claims={"role": user.role})
    return {"Authorization": f"Bearer {token}"}


def _post(client, headers, journal_date, lines, status="POSTED", description="report test"):
    resp = client.post("/admin/accounting/journals", headers=headers, json={"journal_date": journal_date.isoformat(), "description": description, "status": status, "lines": lines})
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()


def _acct(code):
    return AccountingAccount.query.filter_by(account_code=code).first()


def test_trial_balance_posted_draft_reversal_opening_comparative_and_export(app, client):
    admin = _user(); headers = _headers(app, admin); seed_default_accounts(); db.session.commit()
    bank = _acct("1010"); income = _acct("4000"); rent = _acct("5010")
    _post(client, headers, date(2026, 6, 30), [{"account_id": bank.id, "debit":"200.00"}, {"account_id": income.id, "credit":"200.00"}], description="opening income")
    j = _post(client, headers, date(2026, 7, 1), [{"account_id": rent.id, "debit":"50.00"}, {"account_id": bank.id, "credit":"50.00"}], description="rent")
    _post(client, headers, date(2026, 7, 1), [{"account_id": rent.id, "debit":"999.00"}, {"account_id": bank.id, "credit":"999.00"}], status="DRAFT", description="draft")
    rev = client.post(f"/admin/accounting/journals/{j['id']}/reverse", headers=headers, json={"journal_date":"2026-07-02","reason":"void"})
    assert rev.status_code == 201

    resp = client.get("/admin/accounting/reports/trial-balance?date_from=2026-07-01&as_of_date=2026-07-31&comparative_as_of_date=2026-06-30", headers=headers)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["totals"]["is_balanced"] is True
    assert body["totals"]["total_period_debit"] == "100.00"
    assert body["totals"]["total_period_credit"] == "100.00"
    assert any(r["account_code"] == "4000" and r["opening_credit"] == "200.00" for r in body["accounts"])
    assert all(r["period_debit"] != "999.00" for r in body["accounts"])
    assert body["validation"]["is_valid"] is True
    csv_resp = client.get("/admin/accounting/reports/trial-balance/export.csv?as_of_date=2026-07-31", headers=headers)
    assert csv_resp.status_code == 200 and b"TRIAL_BALANCE" in csv_resp.data


def test_income_statement_and_financial_position_summary_drilldown(app, client):
    admin = _user(); headers = _headers(app, admin); seed_default_accounts(); db.session.commit()
    bank = _acct("1010"); capital = _acct("3000"); income = _acct("4000"); rent = _acct("5010")
    _post(client, headers, date(2026, 7, 1), [{"account_id": bank.id, "debit":"1000.00"}, {"account_id": capital.id, "credit":"1000.00"}], description="capital")
    _post(client, headers, date(2026, 7, 2), [{"account_id": bank.id, "debit":"250.00"}, {"account_id": income.id, "credit":"250.00"}], description="interest")
    _post(client, headers, date(2026, 7, 3), [{"account_id": rent.id, "debit":"40.00"}, {"account_id": bank.id, "credit":"40.00"}], description="rent")

    is_resp = client.get("/admin/accounting/reports/income-statement?date_from=2026-07-01&date_to=2026-07-31&comparative_date_from=2026-06-01&comparative_date_to=2026-06-30", headers=headers)
    assert is_resp.status_code == 200
    is_body = is_resp.get_json()
    assert is_body["income"]["total_income"] == "250.00"
    assert is_body["expenses"]["total_expenses"] == "40.00"
    assert is_body["net_profit"] == "210.00"
    assert is_body["validation"]["is_valid"] is True

    sfp = client.get("/admin/accounting/reports/statement-of-financial-position?as_of_date=2026-07-31", headers=headers)
    assert sfp.status_code == 200
    sfp_body = sfp.get_json()
    assert sfp_body["assets"]["total_assets"] == "1210.00"
    assert sfp_body["equity"]["current_period_profit_loss"] == "210.00"
    assert sfp_body["is_balanced"] is True
    assert all(a["account_code"] != "4000" for a in sfp_body["equity"]["accounts"])

    summary = client.get("/admin/accounting/reports/summary?date_from=2026-07-01&date_to=2026-07-31&as_of_date=2026-07-31", headers=headers)
    assert summary.status_code == 200 and summary.get_json()["net_profit"] == "210.00"
    drill = client.get(f"/admin/accounting/reports/account-drilldown?account_id={bank.id}&date_to=2026-07-31", headers=headers)
    assert drill.status_code == 200 and len(drill.get_json()["transactions"]) == 3
    csv_resp = client.get("/admin/accounting/reports/income-statement/export.csv?date_from=2026-07-01&date_to=2026-07-31", headers=headers)
    assert csv_resp.status_code == 200 and b"Rs. 250.00" in csv_resp.data


def test_report_exports_reject_unauthorized(app, client):
    resp = client.get("/admin/accounting/reports/trial-balance/export.csv?as_of_date=2026-07-31")
    assert resp.status_code in (401, 422)


def test_phase2_disbursement_report_contracts_and_overdraft_presentation(app, client):
    from app.accounting import post_loan_disbursement
    from app.models import Customer, Loan

    admin = _user(); headers = _headers(app, admin); seed_default_accounts(); db.session.commit()
    cu = User(email="phase2-customer@example.com", name="Phase2 Customer", role="customer")
    cu.set_password("password")
    db.session.add(cu); db.session.commit()
    customer = Customer(user_id=cu.id, customer_code="C-P2", full_name="Phase Two Customer")
    db.session.add(customer); db.session.commit()
    loan = Loan(loan_number="GROW-LOAN-20260710-0001", customer_id=customer.id, principal_amount=Decimal("50000.00"), interest_rate=Decimal("12.00"), total_days=30, payment_interval_days=30, daily_installment=Decimal("0.00"), total_payable=Decimal("50000.00"), start_date=date(2026, 7, 10), end_date=date(2026, 8, 9), status="Active", created_by_id=admin.id)
    db.session.add(loan); db.session.commit()
    post_loan_disbursement(loan, admin.id, disbursement_date=date(2026, 7, 10)); db.session.commit()

    receivable = _acct("1100")
    gl = client.get(f"/admin/accounting/general-ledger?account_id={receivable.id}&date_from=&date_to=&customer_id=&loan_id=", headers=headers)
    assert gl.status_code == 200
    gl_body = gl.get_json()
    assert gl_body["total_debit"] == "50000.00"
    assert gl_body["total_credit"] == "0.00"
    assert gl_body["closing_balance"] == gl_body["transactions"][-1]["running_balance"] == "50000.00"
    assert gl_body["transactions"][0]["customer_name"] == "Phase Two Customer"
    assert gl_body["transactions"][0]["loan_number"] == "GROW-LOAN-20260710-0001"

    sfp = client.get("/admin/accounting/reports/statement-of-financial-position?as_of_date=2026-07-31", headers=headers)
    assert sfp.status_code == 200
    sfp_body = sfp.get_json()
    current_assets = sfp_body["assets"]["current_assets"]["accounts"]
    current_liabilities = sfp_body["liabilities"]["current_liabilities"]["accounts"]
    assert any(a["account_code"] == "1100" and a["amount"] == "50000.00" for a in current_assets)
    assert any(a["account_code"] == "1010" and a["amount"] == "50000.00" and a["presentation_adjustment"] == "BANK_OVERDRAFT_RECLASSIFICATION" for a in current_liabilities)
    assert sfp_body["has_activity"] is True
    assert sfp_body["is_empty"] is False
    assert sfp_body["financial_position_balanced"] is True

    summary = client.get("/admin/accounting/reports/summary?date_from=2026-07-01&date_to=2026-07-31&as_of_date=2026-07-31", headers=headers)
    assert summary.status_code == 200
    summary_body = summary.get_json()
    assert summary_body["trial_balance_difference"] == "0.00"
    assert summary_body["trial_balance_balanced"] is True
    assert summary_body["financial_position_balanced"] is True
    assert summary_body["net_profit_loss"] == "0.00"


def test_reconciliation_schema_counts_and_warning_consistency(app, client):
    from app.models import Customer, Loan, Payment

    admin = _user(); headers = _headers(app, admin); seed_default_accounts(); db.session.commit()
    cu = User(email="phase2-missing@example.com", name="Missing Journal Customer", role="customer")
    cu.set_password("password")
    db.session.add(cu); db.session.commit()
    customer = Customer(user_id=cu.id, customer_code="C-MISS", full_name="Missing Journal Customer")
    db.session.add(customer); db.session.commit()
    loan = Loan(loan_number="LN-MISSING", customer_id=customer.id, principal_amount=Decimal("100.00"), interest_rate=Decimal("12.00"), total_days=30, payment_interval_days=30, daily_installment=Decimal("0.00"), total_payable=Decimal("100.00"), start_date=date(2026, 7, 10), end_date=date(2026, 8, 9), status="Active", created_by_id=admin.id)
    db.session.add(loan); db.session.commit()
    payment = Payment(loan_id=loan.id, amount_collected=Decimal("10.00"), collection_date=date(2026, 7, 11), collected_by_id=admin.id)
    db.session.add(payment); db.session.commit()

    resp = client.get("/admin/accounting/reconciliation/issues", headers=headers)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["total"] == len(body["issues"])
    assert body["counts_by_severity"]["WARNING"] >= 2
    issue = next(i for i in body["issues"] if i["issue_type"] == "MISSING_DISBURSEMENT_JOURNAL")
    for field in ["id", "issue_type", "severity", "source_type", "source_id", "source_reference", "description", "detected_at", "action"]:
        assert issue[field]
    assert not any(not i.get("issue_type") or not i.get("source_type") or i.get("source_id") is None for i in body["issues"])

    summary = client.get("/admin/accounting/reports/summary?date_from=2026-07-01&date_to=2026-07-31&as_of_date=2026-07-31", headers=headers)
    assert summary.status_code == 200
    assert summary.get_json()["incomplete_accounting_history"] is True
    assert any(w["code"] == "INCOMPLETE_ACCOUNTING_HISTORY" for w in summary.get_json()["warnings"])
