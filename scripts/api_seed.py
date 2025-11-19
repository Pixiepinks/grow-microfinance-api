"""Seed demo data using the public API endpoints.

This script bootstraps an admin account, a staff user, a demo customer,
loan, and a payment using the same HTTP interface the mobile app would
use. It relies on Flask's built-in test client so no separate server
process is required.
"""

from datetime import date, timedelta
from typing import Optional

from app import create_app


ADMIN_EMAIL = "admin@grow.com"
ADMIN_PASSWORD = "admin123"
ADMIN_NAME = "Administrator"

STAFF_EMAIL = "staff@grow.com"
STAFF_PASSWORD = "staff123"
STAFF_NAME = "Field Staff"

CUSTOMER_EMAIL = "customer@grow.com"
CUSTOMER_PASSWORD = "cust123"

CUSTOMER_PROFILE = {
    "customer_code": "CUST001",
    "full_name": "Sunil Perera",
    "nic_number": "901234567V",
    "mobile": "0771234567",
    "address": "123 Market Street",
    "business_type": "Grocery",
    "status": "Active",
}

LOAN_DATA = {
    "loan_number": "LN001",
    "principal_amount": 50000,
    "interest_rate": 5,
    "total_days": 30,
}

PAYMENT_DATA = {
    "amount_collected": 1750,
    "payment_method": "Cash",
    "remarks": "On time",
}


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def login(client, email: str, password: str) -> Optional[str]:
    resp = client.post(
        "/auth/login",
        json={"email": email, "password": password},
    )
    if resp.status_code == 200:
        return resp.get_json().get("access_token")
    return None


def register_admin_if_needed(client) -> int:
    resp = client.post(
        "/auth/register-admin",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD, "name": ADMIN_NAME},
    )
    if resp.status_code in (200, 201):
        print("Admin created via /auth/register-admin")
        return resp.get_json().get("user_id")

    if resp.status_code == 400 and "exists" in resp.get_json().get("message", ""):
        print("Admin already exists; skipping creation")
        token = login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
        if token:
            users = client.get("/admin/users", headers=auth_headers(token)).get_json()
            for user in users:
                if user.get("email") == ADMIN_EMAIL:
                    return user["id"]
    raise RuntimeError(f"Admin bootstrap failed: {resp.status_code} {resp.get_data(as_text=True)}")


def ensure_staff_user(client, admin_token: str) -> int:
    resp = client.post(
        "/admin/users",
        headers=auth_headers(admin_token),
        json={
            "email": STAFF_EMAIL,
            "password": STAFF_PASSWORD,
            "name": STAFF_NAME,
            "role": "staff",
        },
    )
    if resp.status_code == 200:
        print("Staff user created")
        return resp.get_json().get("user_id")

    if resp.status_code == 400 and "exists" in resp.get_json().get("message", ""):
        print("Staff user already exists; skipping creation")
    else:
        raise RuntimeError(f"Staff creation failed: {resp.status_code} {resp.get_data(as_text=True)}")

    users = client.get("/admin/users", headers=auth_headers(admin_token)).get_json()
    for user in users:
        if user["email"] == STAFF_EMAIL:
            return user["id"]
    raise RuntimeError("Staff user not found after skipping creation")


def ensure_customer(client, admin_token: str) -> int:
    customers = client.get("/admin/customers", headers=auth_headers(admin_token)).get_json()
    for customer in customers:
        if customer.get("customer_code") == CUSTOMER_PROFILE["customer_code"]:
            print("Customer already exists; reusing existing record")
            return customer["id"]

    resp = client.post(
        "/admin/customers",
        headers=auth_headers(admin_token),
        json={
            "user": {
                "email": CUSTOMER_EMAIL,
                "password": CUSTOMER_PASSWORD,
                "name": CUSTOMER_PROFILE["full_name"],
            },
            "customer": CUSTOMER_PROFILE,
        },
    )
    if resp.status_code == 200:
        print("Customer profile created")
        return resp.get_json().get("customer_id")

    raise RuntimeError(f"Customer creation failed: {resp.status_code} {resp.get_data(as_text=True)}")


def ensure_loan(client, admin_token: str, customer_id: int) -> int:
    loans = client.get("/admin/loans", headers=auth_headers(admin_token)).get_json()
    for loan in loans:
        if loan.get("loan_number") == LOAN_DATA["loan_number"]:
            print("Loan already exists; reusing existing loan")
            return loan["id"]

    start_date = date.today() - timedelta(days=5)
    end_date = date.today() + timedelta(days=25)
    payload = {
        **LOAN_DATA,
        "customer_id": customer_id,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
    }
    resp = client.post(
        "/admin/loans",
        headers=auth_headers(admin_token),
        json=payload,
    )
    if resp.status_code == 200:
        print("Loan created")
        return resp.get_json().get("loan_id")

    raise RuntimeError(f"Loan creation failed: {resp.status_code} {resp.get_data(as_text=True)}")


def record_payment_if_needed(client, staff_token: str, loan_id: int) -> int:
    todays = client.get("/staff/today-collections", headers=auth_headers(staff_token)).get_json()
    for payment in todays:
        if payment.get("loan_id") == loan_id and payment.get("amount_collected") == PAYMENT_DATA["amount_collected"]:
            print("Payment for today already exists; skipping new payment")
            return payment.get("loan_id")

    payload = {
        **PAYMENT_DATA,
        "loan_id": loan_id,
        "collection_date": date.today().isoformat(),
    }
    resp = client.post(
        "/staff/payments",
        headers=auth_headers(staff_token),
        json=payload,
    )
    if resp.status_code == 200:
        print("Payment recorded")
        return resp.get_json().get("payment_id")

    raise RuntimeError(f"Payment creation failed: {resp.status_code} {resp.get_data(as_text=True)}")


def main():
    app = create_app()
    with app.app_context():
        client = app.test_client()

        admin_id = register_admin_if_needed(client)
        admin_token = login(client, ADMIN_EMAIL, ADMIN_PASSWORD)
        if not admin_token:
            raise RuntimeError("Failed to log in as admin")

        staff_id = ensure_staff_user(client, admin_token)
        staff_token = login(client, STAFF_EMAIL, STAFF_PASSWORD)
        if not staff_token:
            raise RuntimeError("Failed to log in as staff")

        customer_id = ensure_customer(client, admin_token)
        loan_id = ensure_loan(client, admin_token, customer_id)
        payment_id = record_payment_if_needed(client, staff_token, loan_id)

        print()
        print("Seed data ready:")
        print(f"  Admin: {ADMIN_EMAIL} / {ADMIN_PASSWORD} (id={admin_id})")
        print(f"  Staff: {STAFF_EMAIL} / {STAFF_PASSWORD} (id={staff_id})")
        print(f"  Customer: {CUSTOMER_EMAIL} / {CUSTOMER_PASSWORD} (id={customer_id})")
        print(f"  Loan number: {LOAN_DATA['loan_number']} (id={loan_id})")
        print(f"  Payment id: {payment_id}")


if __name__ == "__main__":
    main()
