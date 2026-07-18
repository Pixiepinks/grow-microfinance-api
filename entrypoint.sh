#!/bin/sh

set -eux

export SKIP_AUTO_MIGRATIONS=1

echo "Current database migration revision:"
flask --app app:create_app db current 2>&1

echo "Target database migration head:"
flask --app app:create_app db heads 2>&1

echo "Applying database migrations..."
flask --app app:create_app db upgrade 2>&1
echo "Database migrations completed."

echo "Validating database schema..."
python -m scripts.validate_schema 2>&1

echo "Seeding essential data..."
python -m scripts.seed_data 2>&1

echo "Starting API..."
exec gunicorn \
  --bind "0.0.0.0:${PORT:-8000}" \
  --workers 2 \
  --timeout 120 \
  "app:create_app()"
