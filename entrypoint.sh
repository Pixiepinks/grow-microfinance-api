#!/bin/sh

set -e

echo "Running database migrations..."
flask --app app:create_app db upgrade || true

echo "Seeding initial data (admin/staff/customer)..."
python scripts/seed_data.py || true

echo "Starting Grow Microfinance API with gunicorn..."
exec gunicorn wsgi:app --bind 0.0.0.0:${PORT:-8000}
