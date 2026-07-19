from datetime import date, datetime
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


def test_preview_preserves_negative_raw_balance_as_automatic_customer_credit(app, client):
    admin = User(email="overpaid-admin@example.com", name="Admin", role="admin")
    admin.set_password("password")
    db.session.add(admin)
    db.session.commit()
    loan = _legacy_loan(admin, amount_paid="19500.00", number="SETTLE-OVERPAID")

    response = client.post(
        f"/admin/loans/{loan.id}/settlement-reconciliation/preview",
        headers=_headers(app, admin), json={},
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["total_paid"] == 19500.0  # backward-compatible name
    assert body["total_cash_received"] == 19500.0
    assert body["total_applied_to_loan"] == 18900.0
    assert body["unapplied_excess"] == 600.0
    assert body["raw_balance"] == -600.0
    assert body["remaining_balance"] == 0.0
    assert body["proposed_customer_credit"] == 600.0


def test_preview_excludes_reversed_receipts_and_post_rejects_stale_manual_credit(app, client):
    admin = User(email="reversed-admin@example.com", name="Admin", role="admin")
    admin.set_password("password")
    db.session.add(admin)
    db.session.commit()
    loan = _legacy_loan(admin, amount_paid="18900.00", number="SETTLE-REVERSED")
    payment = loan.payments[0]
    payment.reversed_at = datetime.utcnow()
    db.session.commit()
    headers = _headers(app, admin)

    preview = client.post(f"/admin/loans/{loan.id}/settlement-reconciliation/preview", headers=headers, json={})
    assert preview.get_json()["total_cash_received"] == 0.0
    assert preview.get_json()["remaining_balance"] == 18900.0

    stale = client.post(f"/admin/loans/{loan.id}/settlement-reconciliation", headers=headers,
                        json={"confirm": True, "customer_credit": 600})
    assert stale.status_code == 409
    assert stale.get_json()["proposed_customer_credit"] == 0.0


def test_reconciliation_normalizes_zero_string_and_settles_overpayment_once(app, client):
    """A fully paid ACTIVE loan may be settled even when the preview serializes zero."""
    from app.settlement_reconciliation import _normalized_remaining_balance
    from app.models import CustomerCreditBalance

    assert _normalized_remaining_balance("0.00") == Decimal("0.00")
    admin = User(email="overpayment-post-admin@example.com", name="Admin", role="admin")
    admin.set_password("password")
    db.session.add(admin)
    db.session.commit()
    loan = _legacy_loan(admin, amount_paid="19500.00", number="SETTLE-POST-OVERPAYMENT")
    headers = _headers(app, admin)

    response = client.post(f"/admin/loans/{loan.id}/settlement-reconciliation", headers=headers, json={"confirm": True})
    assert response.status_code == 200
    body = response.get_json()
    assert body["success"] is True
    assert body["previous_status"] == "ACTIVE"
    assert body["new_status"] == "SETTLED"
    assert body["outstanding"] == 0.0
    assert body["overpayment"] == 600.0
    assert body["customer_credit"] == {"available_amount": 600.0, "status": "AVAILABLE"}
    assert body["message"] == "Loan settled and customer credit created successfully."
    assert CustomerCreditBalance.query.filter_by(loan_id=loan.id).count() == 1

    repeated = client.post(f"/admin/loans/{loan.id}/settlement-reconciliation", headers=headers, json={"confirm": True})
    assert repeated.status_code == 200
    assert repeated.get_json()["success"] is False
    assert CustomerCreditBalance.query.filter_by(loan_id=loan.id).count() == 1


def test_reconciliation_rejects_two_cent_remaining_balance(app, client):
    admin = User(email="two-cent-admin@example.com", name="Admin", role="admin")
    admin.set_password("password")
    db.session.add(admin)
    db.session.commit()
    loan = _legacy_loan(admin, amount_paid="18899.98", number="SETTLE-TWO-CENTS")

    response = client.post(f"/admin/loans/{loan.id}/settlement-reconciliation", headers=_headers(app, admin), json={"confirm": True})
    assert response.status_code == 200
    assert response.get_json()["success"] is False
    assert response.get_json()["remaining_balance"] == 0.02
