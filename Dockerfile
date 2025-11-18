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

# Ensure the entrypoint script is executable (handles migrations + seeding)
RUN chmod +x entrypoint.sh

# Start via entrypoint so migrations/seeds run before gunicorn
ENTRYPOINT ["./entrypoint.sh"]
