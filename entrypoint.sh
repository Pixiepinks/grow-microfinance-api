#!/bin/sh

set -eu

echo "Applying database migrations..."
export SKIP_AUTO_MIGRATIONS=1
flask --app app:create_app db upgrade

echo "Validating database schema..."
python -m scripts.validate_schema

echo "Seeding essential data..."
python -m scripts.seed_data

echo "Starting API..."
exec gunicorn \
  --bind "0.0.0.0:${PORT:-8000}" \
  --workers 2 \
  --timeout 120 \
  "app:create_app()"
