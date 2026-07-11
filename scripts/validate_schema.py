"""Validate production schema before starting the web server."""

from app import create_app
from app.extensions import db
from app.schema_validation import validate_required_schema


def main():
    """Validate required database columns inside the Flask app context."""
    app = create_app()

    with app.app_context():
        validate_required_schema(db.engine, app.logger)

    print("Database schema validation passed.")


if __name__ == "__main__":
    main()
