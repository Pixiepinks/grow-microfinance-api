from datetime import date
from decimal import Decimal

from flask_jwt_extended import create_access_token

from app.extensions import db
from app.models import Customer, Loan, LoanLedger, Payment, User


def _headers(app, user):
    with app.app_context():
        token = create_access_token(identity=str(user.id), additional_claims={"role": "admin"})
    return {"Authorization": f"Bearer {token}"}


def _legacy_loan(admin, *, amount_paid, number):
    customer_user = User(email=f"{number}@example.com", name="Settlement Customer", role="customer")
    customer_user.set_password("password")
    db.session.add(customer_user)
    db.session.flush()
    customer = Customer(
        user_id=customer_user.id, customer_code=f"C-{number}", full_name="Settlement Customer",
        nic_number=f"NIC-{number}", mobile="0700000000", address="Address",
    )
    db.session.add(customer)
    db.session.flush()
    loan = Loan(
        loan_number=number, customer_id=customer.id, principal_amount=Decimal("18000.00"),
        interest_rate=Decimal("5.00"), total_days=30, payment_interval_days=30,
        daily_installment=Decimal("630.00"), total_payable=Decimal("18900.00"),
        start_date=date(2026, 6, 19), end_date=date(2026, 7, 19), status="ACTIVE",
        created_by_id=admin.id,
    )
    db.session.add(loan)
    db.session.flush()
    db.session.add(LoanLedger(
        loan_id=loan.id, installment_no=1, period_start_date=date(2026, 6, 19),
        due_date=date(2026, 7, 19), period_days=30, opening_balance=Decimal("18000.00"),
        principal_amount=Decimal("18000.00"), interest_amount=Decimal("900.00"),
        installment_amount=Decimal("18900.00"), closing_balance=Decimal("0.00"),
    ))
    db.session.add(Payment(
        loan_id=loan.id, amount_collected=Decimal(amount_paid), collection_date=date(2026, 7, 19),
        collected_by_id=admin.id, status="POSTED",
    ))
    db.session.commit()
    return loan


def test_reconciliation_alias_and_canonical_route_settle_proven_paid_loans(app, client):
    admin = User(email="settlement-admin@example.com", name="Admin", role="admin")
    admin.set_password("password")
    db.session.add(admin)
    db.session.commit()
    headers = _headers(app, admin)

    alias_loan = _legacy_loan(admin, amount_paid="18900.00", number="SETTLE-ALIAS")
    alias = client.post(f"/admin/loans/{alias_loan.id}/reconciliation", headers=headers, json={"confirm": True})
    assert alias.status_code == 200
    assert alias.get_json()["new_status"] == "SETTLED"
    assert alias.get_json()["warnings"] == ["Loan term metadata is missing."]

    canonical_loan = _legacy_loan(admin, amount_paid="18900.00", number="SETTLE-CANONICAL")
    canonical = client.post(f"/admin/loans/{canonical_loan.id}/settlement-reconciliation", headers=headers, json={"confirm": True})
    assert canonical.status_code == 200
    assert canonical.get_json()["new_status"] == "SETTLED"
    assert canonical.get_json()["total_paid"] == 18900.0


def test_reconciliation_rejects_missing_confirmation_and_returns_structured_not_found(app, client):
    admin = User(email="confirmation-admin@example.com", name="Admin", role="admin")
    admin.set_password("password")
    db.session.add(admin)
    db.session.commit()
    headers = _headers(app, admin)

    missing_confirmation = client.post("/admin/loans/999/reconciliation", headers=headers, json={})
    assert missing_confirmation.status_code == 422
    assert missing_confirmation.get_json() == {
        "error": "confirmation_required",
        "message": "Confirm the settlement reconciliation before posting.",
    }

    not_found = client.post("/admin/loans/999/reconciliation", headers=headers, json={"confirm": True})
    assert not_found.status_code == 404
    assert not_found.get_json() == {"error": "loan_not_found", "message": "The selected loan was not found."}


def test_reconciliation_does_not_settle_loan_with_remaining_balance(app, client):
    admin = User(email="remaining-admin@example.com", name="Admin", role="admin")
    admin.set_password("password")
    db.session.add(admin)
    db.session.commit()
    loan = _legacy_loan(admin, amount_paid="18000.00", number="SETTLE-REMAINING")

    response = client.post(f"/admin/loans/{loan.id}/reconciliation", headers=_headers(app, admin), json={"confirm": True})
    assert response.status_code == 200
    assert response.get_json()["success"] is False
    assert response.get_json()["new_status"] == "ACTIVE"
    assert response.get_json()["outstanding"] == 900.0


def test_reconciliation_options_and_preview_alias_are_available(app, client):
    assert client.options("/admin/loans/1/settlement-reconciliation").status_code == 204
    assert client.options("/admin/loans/1/reconciliation").status_code == 204
    assert client.options("/admin/loans/1/settlement-reconciliation/preview").status_code == 204
    assert client.options("/admin/loans/1/reconciliation/preview").status_code == 204
