import os
from flask import Flask, jsonify
from flask_cors import CORS

from flask_migrate import stamp, upgrade
from sqlalchemy import inspect

from .extensions import db, migrate, jwt, init_jwt_handlers
from .routes.auth import auth_bp
from .routes.admin import admin_bp
from .routes.staff import staff_bp
from .routes.customer import customer_bp
from .routes.customers import customers_bp, public_bp
from .routes.loan_applications import admin_api_bp, loan_app_bp
from .routes.leads import leads_bp
from .schema_fix import ensure_customers_lead_status_column

def create_app():
    app = Flask(__name__)

    config_name = os.getenv("FLASK_ENV", "development").capitalize()
    config_module = f"config.{config_name}Config"
    app.config.from_object(config_module)

    app.config.setdefault("JWT_TOKEN_LOCATION", ["headers", "cookies", "query_string"])
    app.config.setdefault("JWT_QUERY_STRING_NAME", "access_token")

    db.init_app(app)
    migrate.init_app(app, db)
    jwt.init_app(app)
    init_jwt_handlers(app)

    CORS(
        app,
        resources={r"/*": {"origins": app.config.get("CORS_ORIGINS", "*")}},
    )

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(admin_api_bp)
    app.register_blueprint(staff_bp)
    app.register_blueprint(customer_bp)
    app.register_blueprint(customers_bp)
    app.register_blueprint(public_bp)
    app.register_blueprint(loan_app_bp)
    app.register_blueprint(leads_bp)

    with app.app_context():
        ensure_customers_lead_status_column()

    @app.route("/health", methods=["GET"])
    def health_check():
        return jsonify({"status": "ok"})

    # Ensure database schema is present even when the deployment start command
    # skips the entrypoint migration step (e.g., running `gunicorn wsgi:app`).
    # If migrations have already run, `upgrade()` is a no-op. Allow skipping
    # this auto-run via environment variable so CLI migration commands do not
    # execute twice.
    if os.getenv("SKIP_AUTO_MIGRATIONS") != "1":
        with app.app_context():
            try:
                upgrade()
            except Exception as exc:  # pragma: no cover - defensive logging
                app.logger.warning("Skipping automatic migrations: %s", exc)
                try:
                    inspector = inspect(db.engine)
                    if not inspector.has_table("alembic_version"):
                        stamp()
                        app.logger.info(
                            "Database stamped to current migration head after failed upgrade"
                        )
                except Exception as stamp_exc:  # pragma: no cover - defensive logging
                    app.logger.error("Failed to stamp database after migration error: %s", stamp_exc)

    return app
