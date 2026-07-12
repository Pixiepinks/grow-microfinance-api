import ast
import os
import stat
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from app.extensions import db
from app.models import Loan

ROOT = Path(__file__).resolve().parents[1]


def _script_directory():
    return ScriptDirectory.from_config(Config(str(ROOT / "migrations" / "alembic.ini")))


def test_alembic_revision_ids_fit_production_version_column():
    max_revision_length = 32

    for path in (ROOT / "migrations" / "versions").glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in tree.body:
            if (
                isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == "revision"
                    for target in node.targets
                )
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                assert len(node.value.value) <= max_revision_length, (
                    f"{path}: revision {node.value.value!r} "
                    f"exceeds {max_revision_length} characters"
                )


def test_alembic_chain_has_one_head_and_valid_down_revisions():
    script = _script_directory()
    revisions = {rev.revision for rev in script.walk_revisions()}
    assert len(script.get_heads()) == 1
    for rev in script.walk_revisions():
        downs = rev._normalized_down_revisions
        for down_revision in downs:
            assert down_revision in revisions


def test_migration_from_0021_to_head_succeeds(tmp_path):
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        import pytest
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL-compatible migration upgrade test")
    env = os.environ.copy()
    env.update(
        DATABASE_URL=database_url,
        FLASK_ENV="production",
        SKIP_AUTO_MIGRATIONS="1",
        JWT_SECRET_KEY="x" * 32,
    )
    subprocess.run(
        [sys.executable, "-m", "flask", "--app", "app:create_app", "db", "upgrade", "0021"],
        cwd=ROOT,
        env=env,
        check=True,
    )
    subprocess.run(
        [sys.executable, "-m", "flask", "--app", "app:create_app", "db", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        check=True,
    )
    current = subprocess.run(
        [sys.executable, "-m", "flask", "--app", "app:create_app", "db", "current"],
        cwd=ROOT,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )
    assert "0025_ledger_start_nullable" in current.stdout


def test_loan_model_columns_exist_after_schema_creation(app):
    with app.app_context():
        db.drop_all()
        db.create_all()
        expected = set(Loan.__table__.columns.keys())
        actual = {column["name"] for column in inspect(db.engine).get_columns("loans")}
        assert expected <= actual


def test_legacy_loans_with_nullable_flexible_terms_still_load(app):
    from app.models import Customer, User

    with app.app_context():
        user = User(email="legacy@example.com", name="Legacy", role="customer")
        user.set_password("password")
        db.session.add(user)
        db.session.flush()
        customer = Customer(user_id=user.id, customer_code="LEGACY", full_name="Legacy")
        db.session.add(customer)
        db.session.flush()
        loan = Loan(
            loan_number="LEGACY-NULL-TERMS",
            customer_id=customer.id,
            principal_amount=1000,
            interest_rate=5,
            total_days=30,
            daily_installment=35,
            total_payable=1050,
            start_date=date.today(),
            end_date=date.today() + timedelta(days=29),
            status="Active",
            created_by_id=user.id,
        )
        db.session.add(loan)
        db.session.commit()
        loaded = Loan.query.filter_by(loan_number="LEGACY-NULL-TERMS").one()
        assert loaded.loan_days is None
        assert loaded.repayment_frequency is None


def test_entrypoint_exits_before_seed_when_migration_fails(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.log"
    flask = bin_dir / "flask"
    flask.write_text(f"#!/bin/sh\necho flask >> {calls}\nexit 42\n")
    python = bin_dir / "python"
    python.write_text(f"#!/bin/sh\necho seed >> {calls}\nexit 0\n")
    gunicorn = bin_dir / "gunicorn"
    gunicorn.write_text(f"#!/bin/sh\necho gunicorn >> {calls}\nexit 0\n")
    for executable in (flask, python, gunicorn):
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    result = subprocess.run(["/bin/sh", "entrypoint.sh"], cwd=ROOT, env=env)

    assert result.returncode == 42
    assert calls.read_text() == "flask\n"


def test_entrypoint_uses_module_schema_validation_before_gunicorn():
    source = (ROOT / "entrypoint.sh").read_text()
    assert "flask --app app:create_app db upgrade" in source
    assert "python -m scripts.validate_schema" in source
    assert "python scripts/validate_schema.py" not in source
    assert '"app:create_app()"' in source
    assert source.index("python -m scripts.validate_schema") < source.index("exec gunicorn")


def test_validate_schema_module_importable():
    result = subprocess.run(
        [sys.executable, "-c", "from app import create_app; from scripts.validate_schema import main; print(create_app); print(main)"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert "create_app" in result.stdout
    assert "main" in result.stdout


def test_seed_script_demo_data_disabled_does_not_query_loans(monkeypatch):
    source = (ROOT / "scripts" / "seed_data.py").read_text()
    disabled_branch = source.split("if not demo_data_enabled():", 1)[1].split("customer = Customer.query", 1)[0]
    assert "Loan.query" not in disabled_branch
    assert 'SEED_DEMO_DATA", "false"' in source


def test_loan_application_post_unexpected_error_returns_json(app, client, monkeypatch):
    from flask_jwt_extended import create_access_token
    from app.models import Customer, User
    from app.routes import loan_applications as routes

    with app.app_context():
        user = User(email="json-error@example.com", name="Json Error", role="customer")
        user.set_password("password")
        db.session.add(user)
        db.session.commit()
        customer = Customer(
            user_id=user.id,
            customer_code="JSON-ERR",
            full_name="Json Error",
            nic_number="123456789V",
            mobile="0700000000",
            kyc_status="APPROVED",
            eligibility_status="ELIGIBLE",
        )
        db.session.add(customer)
        db.session.commit()
        token = create_access_token(identity=str(user.id), additional_claims={"role": "customer"})

    def explode():
        raise RuntimeError("database internals should not leak")

    monkeypatch.setattr(routes, "generate_application_number", explode)
    response = client.post(
        "/loan-applications",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "loan_type": "GROW_BUSINESS",
            "full_name": "Json Error",
            "nic_number": "123456789V",
            "mobile_number": "0700000000",
            "applied_amount": "10000",
            "tenure_months": 6,
            "monthly_income": "50000",
            "monthly_expenses": "10000",
            "business_name": "Shop",
            "business_address": "123 Street",
            "monthly_sales": "60000",
            "business_type": "Retail",
        },
    )
    assert response.status_code == 500
    assert response.is_json
    assert response.get_json() == {
        "error": "internal_server_error",
        "message": "Unable to submit loan application.",
    }


def test_loan_application_post_validation_failure_returns_json(app, client):
    from flask_jwt_extended import create_access_token
    from app.models import Customer, User

    with app.app_context():
        user = User(email="json-validation@example.com", name="Json Validation", role="customer")
        user.set_password("password")
        db.session.add(user)
        db.session.commit()
        customer = Customer(
            user_id=user.id,
            customer_code="JSON-VALIDATION",
            full_name="Json Validation",
            nic_number="123456789V",
            mobile="0700000000",
            kyc_status="APPROVED",
            eligibility_status="ELIGIBLE",
        )
        db.session.add(customer)
        db.session.commit()
        token = create_access_token(identity=str(user.id), additional_claims={"role": "customer"})

    response = client.post(
        "/loan-applications",
        headers={"Authorization": f"Bearer {token}"},
        json={"loan_type": "GROW_BUSINESS"},
    )
    assert response.status_code == 400
    assert response.is_json
    assert "errors" in response.get_json()


def test_collector_migration_is_fail_fast_and_diagnostic():
    source = (ROOT / "migrations" / "versions" / "0027_collector_collections.py").read_text()
    tree = ast.parse(source)

    assert not any(isinstance(node, ast.Try) for node in ast.walk(tree))
    assert "except Exception" not in source
    assert "except ProgrammingError" not in source
    assert "InFailedSqlTransaction" not in source

    for message in [
        "0027: adding accounting_accounts.collector_id",
        "0027: adding accounting_accounts.is_collection_account",
        "0027: creating collection_deposit_batches",
        "0027: creating collection_deposit_allocations",
        "0027: migration complete",
    ]:
        assert message in source


def test_collector_migration_uses_verified_schema_helpers():
    source = (ROOT / "migrations" / "versions" / "0027_collector_collections.py").read_text()

    assert 'revision = "0027_collector_collect"' in source
    assert 'down_revision = "0026_loan_accrual"' in source
    assert "def _table_names(bind):" in source
    assert "def _column_names(bind, table):" in source
    assert "def _fk_names(bind, table):" in source
    assert "def _index_names(bind, table):" in source
    assert "def _create_fk_if_possible(" in source
    assert "sa.Enum" not in source
    assert "op.create_table(\n            \"collection_deposit_batches\"" in source
    assert "op.create_table(\n            \"collection_deposit_allocations\"" in source
