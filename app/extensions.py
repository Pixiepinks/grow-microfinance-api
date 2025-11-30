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

    @jwt.needs_fresh_token_loader
    def handle_stale_token(jwt_header, jwt_payload):
        app.logger.info("Fresh token required for identity=%s", jwt_payload.get("sub"))
        return {"message": "Fresh token required"}, 401

    app.logger.info("JWT error handlers initialised")
