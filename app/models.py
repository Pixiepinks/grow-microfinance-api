from datetime import datetime, date
from decimal import Decimal
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy.orm import relationship
from sqlalchemy import Numeric, func

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
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    customer_profile = relationship("Customer", back_populates="user", uselist=False)
    created_loans = relationship(
        "Loan", back_populates="created_by", foreign_keys="Loan.created_by_id"
    )
    collected_payments = relationship(
        "Payment", back_populates="collected_by", foreign_keys="Payment.collected_by_id"
    )
    created_applications = relationship(
        "LoanApplication",
        back_populates="created_by",
        foreign_keys="LoanApplication.created_by_id",
    )
    assigned_applications = relationship(
        "LoanApplication",
        back_populates="assigned_officer",
        foreign_keys="LoanApplication.assigned_officer_id",
    )

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Customer(db.Model):
    __tablename__ = "customers"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False
    )
    customer_code = db.Column(db.String(50), unique=True, index=True, nullable=False)
    full_name = db.Column(db.String(150), nullable=False)
    nic_number = db.Column(db.String(50))
    mobile = db.Column(db.String(20))
    address = db.Column(db.String(255))
    business_type = db.Column(db.String(120))
    date_of_birth = db.Column(db.Date, nullable=True)
    civil_status = db.Column(db.String(20), nullable=True)
    permanent_address_line1 = db.Column(db.String(255), nullable=True)
    permanent_address_line2 = db.Column(db.String(255), nullable=True)
    permanent_city = db.Column(db.String(100), nullable=True)
    permanent_district = db.Column(db.String(100), nullable=True)
    permanent_province = db.Column(db.String(100), nullable=True)
    permanent_postal_code = db.Column(db.String(20), nullable=True)
    current_address_line1 = db.Column(db.String(255), nullable=True)
    current_address_line2 = db.Column(db.String(255), nullable=True)
    current_city = db.Column(db.String(100), nullable=True)
    current_district = db.Column(db.String(100), nullable=True)
    current_province = db.Column(db.String(100), nullable=True)
    current_postal_code = db.Column(db.String(20), nullable=True)
    current_address_since = db.Column(db.String(10), nullable=True)
    household_size = db.Column(db.Integer, nullable=True)
    dependents_count = db.Column(db.Integer, nullable=True)
    customer_type = db.Column(db.String(20), nullable=True)
    employer_name = db.Column(db.String(255), nullable=True)
    employer_address = db.Column(db.String(255), nullable=True)
    occupation = db.Column(db.String(100), nullable=True)
    monthly_income = db.Column(db.Numeric(12, 2), nullable=True)
    business_name = db.Column(db.String(255), nullable=True)
    business_address = db.Column(db.String(255), nullable=True)
    guarantor_name = db.Column(db.String(255), nullable=True)
    guarantor_relationship = db.Column(db.String(100), nullable=True)
    guarantor_mobile = db.Column(db.String(30), nullable=True)
    consent_data_processing = db.Column(db.Boolean, nullable=True, default=False)
    consent_credit_checks = db.Column(db.Boolean, nullable=True, default=False)
    status = db.Column(db.String(50), default="Active")
    lead_status = db.Column(db.String(32), nullable=False, default="NEW")
    kyc_status = db.Column(db.String(32), nullable=False, default="PENDING")
    eligibility_status = db.Column(db.String(32), nullable=False, default="UNKNOWN")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="customer_profile")
    loans = relationship("Loan", back_populates="customer")
    loan_applications = relationship("LoanApplication", back_populates="customer")
    documents = relationship(
        "CustomerDocument",
        back_populates="customer",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )
    kyc_profile = relationship(
        "CustomerKYCProfile",
        back_populates="customer",
        uselist=False,
        cascade="all, delete-orphan",
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "customer_code": self.customer_code,
            "full_name": self.full_name,
            "nic_number": self.nic_number,
            "mobile": self.mobile,
            "address": self.address,
            "business_type": self.business_type,
            "date_of_birth": (
                self.date_of_birth.isoformat() if self.date_of_birth else None
            ),
            "civil_status": self.civil_status,
            "permanent_address_line1": self.permanent_address_line1,
            "permanent_address_line2": self.permanent_address_line2,
            "permanent_city": self.permanent_city,
            "permanent_district": self.permanent_district,
            "permanent_province": self.permanent_province,
            "permanent_postal_code": self.permanent_postal_code,
            "current_address_line1": self.current_address_line1,
            "current_address_line2": self.current_address_line2,
            "current_city": self.current_city,
            "current_district": self.current_district,
            "current_province": self.current_province,
            "current_postal_code": self.current_postal_code,
            "current_address_since": self.current_address_since,
            "household_size": self.household_size,
            "dependents_count": self.dependents_count,
            "customer_type": self.customer_type,
            "employer_name": self.employer_name,
            "employer_address": self.employer_address,
            "occupation": self.occupation,
            "monthly_income": (
                float(self.monthly_income) if self.monthly_income is not None else None
            ),
            "business_name": self.business_name,
            "business_address": self.business_address,
            "guarantor_name": self.guarantor_name,
            "guarantor_relationship": self.guarantor_relationship,
            "guarantor_mobile": self.guarantor_mobile,
            "consent_data_processing": self.consent_data_processing,
            "consent_credit_checks": self.consent_credit_checks,
            "lead_status": self.lead_status,
            "kyc_status": self.kyc_status,
            "eligibility_status": self.eligibility_status,
        }


class CustomerDocument(db.Model):
    __tablename__ = "customer_documents"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False)
    document_type = db.Column(db.String(64), nullable=False)
    file_path = db.Column(db.String(512), nullable=False)
    uploaded_at = db.Column(db.DateTime, server_default=func.now(), nullable=False)

    customer = relationship("Customer", back_populates="documents")


class CustomerKYCProfile(db.Model):
    __tablename__ = "customer_kyc_profiles"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(
        db.Integer, db.ForeignKey("customers.id"), unique=True, nullable=False
    )
    date_of_birth = db.Column(db.Date, nullable=True)
    civil_status = db.Column(db.String(50), nullable=True)
    permanent_address_line1 = db.Column(db.String(255), nullable=True)
    permanent_address_line2 = db.Column(db.String(255), nullable=True)
    permanent_city = db.Column(db.String(100), nullable=True)
    permanent_district = db.Column(db.String(100), nullable=True)
    permanent_province = db.Column(db.String(100), nullable=True)
    permanent_postal_code = db.Column(db.String(20), nullable=True)
    current_address_line1 = db.Column(db.String(255), nullable=True)
    current_address_line2 = db.Column(db.String(255), nullable=True)
    current_city = db.Column(db.String(100), nullable=True)
    current_district = db.Column(db.String(100), nullable=True)
    current_province = db.Column(db.String(100), nullable=True)
    current_postal_code = db.Column(db.String(20), nullable=True)
    current_address_since = db.Column(db.String(10), nullable=True)
    household_size = db.Column(db.Integer, nullable=True)
    dependents_count = db.Column(db.Integer, nullable=True)
    customer_type = db.Column(db.String(50), nullable=True)
    employer_name = db.Column(db.String(255), nullable=True)
    employer_address = db.Column(db.String(255), nullable=True)
    occupation = db.Column(db.String(100), nullable=True)
    monthly_income = db.Column(db.Numeric(12, 2), nullable=True)
    business_name = db.Column(db.String(255), nullable=True)
    business_address = db.Column(db.String(255), nullable=True)
    guarantor_name = db.Column(db.String(255), nullable=True)
    guarantor_relationship = db.Column(db.String(100), nullable=True)
    guarantor_mobile = db.Column(db.String(30), nullable=True)
    consent_data_processing = db.Column(db.Boolean, nullable=True)
    consent_credit_checks = db.Column(db.Boolean, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    customer = relationship("Customer", back_populates="kyc_profile")


class Lead(db.Model):
    __tablename__ = "leads"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255))
    mobile = db.Column(db.String(32), nullable=False)
    loan_type_interest = db.Column(db.String(64))
    source = db.Column(db.String(64))
    status = db.Column(db.String(32), nullable=False, default="NEW")
    created_at = db.Column(db.DateTime, default=func.now())
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"))

    customer = relationship("Customer")


class Loan(db.Model):
    __tablename__ = "loans"

    id = db.Column(db.Integer, primary_key=True)
    loan_number = db.Column(db.String(50), unique=True, index=True, nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False)
    principal_amount = db.Column(Numeric(12, 2), nullable=False)
    interest_rate = db.Column(Numeric(5, 2), nullable=False)
    total_days = db.Column(db.Integer, nullable=False)
    payment_interval_days = db.Column(db.Integer, nullable=False, default=7)
    daily_installment = db.Column(Numeric(12, 2), nullable=False)
    total_payable = db.Column(Numeric(12, 2), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(50), default="Active")
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    customer = relationship("Customer", back_populates="loans")
    created_by = relationship(
        "User", back_populates="created_loans", foreign_keys=[created_by_id]
    )
    payments = relationship("Payment", back_populates="loan")
    ledger_entries = relationship(
        "LoanLedger",
        back_populates="loan",
        cascade="all, delete-orphan",
        order_by="LoanLedger.installment_no",
    )

    @property
    def total_paid(self) -> Decimal:
        return sum(
            (payment.amount_collected for payment in self.payments), Decimal("0")
        )

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


class LoanLedger(db.Model):
    __tablename__ = "loan_ledger"
    __table_args__ = (
        db.UniqueConstraint(
            "loan_id", "installment_no", name="uq_loan_ledger_loan_installment"
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    loan_id = db.Column(
        db.Integer, db.ForeignKey("loans.id"), nullable=False, index=True
    )
    installment_no = db.Column(db.Integer, nullable=False)
    period_start_date = db.Column(db.Date, nullable=False)
    due_date = db.Column(db.Date, nullable=False)
    period_days = db.Column(db.Integer, nullable=False)
    opening_balance = db.Column(Numeric(12, 2), nullable=False)
    interest_amount = db.Column(Numeric(12, 2), nullable=False)
    principal_amount = db.Column(Numeric(12, 2), nullable=False)
    installment_amount = db.Column(Numeric(12, 2), nullable=False)
    closing_balance = db.Column(Numeric(12, 2), nullable=False)
    paid_amount = db.Column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    paid_date = db.Column(db.Date)
    delay_days = db.Column(db.Integer, nullable=False, default=0)
    delay_interest = db.Column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    status = db.Column(db.String(20), nullable=False, default="PENDING")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    loan = relationship("Loan", back_populates="ledger_entries")


class LoanApplication(db.Model):
    __tablename__ = "loan_applications"

    id = db.Column(db.Integer, primary_key=True)
    application_number = db.Column(
        db.String(50), unique=True, index=True, nullable=False
    )
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False)
    loan_type = db.Column(db.String(50), nullable=False)
    status = db.Column(db.String(50), default="DRAFT", nullable=False)
    applied_amount = db.Column(Numeric(12, 2), nullable=False)
    tenure_months = db.Column(db.Integer, nullable=False)
    interest_rate = db.Column(Numeric(5, 2))
    approved_amount = db.Column(Numeric(12, 2))
    approved_tenure = db.Column(db.Integer)
    review_notes = db.Column(db.Text)
    reject_reason = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    submitted_at = db.Column(db.DateTime)
    approved_at = db.Column(db.DateTime)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    assigned_officer_id = db.Column(db.Integer, db.ForeignKey("users.id"))

    full_name = db.Column(db.String(150), nullable=False)
    nic_number = db.Column(db.String(50), nullable=False)
    mobile_number = db.Column(db.String(20), nullable=False)
    email = db.Column(db.String(120))
    address_line1 = db.Column(db.String(255))
    address_line2 = db.Column(db.String(255))
    city = db.Column(db.String(120))
    district = db.Column(db.String(120))
    province = db.Column(db.String(120))
    date_of_birth = db.Column(db.Date)
    monthly_income = db.Column(Numeric(12, 2))
    monthly_expenses = db.Column(Numeric(12, 2))
    has_existing_loans = db.Column(db.Boolean, default=False)
    existing_loan_details = db.Column(db.Text)
    extra_data = db.Column(db.JSON, default=dict)

    customer = relationship("Customer", back_populates="loan_applications")
    created_by = relationship(
        "User", foreign_keys=[created_by_id], back_populates="created_applications"
    )
    assigned_officer = relationship(
        "User",
        foreign_keys=[assigned_officer_id],
        back_populates="assigned_applications",
    )
    documents = relationship(
        "LoanApplicationDocument",
        back_populates="loan_application",
        cascade="all, delete-orphan",
    )


class LoanApplicationDocument(db.Model):
    __tablename__ = "loan_application_documents"

    id = db.Column(db.Integer, primary_key=True)
    loan_application_id = db.Column(
        db.Integer, db.ForeignKey("loan_applications.id"), nullable=False
    )
    document_type = db.Column(db.String(50), nullable=False)
    file_path = db.Column(db.String(255), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    loan_application = relationship("LoanApplication", back_populates="documents")


class Payment(db.Model):
    __tablename__ = "payments"

    id = db.Column(db.Integer, primary_key=True)
    loan_id = db.Column(db.Integer, db.ForeignKey("loans.id"), nullable=False)
    collection_date = db.Column(db.Date, default=date.today, nullable=False)
    amount_collected = db.Column(Numeric(12, 2), nullable=False)
    principal_paid = db.Column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    interest_paid = db.Column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    penalty_paid = db.Column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    other_fee_paid = db.Column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    collected_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    payment_method = db.Column(db.String(50), default="Cash")
    remarks = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    loan = relationship("Loan", back_populates="payments")
    collected_by = relationship(
        "User", back_populates="collected_payments", foreign_keys=[collected_by_id]
    )

ACCOUNT_TYPES = ("ASSET", "LIABILITY", "EQUITY", "INCOME", "EXPENSE")
NORMAL_BALANCES = ("DEBIT", "CREDIT")
JOURNAL_STATUSES = ("DRAFT", "POSTED", "REVERSED")


class AccountingAccount(db.Model):
    __tablename__ = "accounting_accounts"
    __table_args__ = (
        db.CheckConstraint("account_type in ('ASSET','LIABILITY','EQUITY','INCOME','EXPENSE')", name="ck_accounting_accounts_type"),
        db.CheckConstraint("normal_balance in ('DEBIT','CREDIT')", name="ck_accounting_accounts_normal_balance"),
    )

    id = db.Column(db.Integer, primary_key=True)
    account_code = db.Column(db.String(32), unique=True, index=True, nullable=False)
    account_name = db.Column(db.String(150), nullable=False)
    account_type = db.Column(db.String(20), nullable=False)
    normal_balance = db.Column(db.String(10), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey("accounting_accounts.id"))
    description = db.Column(db.Text)
    cash_flow_category = db.Column(db.String(20), nullable=False, default="NONE")
    is_system_account = db.Column(db.Boolean, nullable=False, default=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    allow_manual_posting = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    parent = relationship("AccountingAccount", remote_side=[id], backref="children")


class AccountingJournalEntry(db.Model):
    __tablename__ = "accounting_journal_entries"

    id = db.Column(db.Integer, primary_key=True)
    journal_no = db.Column(db.String(40), unique=True, index=True, nullable=False)
    journal_date = db.Column(db.Date, index=True, nullable=False)
    description = db.Column(db.Text, nullable=False)
    reference_type = db.Column(db.String(50))
    reference_id = db.Column(db.String(64))
    source_module = db.Column(db.String(50))
    status = db.Column(db.String(20), nullable=False, default="DRAFT")
    posted_at = db.Column(db.DateTime)
    posted_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    reversal_of_id = db.Column(db.Integer, db.ForeignKey("accounting_journal_entries.id"))
    idempotency_key = db.Column(db.String(160), unique=True, index=True)
    total_debit = db.Column(Numeric(18, 2), nullable=False, default=Decimal("0.00"))
    total_credit = db.Column(Numeric(18, 2), nullable=False, default=Decimal("0.00"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    lines = relationship("AccountingJournalLine", back_populates="journal_entry", cascade="all, delete-orphan", order_by="AccountingJournalLine.line_no")
    reversal_of = relationship("AccountingJournalEntry", remote_side=[id])


class AccountingJournalLine(db.Model):
    __tablename__ = "accounting_journal_lines"
    __table_args__ = (
        db.UniqueConstraint("journal_entry_id", "line_no", name="uq_accounting_journal_line_no"),
        db.CheckConstraint("debit >= 0", name="ck_accounting_journal_lines_debit_nonnegative"),
        db.CheckConstraint("credit >= 0", name="ck_accounting_journal_lines_credit_nonnegative"),
        db.CheckConstraint("(debit > 0 and credit = 0) or (credit > 0 and debit = 0)", name="ck_accounting_journal_lines_one_sided"),
    )

    id = db.Column(db.Integer, primary_key=True)
    journal_entry_id = db.Column(db.Integer, db.ForeignKey("accounting_journal_entries.id"), nullable=False, index=True)
    line_no = db.Column(db.Integer, nullable=False)
    account_id = db.Column(db.Integer, db.ForeignKey("accounting_accounts.id"), nullable=False, index=True)
    debit = db.Column(Numeric(18, 2), nullable=False, default=Decimal("0.00"))
    credit = db.Column(Numeric(18, 2), nullable=False, default=Decimal("0.00"))
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"))
    loan_id = db.Column(db.Integer, db.ForeignKey("loans.id"))
    payment_id = db.Column(db.Integer, db.ForeignKey("payments.id"))
    collection_id = db.Column(db.Integer)
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    journal_entry = relationship("AccountingJournalEntry", back_populates="lines")
    account = relationship("AccountingAccount")


class AccountingSetting(db.Model):
    __tablename__ = "accounting_settings"

    id = db.Column(db.Integer, primary_key=True)
    setting_key = db.Column(db.String(80), unique=True, nullable=False)
    setting_value = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class AccountingAuditLog(db.Model):
    __tablename__ = "accounting_audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    action = db.Column(db.String(80), nullable=False)
    entity_type = db.Column(db.String(80), nullable=False)
    entity_id = db.Column(db.String(64))
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    details = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
