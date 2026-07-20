from datetime import date, datetime
from app.extensions import db
from app.models import User, Customer, CustomerKYCProfile, LoanApplication
from app.customer_master import build_customer_master_profile, apply_backfill

def make_customer():
    user = User(email="u@example.test", name="U", role="admin"); user.set_password("password")
    customer = Customer(user=user, customer_code="C001", full_name="Master", nic_number="NIC1", mobile="071", kyc_status="APPROVED")
    db.session.add(customer); db.session.commit(); return customer

def test_master_precedence_latest_kyc_and_conflict(app):
    with app.app_context():
        c=make_customer(); c.occupation=None
        db.session.add_all([CustomerKYCProfile(customer_id=c.id, occupation="old", review_status="APPROVED", reviewed_at=datetime(2024,1,1)), CustomerKYCProfile(customer_id=c.id, occupation="new", review_status="VERIFIED", reviewed_at=datetime(2025,1,1), date_of_birth=date(1990,1,1))]); db.session.commit()
        profile=build_customer_master_profile(c.id)
        assert profile['fields']['full_name']['value'] == 'Master'
        assert profile['fields']['occupation']['value'] == 'new'
        assert profile['fields']['occupation']['source'] == 'customer_kyc_profiles'

def test_legacy_address_backfill_is_safe_and_idempotent(app):
    with app.app_context():
        c=make_customer(); c.address="Unstructured address"; c.nic_number=None; db.session.commit()
        report=apply_backfill(c); db.session.commit()
        assert c.current_address_line1 == "Unstructured address" and c.current_city is None
        assert c.address_backfill_review_required and not apply_backfill(c)['changes']
        assert report['warnings']
