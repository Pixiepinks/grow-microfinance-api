#!/bin/sh

set -e

echo "Applying database migrations..."
flask --app app:create_app db upgrade || true

echo "Seeding initial users..."
python scripts/seed_data.py || true

echo "Starting API..."
exec gunicorn wsgi:app --bind 0.0.0.0:${PORT:-8000}
