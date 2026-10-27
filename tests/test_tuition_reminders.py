from datetime import date, time

from app.core.config import settings
from app.core.security import hash_password
from app.models.course import Course
from app.models.notification import Notification
from app.models.role import Role
from app.models.study_program import ProgramFeeSchedule
from app.models.tuition import (
    TuitionInstallment,
    TuitionPlan,
    TuitionReminderOutbox,
)
from app.models.training_class import ClassEnrollment, TrainingClass
from app.models.user import User
from app.services.tuition_reminders import process_tuition_reminders, run_daily_scheduler


def test_daily_tuition_reminders_dedupe_and_retry_with_fake_email(test_db):
    roles = [Role(code=code, name=code.title()) for code in ("student", "teacher")]
    test_db.add_all(roles)
    test_db.flush()
    student = User(
        username="reminder_student",
        email="reminder.student@example.edu.vn",
        full_name="Reminder Student",
        hashed_password=hash_password("CorrectPassword123!"),
        roles=[roles[0]],
    )
    teacher = User(
        username="reminder_teacher",
        email="reminder.teacher@example.edu.vn",
        full_name="Reminder Teacher",
        hashed_password=hash_password("CorrectPassword123!"),
        roles=[roles[1]],
    )
    course = Course(code="REM101", name="Reminder course", department="IT", credits=3)
    test_db.add_all([student, teacher, course])
    test_db.flush()
    training_class = TrainingClass(
        code="REM101-01",
        name="Reminder class",
        course_id=course.id,
        teacher_id=teacher.id,
        room="R1",
        term="2026-Fall",
        day_of_week=0,
        start_time=time(9),
        end_time=time(11),
    )
    test_db.add(training_class)
    test_db.flush()
    enrollment = ClassEnrollment(class_id=training_class.id, student_id=student.id)
    test_db.add(enrollment)
    test_db.flush()
    schedule = ProgramFeeSchedule(
        program_id=1,
        version=1,
        amount_vnd=2000,
        valid_from=date(2026, 1, 1),
    )
    # Create the referenced program without depending on the configured seed.
    from app.models.study_program import StudyProgram

    program = StudyProgram(code="REM-PROG", name="Reminder program", total_credits=3)
    test_db.add(program)
    test_db.flush()
    schedule.program_id = program.id
    test_db.add(schedule)
    test_db.flush()
    plan = TuitionPlan(
        enrollment_id=enrollment.id,
        fee_schedule_id=schedule.id,
        total_due_vnd=2000,
        applied_on=date(2026, 1, 1),
    )
    test_db.add(plan)
    test_db.flush()
    test_db.add_all([
        TuitionInstallment(
            plan_id=plan.id,
            installment_number=1,
            amount_due_vnd=1000,
            due_date=date(2026, 10, 29),
        ),
        TuitionInstallment(
            plan_id=plan.id,
            installment_number=2,
            amount_due_vnd=1000,
            due_date=date(2026, 10, 24),
        ),
    ])
    test_db.commit()

    attempts = {1: 0, 2: 0}

    def fake_email(email, *, installment_number, **kwargs):
        attempts[installment_number] += 1
        if installment_number == 1 and attempts[installment_number] == 1:
            raise RuntimeError("temporary mail error")
        assert email == student.email
        assert kwargs["remaining_vnd"] == "1000"

    first_run = process_tuition_reminders(
        test_db,
        today=date(2026, 10, 26),
        email_sender=fake_email,
        send_emails=True,
        max_attempts=3,
    )
    assert first_run == {"created": 2, "sent": 1, "failed": 1, "retried": 0}
    assert test_db.query(Notification).count() == 2
    due_soon = test_db.query(TuitionReminderOutbox).filter_by(reminder_type="due_soon").one()
    overdue = test_db.query(TuitionReminderOutbox).filter_by(reminder_type="overdue").one()
    assert due_soon.status == "failed"
    assert due_soon.attempt_count == 1
    assert due_soon.last_error == "temporary mail error"
    assert overdue.status == "sent"
    assert overdue.sent_at is not None

    retry_run = process_tuition_reminders(
        test_db,
        today=date(2026, 10, 26),
        email_sender=fake_email,
        send_emails=True,
        max_attempts=3,
    )
    assert retry_run == {"created": 0, "sent": 1, "failed": 0, "retried": 1}
    assert test_db.query(Notification).count() == 2
    assert due_soon.status == "sent"
    assert due_soon.attempt_count == 2

    repeated_run = process_tuition_reminders(
        test_db,
        today=date(2026, 10, 26),
        email_sender=fake_email,
        send_emails=True,
        max_attempts=3,
    )
    assert repeated_run == {"created": 0, "sent": 0, "failed": 0, "retried": 0}
    assert attempts == {1: 2, 2: 1}

    failure_calls = []

    def always_failing_email(email, **kwargs):
        failure_calls.append(email)
        raise RuntimeError("mail unavailable")

    exhausted_first = process_tuition_reminders(
        test_db,
        today=date(2026, 10, 27),
        email_sender=always_failing_email,
        send_emails=True,
        max_attempts=1,
    )
    assert exhausted_first == {"created": 1, "sent": 0, "failed": 1, "retried": 0}
    exhausted_second = process_tuition_reminders(
        test_db,
        today=date(2026, 10, 27),
        email_sender=always_failing_email,
        send_emails=True,
        max_attempts=1,
    )
    assert exhausted_second == {"created": 0, "sent": 0, "failed": 0, "retried": 0}
    assert len(failure_calls) == 1


def test_reminder_scheduler_requires_explicit_enablement(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_TUITION_REMINDER_WORKER", False)
    try:
        run_daily_scheduler()
    except RuntimeError as exc:
        assert "ENABLE_TUITION_REMINDER_WORKER" in str(exc)
    else:
        raise AssertionError("scheduler must not run unless explicitly enabled")
