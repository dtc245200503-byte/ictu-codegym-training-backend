from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class TuitionPlan(Base):
    __tablename__ = "tuition_plans"
    __table_args__ = (
        UniqueConstraint("enrollment_id", name="uq_tuition_plan_enrollment"),
        CheckConstraint("total_due_vnd >= 0", name="ck_tuition_plan_nonnegative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    enrollment_id: Mapped[int] = mapped_column(
        ForeignKey("class_enrollments.id", ondelete="RESTRICT"), nullable=False
    )
    fee_schedule_id: Mapped[int] = mapped_column(
        ForeignKey("program_fee_schedules.id", ondelete="RESTRICT"), nullable=False
    )
    total_due_vnd: Mapped[Decimal] = mapped_column(Numeric(18, 0), nullable=False)
    applied_on: Mapped[date] = mapped_column(Date, nullable=False)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), server_default=func.current_timestamp(), nullable=False
    )


class TuitionInstallment(Base):
    __tablename__ = "tuition_installments"
    __table_args__ = (
        UniqueConstraint(
            "plan_id", "installment_number", name="uq_tuition_installment_number"
        ),
        CheckConstraint("amount_due_vnd > 0", name="ck_tuition_installment_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("tuition_plans.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    installment_number: Mapped[int] = mapped_column(Integer, nullable=False)
    amount_due_vnd: Mapped[Decimal] = mapped_column(Numeric(18, 0), nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)


class TuitionPayment(Base):
    __tablename__ = "tuition_payments"
    __table_args__ = (
        UniqueConstraint("receipt_code", name="uq_tuition_payment_receipt"),
        UniqueConstraint(
            "installment_id", "idempotency_key", name="uq_tuition_payment_idempotency"
        ),
        CheckConstraint("amount_vnd > 0", name="ck_tuition_payment_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    installment_id: Mapped[int] = mapped_column(
        ForeignKey("tuition_installments.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    receipt_code: Mapped[str] = mapped_column(String(40), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    amount_vnd: Mapped[Decimal] = mapped_column(Numeric(18, 0), nullable=False)
    method: Mapped[str] = mapped_column(String(20), nullable=False)
    paid_on: Mapped[date] = mapped_column(Date, nullable=False)
    collected_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), server_default=func.current_timestamp(), nullable=False
    )


class TuitionPaymentReversal(Base):
    __tablename__ = "tuition_payment_reversals"
    __table_args__ = (
        UniqueConstraint("payment_id", name="uq_tuition_payment_single_reversal"),
        UniqueConstraint(
            "payment_id", "idempotency_key", name="uq_tuition_reversal_idempotency"
        ),
        CheckConstraint("amount_vnd > 0", name="ck_tuition_reversal_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[int] = mapped_column(
        ForeignKey("tuition_payments.id", ondelete="RESTRICT"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    amount_vnd: Mapped[Decimal] = mapped_column(Numeric(18, 0), nullable=False)
    reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    reversed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), server_default=func.current_timestamp(), nullable=False
    )


class TuitionDiscount(Base):
    __tablename__ = "tuition_discounts"
    __table_args__ = (
        CheckConstraint("kind IN ('fixed', 'percent')", name="ck_tuition_discount_kind"),
        CheckConstraint("value > 0", name="ck_tuition_discount_value_positive"),
        CheckConstraint("amount_vnd > 0", name="ck_tuition_discount_amount_positive"),
        CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="ck_tuition_discount_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("tuition_plans.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    amount_vnd: Mapped[Decimal] = mapped_column(Numeric(18, 0), nullable=False)
    reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="approved", nullable=False)
    requested_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), server_default=func.current_timestamp(), nullable=False
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)


class TuitionReminderOutbox(Base):
    __tablename__ = "tuition_reminder_outbox"
    __table_args__ = (
        UniqueConstraint(
            "installment_id", "reminder_type", "reminder_date",
            name="uq_tuition_reminder_installment_type_date",
        ),
        CheckConstraint("reminder_type IN ('due_soon', 'overdue')", name="ck_tuition_reminder_type"),
        CheckConstraint("status IN ('pending', 'sent', 'failed')", name="ck_tuition_reminder_status"),
        CheckConstraint("attempt_count >= 0", name="ck_tuition_reminder_attempts"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    installment_id: Mapped[int] = mapped_column(
        ForeignKey("tuition_installments.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    notification_id: Mapped[int | None] = mapped_column(
        ForeignKey("notifications.id", ondelete="SET NULL"), nullable=True
    )
    reminder_type: Mapped[str] = mapped_column(String(16), nullable=False)
    reminder_date: Mapped[date] = mapped_column(Date, nullable=False)
    recipient_email: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), server_default=func.current_timestamp(), nullable=False
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
