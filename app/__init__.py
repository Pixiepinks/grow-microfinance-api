import os
from flask import Flask, jsonify

from flask_migrate import upgrade

from .extensions import db, migrate, jwt
from .routes.auth import auth_bp
from .routes.admin import admin_bp
from .routes.staff import staff_bp
from .routes.customer import customer_bp

def create_app():
    app = Flask(__name__)

    config_name = os.getenv("FLASK_ENV", "development").capitalize()
    config_module = f"config.{config_name}Config"
    app.config.from_object(config_module)

    db.init_app(app)
    migrate.init_app(app, db)
    jwt.init_app(app)

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(staff_bp)
    app.register_blueprint(customer_bp)

    @app.route("/health", methods=["GET"])
    def health_check():
        return jsonify({"status": "ok"})

    # Ensure database schema is present even when the deployment start command
    # skips the entrypoint migration step (e.g., running `gunicorn wsgi:app`).
    # If migrations have already run, `upgrade()` is a no-op.
    with app.app_context():
        try:
            upgrade()
        except Exception as exc:  # pragma: no cover - defensive logging
            app.logger.warning("Skipping automatic migrations: %s", exc)

    return app
