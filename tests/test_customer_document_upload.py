from io import BytesIO

from flask_jwt_extended import create_access_token

from app.extensions import db
from app.models import Customer, CustomerDocument, User


def _create_user(role: str, name: str, email: str) -> User:
    user = User(email=email, name=name, role=role)
    user.set_password("password")
    db.session.add(user)
    db.session.commit()
    return user


def _create_customer(user: User, code: str = "CUST-KYC-001") -> Customer:
    customer = Customer(
        user_id=user.id,
        customer_code=code,
        full_name=user.name,
        nic_number="123456789V",
        mobile="0700000000",
        address="123 Street",
        business_type="Retail",
    )
    db.session.add(customer)
    db.session.commit()
    return customer


def _auth_headers(app, user: User):
    with app.app_context():
        token = create_access_token(identity=str(user.id), additional_claims={"role": user.role})
    return {"Authorization": f"Bearer {token}"}


def test_upload_customer_document_returns_400_when_file_missing(app, client, monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role")
    monkeypatch.setenv("SUPABASE_BUCKET_KYC", "kyc-bucket")

    staff_user = _create_user("staff", "Staff One", "staff-upload-1@example.com")

    class _FakeCustomer:
        id = 999
        kyc_status = "PENDING"

    monkeypatch.setattr("app.routes.customers.Customer.query", type("Q", (), {"get": staticmethod(lambda _id: _FakeCustomer())})())

    response = client.post(
        "/customers/999/documents",
        data={"document_type": "NIC_FRONT"},
        headers=_auth_headers(app, staff_user),
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "file_missing"}


def test_upload_customer_document_happy_path(app, client, monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role")
    monkeypatch.setenv("SUPABASE_BUCKET_KYC", "kyc-bucket")

    staff_user = _create_user("staff", "Staff Two", "staff-upload-2@example.com")
    customer_user = _create_user("customer", "Customer Two", "customer-upload-2@example.com")
    customer = _create_customer(customer_user, code="CUST-KYC-002")

    def fake_save(customer_id, uploaded_file, document_type, file_bytes):
        assert customer_id == customer.id
        assert document_type == "NIC_FRONT"
        assert uploaded_file.filename == "nic-front.jpg"
        assert file_bytes == b"fake-image-data"
        return (
            f"kyc/{customer.id}/nic_front/20260101010101_nic-front.jpg",
            f"https://example.supabase.co/storage/v1/object/public/kyc-bucket/kyc/{customer.id}/nic_front/20260101010101_nic-front.jpg",
        )

    monkeypatch.setattr("app.routes.customers.save_customer_document_file", fake_save)

    response = client.post(
        f"/customers/{customer.id}/documents",
        data={
            "document_type": "NIC_FRONT",
            "file": (BytesIO(b"fake-image-data"), "nic-front.jpg"),
        },
        headers=_auth_headers(app, staff_user),
        content_type="multipart/form-data",
    )

    assert response.status_code == 201
    body = response.get_json()
    assert body["customer_id"] == customer.id
    assert body["document_type"] == "NIC_FRONT"
    assert body["url"].endswith(f"kyc/{customer.id}/nic_front/20260101010101_nic-front.jpg")
    assert body["uploaded_at"] is not None

    doc = CustomerDocument.query.filter_by(customer_id=customer.id, document_type="NIC_FRONT").first()
    assert doc is not None
    assert doc.file_path.startswith("kyc/")
