"""Serialize reconciliation separately from domain writes; notify on rising edges."""
from collections import defaultdict
from decimal import Decimal
import logging
from sqlalchemy import text
from sqlalchemy.orm import load_only
from app.crud import attendance as clock
from app.models.attendance_risk import AttendanceRisk, AttendanceRiskNotification, ProgramAbsencePolicy
from app.models.attendance import ClassSession, SessionAttendance, StudentClass
from app.models.student_enrollment import StudentEnrollment
from app.models.subject import ClassSubject
from app.models.training_program import TrainingClass
from app.models.user import User
from app.models.role import Role
from app.models.user_role import UserRole

DEFAULT_THRESHOLD = Decimal("20")
logger = logging.getLogger(__name__)


def begin_sync(db):
    # Call only outside domain mutations. Start a fresh MySQL read snapshot after
    # taking this mutex, so parallel workers cannot send duplicate activations.
    if db.new or db.dirty or db.deleted:
        raise RuntimeError("Risk reconciliation must follow a completed domain transaction")
    db.rollback()
    sql = ("INSERT INTO attendance_risk_sync (id, revision) VALUES (1, 1) ON DUPLICATE KEY UPDATE revision=revision+1"
           if db.bind.dialect.name == "mysql" else
           "INSERT INTO attendance_risk_sync (id, revision) VALUES (1, 1) ON CONFLICT(id) DO UPDATE SET revision=revision+1")
    db.execute(text(sql))


def evaluate(db):
    day, now = clock.today(), clock.utcnow()
    policies = {row.course_id: row.threshold_percent for row in db.query(ProgramAbsencePolicy)}
    classes = {row.class_id: row for row in db.query(TrainingClass).filter(TrainingClass.status.in_(["planned", "in_progress"]))
               if row.start_date is None or row.start_date <= day}
    periods = defaultdict(list)
    for row in db.query(StudentEnrollment).filter(StudentEnrollment.class_id.in_(classes)):
        periods[(row.class_id, row.student_id)].append(row)
    legacy = {(row.class_id, row.student_id): row.enrolled_date for row in db.query(StudentClass).filter(StudentClass.class_id.in_(classes))}

    def enrolled(key, on_date):
        if key in periods:
            return any((row.started_on is None or row.started_on <= on_date) and
                       (row.ended_on is None or on_date < row.ended_on) for row in periods[key])
        return key in legacy and (legacy[key] is None or legacy[key] <= on_date)

    members = {key for key in set(periods) | set(legacy) if enrolled(key, day)}
    people = {row.user_id: row for row in db.query(User).options(load_only(User.user_id, User.full_name, User.username))}
    totals = defaultdict(int)
    sessions = {}
    teachers = defaultdict(set)
    for assignment in db.query(ClassSubject).filter(ClassSubject.class_id.in_(classes)):
        if assignment.instructor_id:
            teachers[assignment.class_id].add(assignment.instructor_id)
    for session, class_id in db.query(ClassSession, ClassSubject.class_id).join(ClassSubject, ClassSubject.id == ClassSession.class_subject_id).filter(
            ClassSubject.class_id.in_(classes), ClassSession.status != "cancelled"):
        totals[class_id] += 1
        sessions[session.session_id] = (class_id, session.session_date)
        if session.substitute_instructor_id:
            teachers[class_id].add(session.substitute_instructor_id)
    absences = defaultdict(int)
    for record in db.query(SessionAttendance).filter(SessionAttendance.session_id.in_(sessions),
            SessionAttendance.status.in_(["excused_absence", "unexcused_absence"])):
        class_id, session_day = sessions[record.session_id]
        key = (class_id, record.student_id)
        if key in members and session_day <= day and enrolled(key, session_day):
            absences[key] += 1
    roles = defaultdict(set)
    for user_id, name in db.query(UserRole.user_id, Role.role_name).join(Role, Role.role_id == UserRole.role_id):
        roles[user_id].add(name)
    # Match the application's authoritative multi-role assignments. A stale
    # legacy users.role_id must not restore a role that has been revoked.
    managers = {id for id, names in roles.items() if names & {"TRAINING_MANAGER", "MANAGER"} and id in people}
    existing = {(row.class_id, row.student_id): row for row in db.query(AttendanceRisk).populate_existing()}
    for key in members | set(existing):
        class_id, student_id = key
        classroom, person = classes.get(class_id), people.get(student_id)
        threshold = policies.get(classroom.course_id, DEFAULT_THRESHOLD) if classroom else DEFAULT_THRESHOLD
        count, total = absences[key], totals[class_id]
        active = bool(key in members and person and classroom and total and Decimal(count * 100) > Decimal(total) * threshold)
        row = existing.get(key)
        if row is None:
            if not active:
                continue
            row = AttendanceRisk(class_id=class_id, student_id=student_id, active=False, activation=0)
            db.add(row)
        if active and not row.active:
            row.activation += 1
            row.activated_at = now
            recipients = managers | {id for id in teachers[class_id] if roles[id] & {"INSTRUCTOR", "TEACHER"} and id in people}
            for recipient in recipients:
                db.add(AttendanceRiskNotification(class_id=class_id, student_id=student_id, activation=row.activation,
                    recipient_id=recipient, title="Cảnh báo học viên vắng vượt ngưỡng",
                    message=f"{person.full_name} ({person.username}) · {classroom.class_name}\n"
                            f"Vắng {count}/{total} buổi đã xếp lịch ({count * 100 / total:.2f}%), vượt ngưỡng {threshold:g}%. "
                            "Tính cả vắng có phép và không phép, bỏ buổi huỷ. Vui lòng liên hệ học viên để hỗ trợ.", created_at=now))
        row.active, row.absent_sessions, row.scheduled_sessions = active, count, total
        row.threshold_percent, row.updated_at = threshold, now


def refresh(db):
    try:
        begin_sync(db)
        evaluate(db)
        db.commit()
    except Exception:
        db.rollback()
        raise


def refresh_after_save(db):
    # Domain data is already saved. A temporary notification failure is retried
    # by the periodic worker / next read, rather than reporting a false save failure.
    try:
        refresh(db)
    except Exception:
        logger.exception("Attendance risk refresh failed; pending periodic retry")


def active_flags(db, *, class_ids=None, student_ids=None):
    query = db.query(AttendanceRisk, TrainingClass.class_name, User.full_name, User.username).join(
        TrainingClass, TrainingClass.class_id == AttendanceRisk.class_id).join(User, User.user_id == AttendanceRisk.student_id).filter(AttendanceRisk.active.is_(True))
    if class_ids is not None:
        query = query.filter(AttendanceRisk.class_id.in_(class_ids))
    if student_ids is not None:
        query = query.filter(AttendanceRisk.student_id.in_(student_ids))
    return [{"class_id": row.class_id, "class_name": name, "student_id": row.student_id,
             "student_name": student_name, "student_code": code, "absent_sessions": row.absent_sessions,
             "scheduled_sessions": row.scheduled_sessions, "threshold_percent": float(row.threshold_percent),
             "absence_rate": round(row.absent_sessions * 100 / row.scheduled_sessions, 2),
             "activated_at": clock.iso(row.activated_at)} for row, name, student_name, code in query.order_by(AttendanceRisk.class_id, User.full_name)]
