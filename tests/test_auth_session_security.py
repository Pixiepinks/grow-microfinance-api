from datetime import datetime

from flask_jwt_extended import create_access_token

from app.extensions import db
from app.models import AccountingAuditLog, User


def _user(email="admin@example.com", password="OldStrong!12345", must_change=False):
    user = User(email=email, name="Administrator", role="admin", must_change_password=must_change)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return user


def test_login_returns_one_hour_access_and_seven_day_refresh(client):
    _user()
    response = client.post("/auth/login", json={"email": "admin@example.com", "password": "OldStrong!12345"})
    data = response.get_json()

    assert response.status_code == 200
    assert data["access_token"]
    assert data["refresh_token"]
    assert data["token_type"] == "Bearer"
    assert data["access_expires_in"] == 3600
    assert data["refresh_expires_in"] == 604800
    assert data["user"]["must_change_password"] is False


def test_refresh_token_obtains_new_access_token(client):
    _user()
    login = client.post("/auth/login", json={"email": "admin@example.com", "password": "OldStrong!12345"}).get_json()

    response = client.post("/auth/refresh", headers={"Authorization": f"Bearer {login['refresh_token']}"})
    data = response.get_json()

    assert response.status_code == 200
    assert data["access_token"]
    assert data["access_expires_in"] == 3600


def test_change_password_rejects_incorrect_current_password(client):
    _user()
    token = client.post("/auth/login", json={"email": "admin@example.com", "password": "OldStrong!12345"}).get_json()["access_token"]

    response = client.post("/auth/change-password", headers={"Authorization": f"Bearer {token}"}, json={"current_password": "wrong", "new_password": "NewStrong!12345", "confirm_password": "NewStrong!12345"})

    assert response.status_code == 401


def test_change_password_rejects_weak_password(client):
    _user()
    token = client.post("/auth/login", json={"email": "admin@example.com", "password": "OldStrong!12345"}).get_json()["access_token"]

    response = client.post("/auth/change-password", headers={"Authorization": f"Bearer {token}"}, json={"current_password": "OldStrong!12345", "new_password": "weak", "confirm_password": "weak"})

    assert response.status_code == 422
    assert response.get_json()["errors"]


def test_change_password_rejects_current_password_reuse(client):
    _user()
    token = client.post("/auth/login", json={"email": "admin@example.com", "password": "OldStrong!12345"}).get_json()["access_token"]

    response = client.post("/auth/change-password", headers={"Authorization": f"Bearer {token}"}, json={"current_password": "OldStrong!12345", "new_password": "OldStrong!12345", "confirm_password": "OldStrong!12345"})

    assert response.status_code == 422


def test_successful_password_change_revokes_old_tokens_and_audits(client):
    user = _user()
    login = client.post("/auth/login", json={"email": "admin@example.com", "password": "OldStrong!12345"}).get_json()

    response = client.post("/auth/change-password", headers={"Authorization": f"Bearer {login['access_token']}"}, json={"current_password": "OldStrong!12345", "new_password": "NewStrong!12345", "confirm_password": "NewStrong!12345"})

    assert response.status_code == 200
    db.session.refresh(user)
    assert user.check_password("NewStrong!12345")
    assert user.password_changed_at is not None
    assert user.token_version == 1
    assert AccountingAuditLog.query.filter_by(action="PASSWORD_CHANGED", entity_id=str(user.id)).first()

    old_refresh = client.post("/auth/refresh", headers={"Authorization": f"Bearer {login['refresh_token']}"})
    assert old_refresh.status_code == 401


def test_forced_password_change_blocks_application_access(client):
    user = _user(must_change=True)
    login = client.post("/auth/login", json={"email": "admin@example.com", "password": "OldStrong!12345"})
    data = login.get_json()

    assert login.status_code == 200
    assert data["password_change_required"] is True
    blocked = client.get("/admin/users", headers={"Authorization": f"Bearer {data['temporary_token']}"})
    assert blocked.status_code == 403

    changed = client.post("/auth/change-password", headers={"Authorization": f"Bearer {data['temporary_token']}"}, json={"current_password": "OldStrong!12345", "new_password": "NewStrong!12345", "confirm_password": "NewStrong!12345"})
    assert changed.status_code == 200
    db.session.refresh(user)
    assert user.must_change_password is False
