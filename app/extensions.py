import logging

from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_jwt_extended import JWTManager


db = SQLAlchemy()
migrate = Migrate()
jwt = JWTManager()


def init_jwt_handlers(app):
    """Attach JWT error handlers with detailed logging."""

    @jwt.unauthorized_loader
    def handle_missing_token(err_msg):
        app.logger.warning("JWT missing or malformed: %s", err_msg)
        return {"message": "Authentication required", "detail": err_msg}, 401

    @jwt.invalid_token_loader
    def handle_invalid_token(err_msg):
        app.logger.warning("JWT invalid: %s", err_msg)
        return {"message": "Invalid token", "detail": err_msg}, 401

    @jwt.expired_token_loader
    def handle_expired_token(jwt_header, jwt_payload):
        app.logger.info("JWT expired for identity=%s", jwt_payload.get("sub"))
        return {"message": "Token has expired"}, 401

    @jwt.revoked_token_loader
    def handle_revoked_token(jwt_header, jwt_payload):
        app.logger.info("JWT revoked for identity=%s", jwt_payload.get("sub"))
        return {"message": "Token has been revoked"}, 401

    @jwt.token_in_blocklist_loader
    def is_token_revoked(jwt_header, jwt_payload):
        from .models import RevokedToken, User

        jti = jwt_payload.get("jti")
        if jti and RevokedToken.query.filter_by(jti=jti).first():
            return True
        identity = jwt_payload.get("sub")
        if not identity:
            return True
        user = User.query.get(int(identity))
        if not user or not user.is_active:
            return True
        token_version = jwt_payload.get("token_version", 0)
        if token_version != (user.token_version or 0):
            return True
        return False

    @jwt.needs_fresh_token_loader
    def handle_stale_token(jwt_header, jwt_payload):
        app.logger.info("Fresh token required for identity=%s", jwt_payload.get("sub"))
        return {"message": "Fresh token required"}, 401

    app.logger.info("JWT error handlers initialised")
