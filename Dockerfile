FROM python:3.11-slim

# Set workdir
WORKDIR /app

# Prevent Python from writing .pyc files and using buffered output
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Default environment
ENV FLASK_ENV=production

# Start the app with gunicorn, binding to the PORT env variable from Railway
CMD ["bash", "-c", "gunicorn wsgi:app --bind 0.0.0.0:${PORT:-8000}"]
