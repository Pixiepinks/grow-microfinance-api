# Grow Microfinance

This repository contains the **Flask REST API** for Grow Microfinance and a starter **Flutter mobile app** scaffold (in the `grow_microfinance_app/` folder).

- Backend repo name: **grow-microfinance-api**
- Mobile repo name: **grow-microfinance-app** (prepared here so you can copy to a new repo)

Both are kept simple and well-commented so you can push to GitHub, deploy the API to Railway + Postgres, and build the Flutter app for Android/iOS.

## 1) Backend (Flask + Postgres + Railway)

### Project structure
```
app/
  __init__.py        # Flask app factory
  extensions.py      # db, migrate, jwt
  models.py          # SQLAlchemy models
  routes/
    auth.py          # login + bootstrap admin
    admin.py         # admin endpoints
    staff.py         # staff endpoints
    customer.py      # customer endpoints
    utils.py         # role decorator
config.py            # Development/Production configs
wsgi.py              # Entrypoint
manage.py            # CLI for migrations/DB tasks
migrations/          # Alembic migration stubs (includes initial revision)
requirements.txt
scripts/seed_data.py # Optional demo data
```

### Setup (local)
1. Install Python 3.11
2. Create & activate a virtualenv
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```
3. Install dependencies
   ```bash
   pip install -r requirements.txt
   ```
4. Set environment variables (create a `.env` file or export in shell)
   ```bash
   export FLASK_ENV=development
   export DATABASE_URL=postgresql+psycopg2://USER:PASSWORD@HOST:PORT/DBNAME
   export JWT_SECRET_KEY=super-secret-key
   ```
5. Initialize the database (Postgres recommended; SQLite used if DATABASE_URL is not set)
   ```bash
   flask --app app:create_app db upgrade
   ```
6. (Optional) Seed demo data with an admin/staff/customer, a sample loan, and a payment
   ```bash
   python scripts/seed_data.py
   ```
7. Run locally
   ```bash
   flask --app wsgi run --port 5000
   ```
   Health check: `GET http://localhost:5000/health` → `{ "status": "ok" }`

### Deploying to Railway
1. Create a new Railway project and add a **PostgreSQL** addon.
2. Create a new Railway service from this repo.
3. Set environment variables in Railway → Variables:
   - `FLASK_ENV=production`
   - `DATABASE_URL` (from the Postgres addon)
   - `JWT_SECRET_KEY` (choose a strong value)
4. Set start command: `gunicorn wsgi:app --bind 0.0.0.0:$PORT`
5. Deploy. Use the public Railway URL as the API base for the Flutter app.

### API highlights
- JWT auth (`/auth/login`) returns `access_token`, `user_id`, `role`, `name`.
- Role-protected blueprints:
  - **Admin**: manage users/customers/loans + dashboard summary.
  - **Staff**: record payments, see today’s collections and arrears.
  - **Customer**: self-profile, loans, and payment history.
- Health check: `GET /health` → `{ "status": "ok" }`

### Initial migration example
`migrations/versions/0001_initial.py` shows the Alembic upgrade steps for `users`, `customers`, `loans`, and `payments` tables. You can regenerate migrations anytime with:
```bash
flask --app app:create_app db migrate -m "new changes"
flask --app app:create_app db upgrade
```

### Creating the first admin manually
You can either call the bootstrap endpoint or seed script:
- API: `POST /auth/register-admin` with `{ "email": "admin@example.com", "password": "StrongPass!", "name": "Admin" }`
- Script: `python scripts/seed_data.py` (creates admin/staff/customer + sample loan/payment)

## 2) Mobile app (Flutter)

The Flutter starter lives in `grow_microfinance_app/`. You can move it into its own repo named **grow-microfinance-app**.

### Structure (under `grow_microfinance_app/lib/`)
```
main.dart                   # App entry, role-based routing
core/api_client.dart        # HTTP client, set apiBaseUrl here
core/auth_storage.dart      # Save/load JWT + role in shared_preferences
features/auth/              # Login screen + provider
features/customer/          # Dashboard, loan details
features/staff/             # Staff dashboard + record payment
features/admin/             # Admin dashboard placeholder
widgets/summary_card.dart   # Simple UI widget
```

### Configure API URL
Edit `lib/core/api_client.dart` and set:
```dart
const String apiBaseUrl = "https://YOUR-RAILWAY-API-URL";
```

### Run the Flutter app (Android)
1. Install Flutter SDK and Android toolchain.
2. From `grow_microfinance_app/` run:
   ```bash
   flutter pub get
   flutter run -d <your_device_id>
   ```
3. Login with the admin/staff/customer accounts you created.

### Login flow
- Single login screen posts to `/auth/login`.
- Saves `access_token` + `role` locally, then routes to:
  - Admin dashboard (high-level metrics)
  - Staff dashboard (today’s collections + record payment form)
  - Customer dashboard (active loans, outstanding, arrears, payment history)
- Auto-redirect on app start if a token/role is already saved.

### Sample data for testing
After running `python scripts/seed_data.py`:
- Admin: `admin@grow.com` / `admin123`
- Staff: `staff@grow.com` / `staff123`
- Customer: `customer@grow.com` / `cust123`
- Loan: `LN001` with one payment (LKR 1,750)

## Notes
- Code is intentionally concise and commented to be approachable for non-programmers.
- You can extend models/routes as your needs grow; re-run migrations when fields change.
