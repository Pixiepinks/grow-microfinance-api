from flask_jwt_extended import create_access_token

from app.extensions import db
from app.models import Customer, User


def _user(role="admin", email="admin-search@example.com"):
    user = User(email=email, name=f"{role} user", role=role)
    user.set_password("password")
    db.session.add(user)
    db.session.commit()
    return user


def _headers(app, user):
    with app.app_context():
        token = create_access_token(identity=str(user.id), additional_claims={"role": user.role})
    return {"Authorization": f"Bearer {token}"}


def _customer(code, name, nic, mobile, *, status="Active", email=None):
    user = _user("customer", email or f"{code.lower()}@example.com")
    customer = Customer(
        user_id=user.id,
        customer_code=code,
        full_name=name,
        nic_number=nic,
        mobile=mobile,
        status=status,
        permanent_address_line1="No:103/25, Green Terrace",
        permanent_city="Olbadduwa",
        permanent_district="Gampola",
    )
    db.session.add(customer)
    db.session.commit()
    return customer


def _seed_customers(count=12):
    first = _customer("GROW-CUS-000010", "T H Vindya Madushani Thennakoon", "912345678V", "0760677104")
    second = _customer("GROW-CUS-000011", "Alpha Borrower", "801111111V", "+94 76-123-4567")
    for idx in range(count - 2):
        number = idx + 100
        _customer(f"GROW-CUS-{number:06d}", f"Zero Customer {idx}", f"70{idx:07d}V", f"07000000{idx:02d}")
    return first, second


def test_customer_search_missing_q_returns_empty(app, client):
    admin = _user()
    _seed_customers()

    resp = client.get("/admin/customers/search", headers=_headers(app, admin))

    assert resp.status_code == 200
    assert resp.get_json() == {"items": [], "total": 0, "query": ""}


def test_customer_search_empty_q_returns_empty(app, client):
    admin = _user()
    _seed_customers()

    resp = client.get("/admin/customers/search?q=   ", headers=_headers(app, admin))

    assert resp.status_code == 200
    assert resp.get_json() == {"items": [], "total": 0, "query": ""}


def test_customer_search_one_digit_is_limited(app, client):
    admin = _user()
    _seed_customers(15)

    resp = client.get("/admin/customers/search?q=0", headers=_headers(app, admin))

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["query"] == "0"
    assert 0 < len(body["items"]) <= 10


def test_customer_search_mobile_normalizes_prefixes(app, client):
    admin = _user()
    _seed_customers()

    resp = client.get("/admin/customers/search?q=076", headers=_headers(app, admin))

    assert resp.status_code == 200
    mobiles = [item["mobile"] for item in resp.get_json()["items"]]
    assert "0760677104" in mobiles
    assert "+94 76-123-4567" in mobiles


def test_customer_search_name_prefix(app, client):
    admin = _user()
    _seed_customers()

    resp = client.get("/admin/customers/search?q=Vind", headers=_headers(app, admin))

    assert resp.status_code == 200
    items = resp.get_json()["items"]
    assert items[0]["full_name"] == "T H Vindya Madushani Thennakoon"
    assert set(items[0]) >= {"id", "customer_id", "customer_number", "label"}


def test_customer_search_exact_nic_first(app, client):
    admin = _user()
    first, _ = _seed_customers()
    _customer("GROW-CUS-999999", "Contains NIC", "X912345678V", "0770000000")

    resp = client.get("/admin/customers/search?q=912345678V", headers=_headers(app, admin))

    assert resp.status_code == 200
    assert resp.get_json()["items"][0]["id"] == first.id


def test_customer_search_no_matches_has_no_fallback(app, client):
    admin = _user()
    _seed_customers()

    resp = client.get("/admin/customers/search?q=not-a-real-customer", headers=_headers(app, admin))

    assert resp.status_code == 200
    assert resp.get_json() == {"items": [], "total": 0, "query": "not-a-real-customer"}


def test_customer_search_limit_is_capped(app, client):
    admin = _user()
    _seed_customers(25)

    resp = client.get("/admin/customers/search?q=0&limit=1000", headers=_headers(app, admin))

    assert resp.status_code == 200
    assert len(resp.get_json()["items"]) <= 20
