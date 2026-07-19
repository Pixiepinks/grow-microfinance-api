from datetime import datetime, date
from decimal import Decimal
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy.orm import relationship
from sqlalchemy import Numeric, func, Index

from .extensions import db


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, index=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    is_collector = db.Column(db.Boolean, nullable=False, default=False)
    collector_code = db.Column(db.String(50), nullable=True, unique=True)
    collector_status = db.Column(db.String(20), nullable=False, default="ACTIVE")
    default_collection_account_id = db.Column(db.Integer, db.ForeignKey("accounting_accounts.id"), nullable=True)
    can_collect_cash = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    must_change_password = db.Column(db.Boolean, nullable=False, default=False)
    password_changed_at = db.Column(db.DateTime, nullable=True)
    token_version = db.Column(db.Integer, nullable=False, default=0)
    failed_login_attempts = db.Column(db.Integer, nullable=False, default=0)
    locked_until = db.Column(db.DateTime, nullable=True)

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


class PasswordHistory(db.Model):
    __tablename__ = "password_history"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class RevokedToken(db.Model):
    __tablename__ = "revoked_tokens"

    id = db.Column(db.Integer, primary_key=True)
    jti = db.Column(db.String(36), nullable=False, unique=True, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    token_type = db.Column(db.String(16), nullable=True)
    revoked_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

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
    status = db.Column(db.String(50), default="ACTIVE")
    lead_status = db.Column(db.String(32), nullable=False, default="NEW")
    kyc_status = db.Column(db.String(32), nullable=False, default="PENDING")
    eligibility_status = db.Column(db.String(32), nullable=False, default="UNKNOWN")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_customers_nic_number", "nic_number"),
        Index("ix_customers_mobile", "mobile"),
        Index("ix_customers_lower_full_name", func.lower(full_name)),
    )

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
    interest_rate = db.Column(Numeric(9, 4), nullable=False)
    total_days = db.Column(db.Integer, nullable=False)
    payment_interval_days = db.Column(db.Integer, nullable=False, default=7)
    daily_installment = db.Column(Numeric(12, 2), nullable=False)
    total_payable = db.Column(Numeric(12, 2), nullable=False)
    loan_days = db.Column(db.Integer)
    tenure_months = db.Column(db.Integer)
    term_type = db.Column(db.String(20))
    term_value = db.Column(db.Integer)
    repayment_frequency = db.Column(db.String(20))
    number_of_installments = db.Column(db.Integer)
    installment_count = db.Column(db.Integer)
    installment_amount = db.Column(Numeric(12, 2))
    total_repayment = db.Column(Numeric(12, 2))
    total_interest = db.Column(Numeric(12, 2))
    interest_type = db.Column(db.String(20))
    interest_rate_basis = db.Column(db.String(20))
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    maturity_date = db.Column(db.Date)
    final_installment_due_date = db.Column(db.Date)
    status = db.Column(db.String(50), default="ACTIVE")
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    interest_accounting_method = db.Column(db.String(32), nullable=False, default="ACCRUAL_BY_INSTALLMENT")
    historical_accrual_mode = db.Column(db.String(16), nullable=False, default="AUTO")
    accrual_processed_through = db.Column(db.Date)
    disbursement_journal_id = db.Column(db.Integer, db.ForeignKey("accounting_journal_entries.id"))
    gross_principal_amount = db.Column(Numeric(18, 2))
    total_disbursement_deductions = db.Column(Numeric(18, 2), nullable=False, default=Decimal("0.00"))
    net_disbursed_amount = db.Column(Numeric(18, 2))
    disbursement_charge_count = db.Column(db.Integer, nullable=False, default=0)
    disbursement_deductions_posted = db.Column(db.Boolean, nullable=False, default=False)
    reversed_at = db.Column(db.DateTime)
    reversal_journal_id = db.Column(db.Integer, db.ForeignKey("accounting_journal_entries.id"))
    settled_at = db.Column(db.DateTime)
    settled_date = db.Column(db.Date)
    settled_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    settlement_payment_id = db.Column(db.Integer, db.ForeignKey("payments.id"))
    settlement_journal_id = db.Column(db.Integer, db.ForeignKey("accounting_journal_entries.id"))
    settlement_reason = db.Column(db.String(50))
    customer_credit_balance = db.Column(Numeric(18, 2), nullable=False, default=Decimal("0.00"))

    customer = relationship("Customer", back_populates="loans")
    created_by = relationship(
        "User", back_populates="created_loans", foreign_keys=[created_by_id]
    )
    payments = relationship("Payment", back_populates="loan", foreign_keys="Payment.loan_id")
    ledger_entries = relationship(
        "LoanLedger",
        back_populates="loan",
        cascade="all, delete-orphan",
        order_by="LoanLedger.installment_no",
    )

    @property
    def total_paid(self) -> Decimal:
        return sum(
            (payment.amount_collected for payment in self.payments if not payment.reversed_at and payment.status != "REVERSED"), Decimal("0")
        )

    @property
    def outstanding(self) -> Decimal:
        return max(Decimal("0.00"), Decimal(self.total_payable or 0) - self.total_paid)

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
    period_start_date = db.Column(db.Date)
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
    interest_accrued = db.Column(db.Boolean, nullable=False, default=False)
    interest_accrued_at = db.Column(db.DateTime)
    interest_accrual_journal_id = db.Column(db.Integer, db.ForeignKey("accounting_journal_entries.id"))
    delay_interest_accrued = db.Column(Numeric(18, 2), nullable=False, default=Decimal("0.00"))
    delay_interest_accrued_at = db.Column(db.DateTime)
    delay_interest_accrual_journal_id = db.Column(db.Integer, db.ForeignKey("accounting_journal_entries.id"))
    principal_paid = db.Column(Numeric(18, 2), nullable=False, default=Decimal("0.00"))
    interest_paid = db.Column(Numeric(18, 2), nullable=False, default=Decimal("0.00"))
    delay_interest_paid = db.Column(Numeric(18, 2), nullable=False, default=Decimal("0.00"))
    unapplied_amount = db.Column(Numeric(18, 2), nullable=False, default=Decimal("0.00"))
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
    interest_rate = db.Column(Numeric(9, 4))
    approved_amount = db.Column(Numeric(12, 2))
    approved_tenure = db.Column(db.Integer)
    loan_days = db.Column(db.Integer)
    term_type = db.Column(db.String(20))
    term_value = db.Column(db.Integer)
    repayment_frequency = db.Column(db.String(20))
    number_of_installments = db.Column(db.Integer)
    installment_count = db.Column(db.Integer)
    installment_amount = db.Column(Numeric(12, 2))
    total_repayment = db.Column(Numeric(12, 2))
    total_interest = db.Column(Numeric(12, 2))
    interest_type = db.Column(db.String(20))
    interest_rate_basis = db.Column(db.String(20))
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
    proposed_disbursement_deductions = db.Column(db.JSON, default=list)
    estimated_total_deductions = db.Column(Numeric(18, 2), nullable=False, default=Decimal("0.00"))
    estimated_net_disbursement = db.Column(Numeric(18, 2))

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


class DisbursementChargeType(db.Model):
    __tablename__ = "disbursement_charge_types"
    __table_args__ = (
        db.CheckConstraint("calculation_method in ('FIXED_AMOUNT','PERCENTAGE_OF_PRINCIPAL','MANUAL_AMOUNT')", name="ck_disb_charge_calc_method"),
        db.CheckConstraint("accounting_treatment in ('INCOME','PAYABLE','EXPENSE_RECOVERY','TAX','OTHER')", name="ck_disb_charge_acct_treatment"),
        db.CheckConstraint("tax_method in ('NO_TAX','TAX_EXCLUSIVE','TAX_INCLUSIVE')", name="ck_disb_charge_tax_method"),
    )

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, index=True, nullable=False)
    name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text)
    active = db.Column(db.Boolean, nullable=False, default=True)
    default_amount = db.Column(Numeric(18, 2))
    default_rate = db.Column(Numeric(9, 4))
    calculation_method = db.Column(db.String(40), nullable=False)
    accounting_treatment = db.Column(db.String(40), nullable=False)
    income_account_id = db.Column(db.Integer, db.ForeignKey("accounting_accounts.id"))
    payable_account_id = db.Column(db.Integer, db.ForeignKey("accounting_accounts.id"))
    expense_account_id = db.Column(db.Integer, db.ForeignKey("accounting_accounts.id"))
    tax_payable_account_id = db.Column(db.Integer, db.ForeignKey("accounting_accounts.id"))
    tax_rate = db.Column(Numeric(9, 4))
    tax_method = db.Column(db.String(30), nullable=False, default="NO_TAX")
    included_in_principal = db.Column(db.Boolean, nullable=False, default=False)
    deducted_from_disbursement = db.Column(db.Boolean, nullable=False, default=True)
    refundable = db.Column(db.Boolean, nullable=False, default=False)
    display_order = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    income_account = relationship("AccountingAccount", foreign_keys=[income_account_id])
    payable_account = relationship("AccountingAccount", foreign_keys=[payable_account_id])
    expense_account = relationship("AccountingAccount", foreign_keys=[expense_account_id])
    tax_payable_account = relationship("AccountingAccount", foreign_keys=[tax_payable_account_id])


class LoanDisbursementDeduction(db.Model):
    __tablename__ = "loan_disbursement_deductions"
    __table_args__ = (
        Index("ix_loan_disb_deduction_loan_id", "loan_id"),
        Index("ix_loan_disb_deduction_charge_type_id", "charge_type_id"),
        Index("ix_loan_disb_deduction_status", "status"),
        db.CheckConstraint("status in ('DRAFT','POSTED','REVERSED','WAIVED')", name="ck_loan_disb_deduction_status"),
    )

    id = db.Column(db.Integer, primary_key=True)
    loan_id = db.Column(db.Integer, db.ForeignKey("loans.id"), nullable=False)
    loan_application_id = db.Column(db.Integer, db.ForeignKey("loan_applications.id"))
    charge_type_id = db.Column(db.Integer, db.ForeignKey("disbursement_charge_types.id"), nullable=False)
    description = db.Column(db.Text, nullable=False)
    gross_amount = db.Column(Numeric(18, 2), nullable=False)
    tax_amount = db.Column(Numeric(18, 2), nullable=False, default=Decimal("0.00"))
    net_charge_amount = db.Column(Numeric(18, 2), nullable=False)
    calculation_method = db.Column(db.String(40), nullable=False)
    rate = db.Column(Numeric(9, 4))
    accounting_treatment = db.Column(db.String(40), nullable=False)
    destination_account_id = db.Column(db.Integer, db.ForeignKey("accounting_accounts.id"), nullable=False)
    tax_account_id = db.Column(db.Integer, db.ForeignKey("accounting_accounts.id"))
    status = db.Column(db.String(20), nullable=False, default="DRAFT")
    journal_entry_id = db.Column(db.Integer, db.ForeignKey("accounting_journal_entries.id"))
    reversed_at = db.Column(db.DateTime)
    reversal_journal_id = db.Column(db.Integer, db.ForeignKey("accounting_journal_entries.id"))
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    loan = relationship("Loan", backref="disbursement_deductions")
    charge_type = relationship("DisbursementChargeType")
    destination_account = relationship("AccountingAccount", foreign_keys=[destination_account_id])
    tax_account = relationship("AccountingAccount", foreign_keys=[tax_account_id])


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
    transaction_reference = db.Column(db.String(120))
    receipt_account_id = db.Column(db.Integer, db.ForeignKey("accounting_accounts.id"))
    remarks = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    journal_id = db.Column(db.Integer, db.ForeignKey("accounting_journal_entries.id"))
    reversed_at = db.Column(db.DateTime)
    reversal_journal_id = db.Column(db.Integer, db.ForeignKey("accounting_journal_entries.id"))
    reversal_reason = db.Column(db.Text)
    payment_date = db.Column(db.Date)
    accounting_date = db.Column(db.Date)
    collection_method = db.Column(db.String(50))
    collection_account_id = db.Column(db.Integer, db.ForeignKey("accounting_accounts.id"))
    collector_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    bank_reference = db.Column(db.String(120))
    receipt_number = db.Column(db.String(40), unique=True, index=True)
    status = db.Column(db.String(20), nullable=False, default="POSTED")
    reversed_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    deposited_amount = db.Column(Numeric(18, 2), nullable=False, default=Decimal("0.00"))
    deposit_status = db.Column(db.String(30), nullable=False, default="NOT_APPLICABLE")

    @property
    def undeposited_amount(self):
        return Decimal(self.amount_collected or 0) - Decimal(self.deposited_amount or 0)

    loan = relationship("Loan", back_populates="payments", foreign_keys=[loan_id])
    collected_by = relationship(
        "User", back_populates="collected_payments", foreign_keys=[collected_by_id]
    )
    collector = relationship("User", foreign_keys=[collector_id])
    collection_account = relationship("AccountingAccount", foreign_keys=[collection_account_id])

ACCOUNT_TYPES = ("ASSET", "LIABILITY", "EQUITY", "INCOME", "EXPENSE")
ACCOUNT_SUBTYPES = ("CASH", "BANK", "COLLECTION_CLEARING", "COLLECTION_CLEARING_CONTROL", "LOAN_RECEIVABLE", "INTEREST_RECEIVABLE", "PENALTY_RECEIVABLE", "OTHER_CURRENT_ASSET", "FIXED_ASSET", "ACCOUNTS_PAYABLE", "BORROWING", "CUSTOMER_ADVANCE", "CAPITAL", "RETAINED_EARNINGS", "INTEREST_INCOME", "PENALTY_INCOME", "FEE_INCOME", "OPERATING_EXPENSE", "WRITE_OFF_EXPENSE", "SUSPENSE", "OTHER")
NORMAL_BALANCES = ("DEBIT", "CREDIT")
JOURNAL_STATUSES = ("DRAFT", "POSTED", "REVERSED")


class AccountingAccount(db.Model):
    __tablename__ = "accounting_accounts"
    __table_args__ = (
        db.CheckConstraint("account_type in ('ASSET','LIABILITY','EQUITY','INCOME','EXPENSE')", name="ck_accounting_accounts_type"),
        db.CheckConstraint("normal_balance in ('DEBIT','CREDIT')", name="ck_accounting_accounts_normal_balance"),
        db.CheckConstraint("account_subtype in ('CASH','BANK','COLLECTION_CLEARING','COLLECTION_CLEARING_CONTROL','LOAN_RECEIVABLE','INTEREST_RECEIVABLE','PENALTY_RECEIVABLE','OTHER_CURRENT_ASSET','FIXED_ASSET','ACCOUNTS_PAYABLE','BORROWING','CAPITAL','RETAINED_EARNINGS','INTEREST_INCOME','PENALTY_INCOME','FEE_INCOME','OPERATING_EXPENSE','WRITE_OFF_EXPENSE','SUSPENSE','OTHER')", name="ck_accounting_accounts_subtype"),
    )

    id = db.Column(db.Integer, primary_key=True)
    account_code = db.Column(db.String(32), unique=True, index=True, nullable=False)
    account_name = db.Column(db.String(150), nullable=False)
    account_type = db.Column(db.String(20), nullable=False)
    normal_balance = db.Column(db.String(10), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey("accounting_accounts.id"))
    parent_account_id = db.Column(db.Integer, db.ForeignKey("accounting_accounts.id"))
    collector_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    is_collection_account = db.Column(db.Boolean, nullable=False, default=False)
    account_role = db.Column(db.String(50))
    description = db.Column(db.Text)
    cash_flow_category = db.Column(db.String(50), nullable=False, default="NONE")
    account_subtype = db.Column(db.String(50), nullable=False, default="OTHER")
    financial_statement_group = db.Column(db.String(40))
    financial_statement_order = db.Column(db.Integer)
    cash_flow_group = db.Column(db.String(40))
    is_system_account = db.Column(db.Boolean, nullable=False, default=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    allow_manual_posting = db.Column(db.Boolean, nullable=False, default=True)
    requires_customer = db.Column(db.Boolean, nullable=False, default=False)
    requires_loan = db.Column(db.Boolean, nullable=False, default=False)
    allows_customer = db.Column(db.Boolean, nullable=False, default=True)
    allows_loan = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    parent = relationship("AccountingAccount", remote_side=[id], foreign_keys=[parent_id], backref="children")
    parent_account = relationship("AccountingAccount", remote_side=[id], foreign_keys=[parent_account_id])
    collector = relationship("User", foreign_keys=[collector_id])


class AccountingJournalEntry(db.Model):
    __tablename__ = "accounting_journal_entries"
    __table_args__ = (
        Index("uq_journal_source_posted", "source_type", "source_id", unique=True, postgresql_where=db.text("source_type is not null and source_id is not null and status != 'REVERSED'")),
    )

    id = db.Column(db.Integer, primary_key=True)
    journal_no = db.Column(db.String(40), unique=True, index=True, nullable=False)
    reference = db.Column(db.String(120))
    journal_date = db.Column(db.Date, index=True, nullable=False)
    description = db.Column(db.Text, nullable=False)
    reference_type = db.Column(db.String(50))
    reference_id = db.Column(db.String(64))
    source_module = db.Column(db.String(50))
    source_type = db.Column(db.String(80), index=True)
    source_id = db.Column(db.Integer, index=True)
    loan_id = db.Column(db.Integer, db.ForeignKey("loans.id"), index=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), index=True)
    accounting_date = db.Column(db.Date, index=True)
    reversal_of_journal_id = db.Column(db.Integer, db.ForeignKey("accounting_journal_entries.id"))
    is_reversal = db.Column(db.Boolean, nullable=False, default=False)
    status = db.Column(db.String(20), nullable=False, default="DRAFT")
    posted_at = db.Column(db.DateTime)
    reversed_at = db.Column(db.DateTime)
    reversal_journal_id = db.Column(db.Integer, db.ForeignKey("accounting_journal_entries.id"))
    posted_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    reversal_of_id = db.Column(db.Integer, db.ForeignKey("accounting_journal_entries.id"))
    idempotency_key = db.Column(db.String(160), unique=True, index=True)
    total_debit = db.Column(Numeric(18, 2), nullable=False, default=Decimal("0.00"))
    total_credit = db.Column(Numeric(18, 2), nullable=False, default=Decimal("0.00"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    lines = relationship("AccountingJournalLine", back_populates="journal_entry", cascade="all, delete-orphan", order_by="AccountingJournalLine.line_no")
    reversal_of = relationship("AccountingJournalEntry", remote_side=[id], foreign_keys=[reversal_of_id], backref="reversal_journals")
    created_by = relationship("User", foreign_keys=[created_by_id])
    posted_by = relationship("User", foreign_keys=[posted_by_id])

    @property
    def journal_number(self):
        return self.journal_no

    @journal_number.setter
    def journal_number(self, value):
        self.journal_no = value


class PaymentAllocation(db.Model):
    __tablename__ = "payment_allocations"

    id = db.Column(db.Integer, primary_key=True)
    payment_id = db.Column(db.Integer, db.ForeignKey("payments.id"), nullable=False, index=True)
    loan_id = db.Column(db.Integer, db.ForeignKey("loans.id"), nullable=False, index=True)
    ledger_id = db.Column(db.Integer, db.ForeignKey("loan_ledger.id"), index=True)
    allocation_type = db.Column(db.String(32), nullable=False)
    amount = db.Column(Numeric(18, 2), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    payment = relationship("Payment", backref="allocations")
    loan = relationship("Loan")
    ledger = relationship("LoanLedger")


class CustomerCreditBalance(db.Model):
    __tablename__ = "customer_credit_balances"
    __table_args__ = (db.UniqueConstraint("source_type", "source_id", name="uq_customer_credit_source"),)

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False, index=True)
    loan_id = db.Column(db.Integer, db.ForeignKey("loans.id"), index=True)
    payment_id = db.Column(db.Integer, db.ForeignKey("payments.id"), unique=True, index=True)
    credit_number = db.Column(db.String(40), nullable=False, unique=True, index=True)
    credit_date = db.Column(db.Date, nullable=False)
    source_type = db.Column(db.String(50), nullable=False)
    source_id = db.Column(db.String(64), nullable=False)
    original_amount = db.Column(Numeric(18, 2), nullable=False)
    available_amount = db.Column(Numeric(18, 2), nullable=False)
    applied_amount = db.Column(Numeric(18, 2), nullable=False, default=Decimal("0.00"))
    refunded_amount = db.Column(Numeric(18, 2), nullable=False, default=Decimal("0.00"))
    status = db.Column(db.String(30), nullable=False, default="AVAILABLE")
    reference = db.Column(db.String(120))
    remarks = db.Column(db.Text)
    journal_entry_id = db.Column(db.Integer, db.ForeignKey("accounting_journal_entries.id"))
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    customer = relationship("Customer")
    loan = relationship("Loan", foreign_keys=[loan_id])
    payment = relationship("Payment", foreign_keys=[payment_id])


class AccountingPeriod(db.Model):
    __tablename__ = "accounting_periods"

    id = db.Column(db.Integer, primary_key=True)
    period = db.Column(db.String(7), unique=True, nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    is_locked = db.Column(db.Boolean, nullable=False, default=False)
    locked_at = db.Column(db.DateTime)
    locked_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


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
    investor_id = db.Column(db.Integer)
    investor_agreement_id = db.Column(db.Integer)
    collector_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"))
    loan_id = db.Column(db.Integer, db.ForeignKey("loans.id"))
    payment_id = db.Column(db.Integer, db.ForeignKey("payments.id"))
    collection_id = db.Column(db.Integer)
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    journal_entry = relationship("AccountingJournalEntry", back_populates="lines")
    account = relationship("AccountingAccount")
    customer = relationship("Customer")
    loan = relationship("Loan")
    payment = relationship("Payment")

    @property
    def debit_amount(self):
        return self.debit

    @debit_amount.setter
    def debit_amount(self, value):
        self.debit = value

    @property
    def credit_amount(self):
        return self.credit

    @credit_amount.setter
    def credit_amount(self, value):
        self.credit = value


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


class CollectionDepositBatch(db.Model):
    __tablename__ = "collection_deposit_batches"

    id = db.Column(db.Integer, primary_key=True)
    deposit_number = db.Column(db.String(40), unique=True, index=True, nullable=False)
    collector_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    collector_account_id = db.Column(db.Integer, db.ForeignKey("accounting_accounts.id"), nullable=False)
    bank_account_id = db.Column(db.Integer, db.ForeignKey("accounting_accounts.id"), nullable=False)
    deposit_date = db.Column(db.Date, nullable=False)
    accounting_date = db.Column(db.Date, nullable=False)
    total_amount = db.Column(Numeric(18, 2), nullable=False)
    bank_reference = db.Column(db.String(120))
    deposit_slip_reference = db.Column(db.String(120))
    remarks = db.Column(db.Text)
    journal_entry_id = db.Column(db.Integer, db.ForeignKey("accounting_journal_entries.id"))
    reversal_journal_id = db.Column(db.Integer, db.ForeignKey("accounting_journal_entries.id"))
    status = db.Column(db.String(20), nullable=False, default="DRAFT")
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    reversed_at = db.Column(db.DateTime)
    reversal_reason = db.Column(db.Text)

    collector = relationship("User", foreign_keys=[collector_id])
    collector_account = relationship("AccountingAccount", foreign_keys=[collector_account_id])
    bank_account = relationship("AccountingAccount", foreign_keys=[bank_account_id])
    journal_entry = relationship("AccountingJournalEntry", foreign_keys=[journal_entry_id])
    allocations = relationship("CollectionDepositAllocation", back_populates="deposit_batch", cascade="all, delete-orphan")


class CollectionDepositAllocation(db.Model):
    __tablename__ = "collection_deposit_allocations"

    id = db.Column(db.Integer, primary_key=True)
    deposit_batch_id = db.Column(db.Integer, db.ForeignKey("collection_deposit_batches.id"), nullable=False, index=True)
    payment_id = db.Column(db.Integer, db.ForeignKey("payments.id"), nullable=False, index=True)
    allocated_amount = db.Column(Numeric(18, 2), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    deposit_batch = relationship("CollectionDepositBatch", back_populates="allocations")
    payment = relationship("Payment")

    @property
    def debit_amount(self):
        return self.debit

    @debit_amount.setter
    def debit_amount(self, value):
        self.debit = value

    @property
    def credit_amount(self):
        return self.credit

    @credit_amount.setter
    def credit_amount(self, value):
        self.credit = value

class Investor(db.Model):
    __tablename__ = "investors"
    __table_args__ = (db.UniqueConstraint("investor_number", name="uq_investors_investor_number"),)
    id = db.Column(db.Integer, primary_key=True)
    investor_number = db.Column(db.String(32), index=True, nullable=False)
    investor_type = db.Column(db.String(20), nullable=False, default="INDIVIDUAL")
    full_name = db.Column(db.String(150), nullable=False)
    company_name = db.Column(db.String(150))
    nic = db.Column(db.String(50))
    company_registration_number = db.Column(db.String(80))
    tax_identification_number = db.Column(db.String(80))
    mobile = db.Column(db.String(30))
    email = db.Column(db.String(120))
    address = db.Column(db.Text)
    bank_name = db.Column(db.String(120))
    bank_branch = db.Column(db.String(120))
    bank_account_name = db.Column(db.String(150))
    bank_account_number = db.Column(db.String(80))
    status = db.Column(db.String(20), nullable=False, default="ACTIVE", index=True)
    notes = db.Column(db.Text)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    deactivated_at = db.Column(db.DateTime)


class InvestorFundingAgreement(db.Model):
    __tablename__ = "investor_funding_agreements"
    __table_args__ = (db.UniqueConstraint("agreement_number", name="uq_investor_funding_agreement_number"),)
    id = db.Column(db.Integer, primary_key=True)
    agreement_number = db.Column(db.String(40), nullable=False, index=True)
    investor_id = db.Column(db.Integer, db.ForeignKey("investors.id"), nullable=False, index=True)
    agreement_name = db.Column(db.String(150))
    agreement_date = db.Column(db.Date, nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    maturity_date = db.Column(db.Date)
    currency = db.Column(db.String(3), nullable=False, default="LKR")
    original_principal_amount = db.Column(Numeric(18, 2), nullable=False, default=Decimal("0.00"))
    current_principal_balance = db.Column(Numeric(18, 2), nullable=False, default=Decimal("0.00"))
    interest_rate = db.Column(Numeric(10, 4), nullable=False, default=Decimal("0.0000"))
    interest_rate_period = db.Column(db.String(20), nullable=False, default="MONTHLY")
    calculation_method = db.Column(db.String(40), nullable=False, default="MONTHLY_AVERAGE_DAILY_BALANCE")
    interest_payment_frequency = db.Column(db.String(20), nullable=False, default="MONTHLY")
    compounding_method = db.Column(db.String(30), nullable=False, default="SIMPLE")
    day_count_basis = db.Column(db.String(20), nullable=False, default="ACTUAL")
    interest_payment_method = db.Column(db.String(30), nullable=False, default="BANK_TRANSFER")
    funding_account_id = db.Column(db.Integer, db.ForeignKey("accounting_accounts.id"))
    investor_liability_account_id = db.Column(db.Integer, db.ForeignKey("accounting_accounts.id"), nullable=False)
    interest_expense_account_id = db.Column(db.Integer, db.ForeignKey("accounting_accounts.id"), nullable=False)
    accrued_interest_payable_account_id = db.Column(db.Integer, db.ForeignKey("accounting_accounts.id"), nullable=False)
    withholding_tax_account_id = db.Column(db.Integer, db.ForeignKey("accounting_accounts.id"))
    withholding_tax_rate = db.Column(Numeric(10, 4))
    allow_additional_funding = db.Column(db.Boolean, nullable=False, default=True)
    allow_partial_withdrawal = db.Column(db.Boolean, nullable=False, default=True)
    auto_accrual_enabled = db.Column(db.Boolean, nullable=False, default=True, index=True)
    auto_capitalize_interest = db.Column(db.Boolean, nullable=False, default=False)
    status = db.Column(db.String(20), nullable=False, default="DRAFT", index=True)
    closed_at = db.Column(db.DateTime)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    investor = relationship("Investor", backref="funding_agreements")


class InvestorFundingTransaction(db.Model):
    __tablename__ = "investor_funding_transactions"
    id = db.Column(db.Integer, primary_key=True)
    transaction_number = db.Column(db.String(40), unique=True, index=True, nullable=False)
    investor_id = db.Column(db.Integer, db.ForeignKey("investors.id"), nullable=False, index=True)
    agreement_id = db.Column(db.Integer, db.ForeignKey("investor_funding_agreements.id"), nullable=False, index=True)
    transaction_type = db.Column(db.String(30), nullable=False)
    transaction_date = db.Column(db.Date, nullable=False, index=True)
    accounting_date = db.Column(db.Date, nullable=False, index=True)
    amount = db.Column(Numeric(18, 2), nullable=False)
    bank_account_id = db.Column(db.Integer, db.ForeignKey("accounting_accounts.id"))
    reference = db.Column(db.String(120))
    remarks = db.Column(db.Text)
    journal_entry_id = db.Column(db.Integer, db.ForeignKey("accounting_journal_entries.id"))
    status = db.Column(db.String(20), nullable=False, default="DRAFT", index=True)
    reversal_of_transaction_id = db.Column(db.Integer, db.ForeignKey("investor_funding_transactions.id"))
    reversed_at = db.Column(db.DateTime)
    reversal_reason = db.Column(db.Text)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    agreement = relationship("InvestorFundingAgreement", backref="transactions")
    investor = relationship("Investor")
    journal_entry = relationship("AccountingJournalEntry")


class InvestorInterestAccrual(db.Model):
    __tablename__ = "investor_interest_accruals"
    __table_args__ = (db.UniqueConstraint("agreement_id", "accrual_period_start", "accrual_period_end", name="uq_investor_interest_period"),)
    id = db.Column(db.Integer, primary_key=True)
    investor_id = db.Column(db.Integer, db.ForeignKey("investors.id"), nullable=False, index=True)
    agreement_id = db.Column(db.Integer, db.ForeignKey("investor_funding_agreements.id"), nullable=False, index=True)
    accrual_period_start = db.Column(db.Date, nullable=False)
    accrual_period_end = db.Column(db.Date, nullable=False, index=True)
    days_in_period = db.Column(db.Integer, nullable=False)
    calendar_days_in_month = db.Column(db.Integer, nullable=False, default=0)
    opening_principal_balance = db.Column(Numeric(18, 2), nullable=False)
    closing_principal_balance = db.Column(Numeric(18, 2), nullable=False)
    average_daily_balance = db.Column(Numeric(18, 2), nullable=False)
    interest_rate = db.Column(Numeric(10, 4), nullable=False)
    interest_rate_period = db.Column(db.String(20), nullable=False)
    calculation_method = db.Column(db.String(40), nullable=False)
    gross_interest_amount = db.Column(Numeric(18, 2), nullable=False)
    withholding_tax_amount = db.Column(Numeric(18, 2), nullable=False, default=Decimal("0.00"))
    net_interest_payable = db.Column(Numeric(18, 2), nullable=False)
    capitalization_amount = db.Column(Numeric(18, 2), nullable=False, default=Decimal("0.00"))
    payment_amount = db.Column(Numeric(18, 2), nullable=False, default=Decimal("0.00"))
    status = db.Column(db.String(20), nullable=False, default="CALCULATED", index=True)
    journal_entry_id = db.Column(db.Integer, db.ForeignKey("accounting_journal_entries.id"))
    payment_journal_entry_id = db.Column(db.Integer, db.ForeignKey("accounting_journal_entries.id"))
    capitalization_journal_entry_id = db.Column(db.Integer, db.ForeignKey("accounting_journal_entries.id"))
    posted_at = db.Column(db.DateTime)
    reversed_at = db.Column(db.DateTime)
    reversal_journal_id = db.Column(db.Integer, db.ForeignKey("accounting_journal_entries.id"))
    reversed_at = db.Column(db.DateTime)
    reversal_journal_id = db.Column(db.Integer, db.ForeignKey("accounting_journal_entries.id"))
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    agreement = relationship("InvestorFundingAgreement", backref="interest_accruals")
    investor = relationship("Investor")
    journal_entry = relationship("AccountingJournalEntry", foreign_keys=[journal_entry_id])
