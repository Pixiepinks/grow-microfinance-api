from flask import Blueprint, request, jsonify
from flask_jwt_extended import create_access_token

from ..extensions import db
from ..models import User

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@auth_bp.route("/login", methods=["POST"])
def login():
    data = request.get_json() or {}
    email = data.get("email")
    password = data.get("password")

    if not email or not password:
        return jsonify({"message": "Email and password are required"}), 400

    user = User.query.filter_by(email=email).first()
    if not user or not user.check_password(password):
        return jsonify({"message": "Invalid credentials"}), 401

    additional_claims = {"role": user.role}
    access_token = create_access_token(identity=str(user.id), additional_claims=additional_claims)

    return jsonify(
        {
            "access_token": access_token,
            "user_id": user.id,
            "role": user.role,
            "name": user.name,
        }
    )


@auth_bp.route("/register-admin", methods=["POST"])
def register_admin():
    data = request.get_json() or {}
    email = data.get("email")
    password = data.get("password")
    name = data.get("name")

    if not all([email, password, name]):
        return jsonify({"message": "Email, name, and password are required"}), 400

    if User.query.filter_by(email=email).first():
        return jsonify({"message": "User already exists"}), 400

    admin = User(email=email, name=name, role="admin")
    admin.set_password(password)
    db.session.add(admin)
    db.session.commit()

    return jsonify({"message": "Admin user created", "user_id": admin.id})
