from datetime import datetime, date
from decimal import Decimal
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy.orm import relationship
from sqlalchemy import Numeric

from .extensions import db


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, index=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    customer_profile = relationship("Customer", back_populates="user", uselist=False)
    created_loans = relationship("Loan", back_populates="created_by", foreign_keys="Loan.created_by_id")
    collected_payments = relationship("Payment", back_populates="collected_by", foreign_keys="Payment.collected_by_id")

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Customer(db.Model):
    __tablename__ = "customers"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False)
    customer_code = db.Column(db.String(50), unique=True, index=True, nullable=False)
    full_name = db.Column(db.String(150), nullable=False)
    nic_number = db.Column(db.String(50))
    mobile = db.Column(db.String(20))
    address = db.Column(db.String(255))
    business_type = db.Column(db.String(120))
    status = db.Column(db.String(50), default="Active")

    user = relationship("User", back_populates="customer_profile")
    loans = relationship("Loan", back_populates="customer")


class Loan(db.Model):
    __tablename__ = "loans"

    id = db.Column(db.Integer, primary_key=True)
    loan_number = db.Column(db.String(50), unique=True, index=True, nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False)
    principal_amount = db.Column(Numeric(12, 2), nullable=False)
    interest_rate = db.Column(Numeric(5, 2), nullable=False)
    total_days = db.Column(db.Integer, nullable=False)
    daily_installment = db.Column(Numeric(12, 2), nullable=False)
    total_payable = db.Column(Numeric(12, 2), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(50), default="Active")
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    customer = relationship("Customer", back_populates="loans")
    created_by = relationship("User", back_populates="created_loans", foreign_keys=[created_by_id])
    payments = relationship("Payment", back_populates="loan")

    @property
    def total_paid(self) -> Decimal:
        return sum((payment.amount_collected for payment in self.payments), Decimal("0"))

    @property
    def outstanding(self) -> Decimal:
        return Decimal(self.total_payable) - self.total_paid

    def expected_to_date(self) -> Decimal:
        today = date.today()
        if today < self.start_date:
            return Decimal("0")
        elapsed_days = min((today - self.start_date).days + 1, self.total_days)
        return Decimal(self.daily_installment) * Decimal(elapsed_days)

    def arrears(self) -> Decimal:
        expected = self.expected_to_date()
        paid = self.total_paid
        return expected - paid if expected > paid else Decimal("0")


class Payment(db.Model):
    __tablename__ = "payments"

    id = db.Column(db.Integer, primary_key=True)
    loan_id = db.Column(db.Integer, db.ForeignKey("loans.id"), nullable=False)
    collection_date = db.Column(db.Date, default=date.today, nullable=False)
    amount_collected = db.Column(Numeric(12, 2), nullable=False)
    collected_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    payment_method = db.Column(db.String(50), default="Cash")
    remarks = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    loan = relationship("Loan", back_populates="payments")
    collected_by = relationship("User", back_populates="collected_payments", foreign_keys=[collected_by_id])
