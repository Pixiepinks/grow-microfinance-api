from datetime import datetime, timedelta
import re

from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import (
    create_access_token,
    create_refresh_token,
    get_jwt,
    get_jwt_identity,
    jwt_required,
)

from ..accounting import log_audit
from ..extensions import db
from ..models import PasswordHistory, RevokedToken, User


auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

COMMON_PASSWORDS = {
    "password", "password123", "admin123", "qwerty123", "letmein", "welcome",
    "changeme", "123456789", "1234567890", "iloveyou", "adminadmin",
}
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


def _access_expires_seconds() -> int:
    value = current_app.config.get("JWT_ACCESS_TOKEN_EXPIRES", timedelta(hours=1))
    return int(value.total_seconds())


def _refresh_expires_seconds() -> int:
    value = current_app.config.get("JWT_REFRESH_TOKEN_EXPIRES", timedelta(days=7))
    return int(value.total_seconds())


def _claims_for(user: User) -> dict:
    return {"role": user.role, "token_version": user.token_version or 0, "must_change_password": bool(user.must_change_password)}


def _user_payload(user: User) -> dict:
    return {"id": user.id, "name": user.name, "role": user.role.upper(), "must_change_password": bool(user.must_change_password)}


def _is_locked(user: User) -> bool:
    return bool(user.locked_until and user.locked_until > datetime.utcnow())


def _record_failed_login(user: User | None, email: str | None) -> None:
    if user:
        user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
        if user.failed_login_attempts >= MAX_FAILED_ATTEMPTS:
            user.locked_until = datetime.utcnow() + timedelta(minutes=LOCKOUT_MINUTES)
            log_audit("ACCOUNT_LOCKED", "User", user.id, user.id, {"reason": "failed_login_attempts"})
        log_audit("FAILED_LOGIN", "User", user.id, user.id, {"email": email})
    else:
        log_audit("FAILED_LOGIN", "User", None, None, {"email": email})
    db.session.commit()


def validate_password_policy(password: str, user: User) -> list[str]:
    errors = []
    if len(password or "") < 12:
        errors.append("Password must be at least 12 characters long.")
    if not re.search(r"[A-Z]", password or ""):
        errors.append("Password must include at least one uppercase letter.")
    if not re.search(r"[a-z]", password or ""):
        errors.append("Password must include at least one lowercase letter.")
    if not re.search(r"\d", password or ""):
        errors.append("Password must include at least one number.")
    if not re.search(r"[^A-Za-z0-9]", password or ""):
        errors.append("Password must include at least one special character.")

    lowered = (password or "").lower()
    if user.email and user.email.lower() in lowered:
        errors.append("Password must not contain your email address.")
    username = (user.email or "").split("@")[0].lower()
    if username and username in lowered:
        errors.append("Password must not contain your username.")
    if (user.name or "").lower() and (user.name or "").lower() in lowered:
        errors.append("Password must not contain your name.")
    if lowered in COMMON_PASSWORDS:
        errors.append("Password is too common; choose a unique password.")
    return errors


def _revoke_current_token(user_id: int | None = None) -> None:
    claims = get_jwt()
    jti = claims.get("jti")
    if jti and not RevokedToken.query.filter_by(jti=jti).first():
        db.session.add(RevokedToken(jti=jti, user_id=user_id, token_type=claims.get("type")))


@auth_bp.route("/login", methods=["POST"])
def login():
    data = request.get_json() or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password")

    if not email or not password:
        return jsonify({"message": "Email and password are required"}), 400

    user = User.query.filter_by(email=email).first()
    if not user or not user.check_password(password):
        _record_failed_login(user, email)
        return jsonify({"message": "Invalid username or password."}), 401

    if not user.is_active or _is_locked(user):
        log_audit("FAILED_LOGIN", "User", user.id, user.id, {"reason": "inactive_or_locked"})
        db.session.commit()
        return jsonify({"message": "Invalid username or password."}), 401

    user.failed_login_attempts = 0
    user.locked_until = None
    if user.must_change_password:
        temporary_token = create_access_token(identity=str(user.id), additional_claims={**_claims_for(user), "password_change_only": True})
        log_audit("SUCCESSFUL_LOGIN", "User", user.id, user.id, {"password_change_required": True})
        db.session.commit()
        return jsonify({"password_change_required": True, "temporary_token": temporary_token})

    claims = _claims_for(user)
    access_token = create_access_token(identity=str(user.id), additional_claims=claims)
    refresh_token = create_refresh_token(identity=str(user.id), additional_claims=claims)
    log_audit("SUCCESSFUL_LOGIN", "User", user.id, user.id)
    db.session.commit()

    return jsonify({
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "Bearer",
        "access_expires_in": _access_expires_seconds(),
        "refresh_expires_in": _refresh_expires_seconds(),
        "user": _user_payload(user),
        "user_id": user.id,
        "role": user.role,
        "name": user.name,
    })


@auth_bp.route("/refresh", methods=["POST"])
@jwt_required(refresh=True)
def refresh():
    user = db.session.get(User, int(get_jwt_identity()))
    claims = get_jwt()
    if not user or not user.is_active or _is_locked(user) or user.must_change_password or claims.get("token_version") != (user.token_version or 0):
        return jsonify({"message": "Invalid token"}), 401
    return jsonify({"access_token": create_access_token(identity=str(user.id), additional_claims=_claims_for(user)), "access_expires_in": _access_expires_seconds()})


@auth_bp.route("/change-password", methods=["POST"])
@jwt_required()
def change_password():
    user = db.session.get(User, int(get_jwt_identity()))
    if not user or not user.is_active:
        return jsonify({"message": "Authentication required"}), 401

    data = request.get_json() or {}
    current_password = data.get("current_password") or ""
    new_password = data.get("new_password") or ""
    confirm_password = data.get("confirm_password") or ""

    if not user.check_password(current_password):
        return jsonify({"message": "Current password is incorrect"}), 401
    if new_password != confirm_password:
        return jsonify({"message": "New password and confirmation do not match"}), 422
    if user.check_password(new_password):
        return jsonify({"message": "New password must differ from current password"}), 422

    errors = validate_password_policy(new_password, user)
    for history in PasswordHistory.query.filter_by(user_id=user.id).order_by(PasswordHistory.created_at.desc()).limit(5):
        if history.check_password(new_password):
            errors.append("New password must not match a recent password.")
            break
    if errors:
        return jsonify({"message": "Password does not meet policy", "errors": errors}), 422

    db.session.add(PasswordHistory(user_id=user.id, password_hash=user.password_hash))
    user.set_password(new_password)
    user.password_changed_at = datetime.utcnow()
    user.must_change_password = False
    user.token_version = (user.token_version or 0) + 1
    user.failed_login_attempts = 0
    user.locked_until = None
    _revoke_current_token(user.id)
    log_audit("PASSWORD_CHANGED", "User", user.id, user.id)
    log_audit("SESSION_REVOKED", "User", user.id, user.id, {"reason": "password_change"})
    db.session.commit()
    return jsonify({"message": "Password changed successfully. Please sign in again."})


@auth_bp.route("/logout", methods=["POST"])
@jwt_required(optional=True)
def logout():
    identity = get_jwt_identity()
    if identity:
        _revoke_current_token(int(identity))
        log_audit("SESSION_REVOKED", "User", identity, int(identity), {"reason": "logout"})
        db.session.commit()
    response = jsonify({"message": "Logged out"})
    response.delete_cookie("access_token_cookie")
    response.delete_cookie("refresh_token_cookie")
    return response


@auth_bp.route("/register-admin", methods=["POST"])
def register_admin():
    data = request.get_json() or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password")
    name = data.get("name")

    if not all([email, password, name]):
        return jsonify({"message": "Email, name, and password are required"}), 400
    if User.query.filter_by(email=email).first():
        return jsonify({"message": "User already exists"}), 400
    admin = User(email=email, name=name, role="admin")
    errors = validate_password_policy(password, admin)
    if errors:
        return jsonify({"message": "Password does not meet policy", "errors": errors}), 422
    admin.set_password(password)
    admin.password_changed_at = datetime.utcnow()
    db.session.add(admin)
    db.session.flush()
    log_audit("PASSWORD_RESET_COMPLETED", "User", admin.id, admin.id, {"source": "register-admin"})
    db.session.commit()
    return jsonify({"message": "Admin user created", "user_id": admin.id})
