"""Validate production schema before starting the web server."""

from app import create_app
from app.extensions import db
from app.schema_validation import validate_required_schema

app = create_app()
with app.app_context():
    validate_required_schema(db.engine, app.logger)
