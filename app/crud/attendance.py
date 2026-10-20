from app.room_catalog import room_info
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException
from sqlalchemy import or_, and_
from app.core.permissions import user_has_permission
from app.crud.leads import user_roles
from app.models.attendance import ClassSession, StudentClass, SessionAttendance
from app.models.subject import ClassSubject, Subject
from app.models.training_program import TrainingClass
from app.models.user import User

MANAGERS = {"TRAINING_MANAGER", "MANAGER"}
LECTURERS = {"INSTRUCTOR", "TEACHER"}


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def today():
    return (utcnow() + timedelta(hours=7)).date()


def iso(value):
    return value.replace(tzinfo=timezone.utc).isoformat() if value else None


def is_manager(db, user):
    return bool(user_roles(db, user) & MANAGERS)


def can_prepare(db, user):
    return bool(user_roles(db, user) & (MANAGERS | {"ADMIN"})) and user_has_permission(user.user_id, "ATTENDANCE_EDIT", db)


def subject_scope(db, user):
    query = db.query(ClassSubject)
    if not user_roles(db, user) & (MANAGERS | {"ADMIN"}):
        query = query.filter(or_(ClassSubject.instructor_id == user.user_id, ClassSubject.id.in_(
            db.query(ClassSession.class_subject_id).filter_by(substitute_instructor_id=user.user_id))))
    return query


def sessions_query(db, user):
    query = db.query(ClassSession)
    if not user_roles(db, user) & (MANAGERS | {"ADMIN"}):
        assigned = db.query(ClassSubject.id).filter_by(instructor_id=user.user_id)
        query = query.filter(or_(ClassSession.substitute_instructor_id == user.user_id,
            and_(ClassSession.substitute_instructor_id.is_(None), ClassSession.class_subject_id.in_(assigned))))
    return query


def find_session(db, user, session_id, lock=False):
    session = sessions_query(db, user).filter(ClassSession.session_id == session_id).first()
    if session is None:
        raise HTTPException(404, "Không tìm thấy buổi học hoặc bạn không được phân công.")
    if lock:
        subject = db.get(ClassSubject, session.class_subject_id)
        # Same lock order as teacher assignment: class -> subject -> session.
        db.query(TrainingClass).filter_by(class_id=subject.class_id).populate_existing().with_for_update().one()
        db.query(ClassSubject).filter_by(id=subject.id).populate_existing().with_for_update().one()
        session = sessions_query(db, user).filter(ClassSession.session_id == session_id).populate_existing().with_for_update().first()
        if session is None:
            raise HTTPException(404, "Phân công giảng dạy đã thay đổi. Vui lòng tải lại.")
    return session


def edit_policy(db, user, session):
    if not user_has_permission(user.user_id, "ATTENDANCE_EDIT", db):
        return False, "Bạn không có quyền cập nhật điểm danh."
    subject = db.get(ClassSubject, session.class_subject_id)
    classroom = db.get(TrainingClass, subject.class_id)
    if session.status == "cancelled":
        return False, "Buổi đã hủy, không thể điểm danh."
    if classroom.status == "cancelled":
        return False, "Lớp đã hủy, không thể điểm danh."
    if session.session_date > today():
        return False, "Chưa đến ngày học. Không thể điểm danh buổi tương lai."
    if is_manager(db, user):
        return True, None
    if session.session_date < today():
        return False, "Chỉ Quản lý đào tạo được sửa điểm danh buổi quá khứ."
    if (session.substitute_instructor_id or subject.instructor_id) != user.user_id or not user_roles(db, user) & LECTURERS:
        return False, "Chỉ giảng viên được phân công hoặc Quản lý đào tạo được điểm danh."
    return True, None


def roster(db, session, lock=False):
    from app.crud.student_enrollments import attendance_membership
    subject = db.get(ClassSubject, session.class_subject_id)
    managed, active, incoming = attendance_membership(db, subject.class_id, session.session_date)
    records_query = db.query(SessionAttendance).filter_by(session_id=session.session_id).populate_existing()
    if lock:
        records_query = records_query.with_for_update()
    records = {row.student_id: row for row in records_query}
    if session.attendance_version and not session.absence_prefilled:
        # Keep snapshots, but apply formal decisions by session date. Incoming transfers
        # must also appear in a destination session that has already been saved.
        ids = (set(records) - (managed - active)) | incoming
    else:
        legacy = {row[0] for row in db.query(StudentClass.student_id).filter(
            StudentClass.class_id == subject.class_id,
            (StudentClass.enrolled_date.is_(None)) | (StudentClass.enrolled_date <= session.session_date))}
        ids = (legacy - managed) | active
    users = {user.user_id: user for user in db.query(User).filter(User.user_id.in_(ids - set(records)))}
    from app.models.absence_request import AbsenceRequest
    approved_query = db.query(AbsenceRequest.student_id).filter_by(session_id=session.session_id, status='approved')
    if lock:
        approved_query = approved_query.with_for_update()
    approved = {row[0] for row in approved_query}
    result = []
    for student_id in ids:
        record, user = records.get(student_id), users.get(student_id)
        if record:
            result.append({"student_id": student_id, "student_name": record.student_name,
                           "student_code": record.student_code, "status": record.status, "approved_absence": student_id in approved})
        elif user:
            result.append({"student_id": student_id, "student_name": user.full_name,
                           "student_code": user.username, "status": None, "approved_absence": student_id in approved})
    return sorted(result, key=lambda row: (row["student_name"], row["student_id"]))


def session_info(db, user, session):
    subject = db.get(ClassSubject, session.class_subject_id)
    classroom = db.get(TrainingClass, subject.class_id)
    course = db.get(Subject, subject.subject_id)
    allowed, reason = edit_policy(db, user, session)
    return {"session_id": session.session_id, "class_id": classroom.class_id, "class_name": classroom.class_name,
            "subject_name": course.subject_name, "session_date": session.session_date,
            "start_time": session.start_time.strftime("%H:%M"), "topic": session.topic,
            **room_info(db, session.room_id, session.room_name, classroom.class_id, session.session_date),
            "status": session.status, "cancellation_reason": session.cancellation_reason,
            "absence_prefilled": session.absence_prefilled, "version": session.attendance_version, "saved_at": iso(session.saved_at), "saved_by_name": session.saved_by_name,
            "can_edit": allowed, "read_only_reason": reason, "can_edit_past": is_manager(db, user),
            "today": today()}


def detail(db, user, session):
    students = roster(db, session)
    visible = {student["student_id"] for student in students}
    archived = [{"student_id": row.student_id, "student_name": row.student_name, "student_code": row.student_code,
                 "status": row.status} for row in db.query(SessionAttendance).filter_by(session_id=session.session_id)
                if row.student_id not in visible]
    return {**session_info(db, user, session), "students": students, "archived_students": archived}
