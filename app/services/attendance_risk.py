"""Persist attendance risk transitions and atomically queue staff notifications."""
from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.attendance import AttendanceRecord, AttendanceRiskState, AttendanceSession
from app.models.notification import Notification, NotificationOutbox
from app.models.role import Role
from app.models.study_program import StudyProgram
from app.models.training_class import ClassEnrollment, TrainingClass, class_assistants
from app.models.user import User
from app.services.attendance_summary import summarize_class_attendance

MANAGER_ROLES = ("admin", "manager")


def attendance_warning(absent_count: int, total_sessions: int, threshold_percent: float) -> dict[str, int | str | None]:
    """Classify absence against the program threshold using S5-02 eligible sessions."""
    if total_sessions <= 0:
        return {"warning_status": "insufficient_data", "allowed_absences": None,
                "remaining_allowed_absences": None}
    allowed = int((Decimal(str(threshold_percent)) * total_sessions / Decimal(100)).to_integral_value(rounding=ROUND_FLOOR))
    if absent_count <= 0:
        state = "normal"
    elif allowed <= 0:
        state = "over_threshold"
    elif absent_count > allowed:
        state = "over_threshold"
    elif absent_count == allowed:
        state = "at_threshold"
    elif allowed - absent_count == 1:
        state = "near_threshold"
    else:
        state = "normal"
    return {"warning_status": state, "allowed_absences": allowed,
            "remaining_allowed_absences": max(0, allowed - absent_count)}


def refresh_attendance_risk_states(db: Session, training_class: TrainingClass) -> None:
    sessions = db.scalars(select(AttendanceSession).where(AttendanceSession.class_id == training_class.id)).all()
    enrollments = db.scalars(select(ClassEnrollment).options(joinedload(ClassEnrollment.student)).where(
        ClassEnrollment.class_id == training_class.id
    )).all()
    session_ids = [session.id for session in sessions]
    records = db.scalars(select(AttendanceRecord).where(AttendanceRecord.session_id.in_(session_ids))).all() if session_ids else []
    summary = summarize_class_attendance(enrollments, sessions, records, datetime.now(timezone.utc))
    program = db.get(StudyProgram, training_class.program_id) if training_class.program_id else None
    threshold = float(program.absence_threshold_percent) if program is not None else 20.0
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    assistant_ids = set(db.scalars(select(class_assistants.c.assistant_id).where(
        class_assistants.c.class_id == training_class.id
    )).all())
    manager_ids = set(db.scalars(select(User.id).join(User.roles).where(
        Role.code.in_(MANAGER_ROLES), User.is_active.is_(True), User.is_locked.is_(False)
    )).all())
    recipient_ids = manager_ids | assistant_ids | {training_class.teacher_id}
    recipients = db.scalars(select(User).where(
        User.id.in_(recipient_ids), User.is_active.is_(True), User.is_locked.is_(False)
    )).all() if recipient_ids else []

    for student_summary in summary.students:
        warning = attendance_warning(student_summary.absent_count, student_summary.total_sessions, threshold)
        is_at_risk = warning["warning_status"] in ("at_threshold", "over_threshold")
        state = db.scalar(select(AttendanceRiskState).where(
            AttendanceRiskState.class_id == training_class.id,
            AttendanceRiskState.student_id == student_summary.student_id,
        ).with_for_update())
        if state is None:
            state = AttendanceRiskState(class_id=training_class.id, student_id=student_summary.student_id,
                is_at_risk=False, absence_threshold_percent=threshold)
            db.add(state)
            db.flush()
        activated = is_at_risk and not state.is_at_risk
        cleared = not is_at_risk and state.is_at_risk
        if activated:
            state.activation_count += 1
            state.flagged_at = now
            state.cleared_at = None
        elif cleared:
            state.cleared_at = now
        state.is_at_risk = is_at_risk
        state.absent_count = student_summary.absent_count
        state.completed_session_count = student_summary.total_sessions
        state.absence_threshold_percent = threshold
        state.last_evaluated_at = now
        if not activated:
            continue

        student = next(enrollment.student for enrollment in enrollments
                       if enrollment.student_id == student_summary.student_id)
        message = (
            f"{student.full_name} ({training_class.code}) vượt ngưỡng vắng học: "
            f"{student_summary.absent_count}/{student_summary.total_sessions} buổi; "
            f"ngưỡng {threshold:.2f}%."
        )
        for recipient in recipients:
            notification = Notification(
                user_id=recipient.id, class_id=training_class.id,
                title="Cảnh báo nguy cơ nghỉ học", message=message,
                navigation_url=f"/classes/{training_class.id}/attendance/summary?student_id={student.id}",
            )
            db.add(notification)
            db.flush()
            db.add(NotificationOutbox(
                notification_id=notification.id,
                dedupe_key=f"attendance-risk:{training_class.id}:{student.id}:{state.activation_count}:{recipient.id}",
                recipient_email=recipient.email, status="pending", attempt_count=0,
            ))
