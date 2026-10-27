"""Explicitly invoked daily tuition reminder job and scheduler."""

import argparse
import asyncio
from datetime import date, datetime, time, timedelta
import inspect
import logging
import time as time_module

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.notification import Notification
from app.models.tuition import (
    TuitionDiscount,
    TuitionInstallment,
    TuitionPayment,
    TuitionPaymentReversal,
    TuitionPlan,
    TuitionReminderOutbox,
)
from app.models.training_class import ClassEnrollment, TrainingClass
from app.models.user import User
from app.services.attendance_summary import HO_CHI_MINH
from app.services.email_service import send_tuition_due_reminder_email

logger = logging.getLogger(__name__)
ICTU_TZ = HO_CHI_MINH


def _remaining_balance(db: Session, installment: TuitionInstallment) -> int:
    paid = db.scalar(
        select(func.coalesce(func.sum(TuitionPayment.amount_vnd), 0)).where(
            TuitionPayment.installment_id == installment.id
        )
    ) or 0
    reversed_amount = db.scalar(
        select(func.coalesce(func.sum(TuitionPaymentReversal.amount_vnd), 0))
        .join(TuitionPayment, TuitionPayment.id == TuitionPaymentReversal.payment_id)
        .where(TuitionPayment.installment_id == installment.id)
    ) or 0
    gross_remaining = max(0, int(installment.amount_due_vnd - paid + reversed_amount))
    discount_total = db.scalar(
        select(func.coalesce(func.sum(TuitionDiscount.amount_vnd), 0)).where(
            TuitionDiscount.plan_id == installment.plan_id,
            TuitionDiscount.status == "approved",
        )
    ) or 0
    later = db.scalars(
        select(TuitionInstallment).where(
            TuitionInstallment.plan_id == installment.plan_id,
            TuitionInstallment.installment_number > installment.installment_number,
        )
    ).all()
    later_open = 0
    for row in later:
        row_paid = db.scalar(
            select(func.coalesce(func.sum(TuitionPayment.amount_vnd), 0)).where(
                TuitionPayment.installment_id == row.id
            )
        ) or 0
        row_reversed = db.scalar(
            select(func.coalesce(func.sum(TuitionPaymentReversal.amount_vnd), 0))
            .join(TuitionPayment, TuitionPayment.id == TuitionPaymentReversal.payment_id)
            .where(TuitionPayment.installment_id == row.id)
        ) or 0
        later_open += max(0, int(row.amount_due_vnd - row_paid + row_reversed))
    allocated = min(gross_remaining, max(0, int(discount_total) - later_open))
    return gross_remaining - allocated


def _invoke_email(
    sender,
    row: TuitionReminderOutbox,
    installment: TuitionInstallment,
    training_class: TrainingClass,
    student: User,
    remaining: int,
    overdue: bool,
) -> None:
    result = sender(
        row.recipient_email,
        student_name=student.full_name,
        class_code=training_class.code,
        installment_number=installment.installment_number,
        due_date=installment.due_date.isoformat(),
        remaining_vnd=str(remaining),
        overdue=overdue,
    )
    if inspect.isawaitable(result):
        asyncio.run(result)



def process_tuition_reminders(
    db: Session,
    *,
    today: date | None = None,
    email_sender=None,
    send_emails: bool | None = None,
    max_attempts: int | None = None,
) -> dict[str, int]:
    """Create due-soon/overdue notices and optionally deliver outbox emails.

    Email delivery is disabled by default. Tests can inject a fake sender and
    explicitly opt in without configuring or calling SMTP.
    """
    today = today or datetime.now(ICTU_TZ).date()
    attempts_limit = max(1, max_attempts or settings.TUITION_REMINDER_MAX_ATTEMPTS)
    if send_emails is None:
        send_emails = bool(settings.TUITION_REMINDER_EMAIL_ENABLED)
    sender = email_sender or send_tuition_due_reminder_email
    now = datetime.now(ICTU_TZ).replace(tzinfo=None)
    report = {"created": 0, "sent": 0, "failed": 0, "retried": 0}

    records = db.execute(
        select(TuitionInstallment, TuitionPlan, ClassEnrollment, User, TrainingClass)
        .join(TuitionPlan, TuitionPlan.id == TuitionInstallment.plan_id)
        .join(ClassEnrollment, ClassEnrollment.id == TuitionPlan.enrollment_id)
        .join(User, User.id == ClassEnrollment.student_id)
        .join(TrainingClass, TrainingClass.id == ClassEnrollment.class_id)
        .where(TuitionInstallment.due_date <= today + timedelta(days=3))
        .order_by(TuitionInstallment.due_date, TuitionInstallment.id)
    ).all()
    for installment, plan, enrollment, student, training_class in records:
        remaining = _remaining_balance(db, installment)
        if remaining <= 0:
            continue
        if installment.due_date == today + timedelta(days=3):
            reminder_type = "due_soon"
            title = "Hạn thanh toán học phí sau 3 ngày"
        elif installment.due_date < today:
            reminder_type = "overdue"
            title = "Nhắc học phí quá hạn"
        else:
            continue
        existing = db.scalar(
            select(TuitionReminderOutbox).where(
                TuitionReminderOutbox.installment_id == installment.id,
                TuitionReminderOutbox.reminder_type == reminder_type,
                TuitionReminderOutbox.reminder_date == today,
            ).with_for_update()
        )
        if existing is None:
            notification = Notification(
                user_id=student.id,
                class_id=training_class.id,
                title=title,
                message=(
                    f"Lớp {training_class.code}; đợt {installment.installment_number}; "
                    f"hạn {installment.due_date.isoformat()}; remaining_vnd={remaining}; "
                    f"installment_id={installment.id}"
                ),
                scheduled_for=now,
            )
            db.add(notification)
            db.flush()
            existing = TuitionReminderOutbox(
                installment_id=installment.id,
                student_id=student.id,
                notification_id=notification.id,
                reminder_type=reminder_type,
                reminder_date=today,
                recipient_email=student.email,
                status="pending",
                attempt_count=0,
            )
            db.add(existing)
            try:
                db.commit()
                report["created"] += 1
            except IntegrityError:
                db.rollback()
                continue
        if not send_emails or existing.status == "sent" or existing.attempt_count >= attempts_limit:
            continue
        if existing.attempt_count:
            report["retried"] += 1
        try:
            _invoke_email(
                sender,
                existing,
                installment,
                training_class,
                student,
                remaining,
                reminder_type == "overdue",
            )
            existing.attempt_count += 1
            existing.status = "sent"
            existing.last_error = None
            existing.sent_at = now
            report["sent"] += 1
        except Exception as exc:   # keep failed delivery in the durable outbox
            existing.attempt_count += 1
            existing.status = "failed"
            existing.last_error = str(exc)[:500]
            report["failed"] += 1
            logger.warning("Tuition reminder delivery failed for outbox id %s", existing.id)
        db.commit()
    return report


def run_once(send_emails: bool | None = None) -> dict[str, int]:
    db = SessionLocal()
    try:
        return process_tuition_reminders(db, send_emails=send_emails)
    finally:
        db.close()


def run_daily_scheduler() -> None:
    if not settings.ENABLE_TUITION_REMINDER_WORKER:
        raise RuntimeError("Set ENABLE_TUITION_REMINDER_WORKER=true to start the scheduler.")
    while True:
        run_once()
        now = datetime.now(ICTU_TZ)
        tomorrow = now.date() + timedelta(days=1)
        next_run = datetime.combine(tomorrow, time.min, tzinfo=ICTU_TZ)
        time_module.sleep(max(1, (next_run - now).total_seconds()))


def main() -> None:
    parser = argparse.ArgumentParser(description="ICTU tuition reminder job")
    parser.add_argument("--loop", action="store_true", help="run daily; requires explicit worker enablement")
    args = parser.parse_args()
    if args.loop:
        run_daily_scheduler()
    else:
        print(run_once())


if __name__ == "__main__":
    main()
