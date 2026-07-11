#!/bin/sh

set -e

echo "Applying database migrations..."
export SKIP_AUTO_MIGRATIONS=1
flask --app app:create_app db upgrade

echo "Validating database schema..."
python scripts/validate_schema.py

echo "Seeding essential data..."
python scripts/seed_data.py

echo "Starting API..."
exec gunicorn wsgi:app --bind 0.0.0.0:${PORT:-8000}
