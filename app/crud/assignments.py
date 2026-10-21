from datetime import timedelta
from fastapi import HTTPException
from sqlalchemy import and_, or_
from app.core.permissions import user_has_permission
from app.crud import attendance as clock
from app.crud.leads import user_roles
from app.crud.student_enrollments import active_on
from app.models.student_enrollment import StudentEnrollment
from app.models.assignment import Assignment, AssignmentAttachment, AssignmentRecipient
from app.models.attendance import ClassSession, StudentClass
from app.models.subject import ClassSubject, Subject
from app.models.training_program import TrainingClass
from app.models.user import User
from app.models.user_role import UserRole
from app.models.role import Role

TEACHERS = {"INSTRUCTOR", "TEACHER"}
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 25 * 1024 * 1024
MAX_FILES = 5


def can_create(db, user):
    return bool(user_roles(db, user) & TEACHERS) and user_has_permission(user.user_id, "ASSIGNMENT_MANAGE", db)


def teaching_sessions(db, user):
    return db.query(ClassSession).join(ClassSubject, ClassSubject.id == ClassSession.class_subject_id).filter(or_(
        ClassSession.substitute_instructor_id == user.user_id,
        and_(ClassSession.substitute_instructor_id.is_(None), ClassSubject.instructor_id == user.user_id)))


def target(db, user, subject_id, session_id, lock=False):
    session = teaching_sessions(db, user).filter(ClassSession.session_id == session_id,
                                               ClassSession.class_subject_id == subject_id).first()
    if session is None:
        raise HTTPException(404, "Không tìm thấy buổi học thuộc môn bạn được phân công.")
    subject = db.get(ClassSubject, subject_id)
    query = db.query(TrainingClass).filter_by(class_id=subject.class_id)
    classroom = query.populate_existing().with_for_update().one() if lock else query.one()
    if lock:
        db.query(ClassSubject).filter_by(id=subject_id).populate_existing().with_for_update().one()
        session = teaching_sessions(db, user).filter(ClassSession.session_id == session_id,
            ClassSession.class_subject_id == subject_id).populate_existing().with_for_update().first()
        if session is None:
            raise HTTPException(409, "Phân công đã thay đổi. Vui lòng tải lại.")
    if session.status == "cancelled" or classroom.status not in {"planned", "in_progress"}:
        raise HTTPException(409, "Không thể giao hoặc sửa bài của buổi đã huỷ hay lớp đã kết thúc/huỷ.")
    return classroom


def scoped(db, user):
    conditions = []
    if can_create(db, user):
        conditions.append(and_(Assignment.author_id == user.user_id,
            Assignment.session_id.in_(teaching_sessions(db, user).with_entities(ClassSession.session_id))))
    if "STUDENT" in user_roles(db, user):
        conditions.append(and_(Assignment.status == "published", Assignment.assignment_id.in_(
            db.query(AssignmentRecipient.assignment_id).filter_by(student_id=user.user_id))))
    return db.query(Assignment).filter(or_(*conditions) if conditions else False)


def find(db, user, id):
    row = scoped(db, user).filter(Assignment.assignment_id == id).first()
    if row is None:
        raise HTTPException(404, "Không tìm thấy bài tập hoặc bạn không có quyền xem.")
    return row


def require_future(value):
    if value <= clock.utcnow():
        raise HTTPException(422, "Hạn nộp phải ở tương lai (giờ Việt Nam).")


def claim_draft(db, user, id, version):
    row = find(db, user, id)
    if row.author_id != user.user_id or not can_create(db, user):
        raise HTTPException(403, "Chỉ tác giả là giảng viên được phân công mới được sửa bài.")
    target(db, user, row.class_subject_id, row.session_id, lock=True)
    changed = db.query(Assignment).filter_by(assignment_id=id, status="draft", version=version).update(
        {"version": version + 1, "updated_at": clock.utcnow()}, synchronize_session=False)
    if not changed:
        raise HTTPException(409, "Bài đã được giao hoặc bản nháp đã thay đổi. Vui lòng tải lại.")
    db.expire(row)
    return row


def recipient_ids(db, class_id):
    day = clock.today()
    # Locking reads use the latest committed membership even when a MySQL
    # REPEATABLE READ snapshot was established before waiting for the class lock.
    periods = db.query(StudentEnrollment).filter_by(class_id=class_id).populate_existing().with_for_update().all()
    managed = {row.student_id for row in periods}
    active = {row.student_id for row in periods if active_on(row, day)}
    legacy = {id for id, in db.query(StudentClass.student_id).filter(
        StudentClass.class_id == class_id,
        or_(StudentClass.enrolled_date.is_(None), StudentClass.enrolled_date <= day)).with_for_update()}
    candidates = (legacy - managed) | active
    return {id for id, in db.query(User.user_id).join(UserRole, UserRole.user_id == User.user_id).join(
        Role, Role.role_id == UserRole.role_id).filter(User.user_id.in_(candidates), Role.role_name == "STUDENT").with_for_update()}


def detail(db, row, user):
    subject = db.get(ClassSubject, row.class_subject_id)
    classroom = db.get(TrainingClass, subject.class_id)
    course = db.get(Subject, subject.subject_id)
    session = db.get(ClassSession, row.session_id)
    return {
        "assignment_id": row.assignment_id, "title": row.title, "description": row.description,
        "class_subject_id": row.class_subject_id, "session_id": row.session_id,
        "class_id": classroom.class_id, "class_name": classroom.class_name, "subject_name": course.subject_name,
        "session_date": session.session_date, "session_time": session.start_time.strftime("%H:%M"), "topic": session.topic,
        "due_at": clock.iso(row.due_at), "max_points": str(row.max_points), "status": row.status,
        "version": row.version, "created_at": clock.iso(row.created_at), "published_at": clock.iso(row.published_at),
        "can_edit": row.status == "draft" and row.author_id == user.user_id and can_create(db, user),
        "recipient_count": db.query(AssignmentRecipient).filter_by(assignment_id=row.assignment_id).count(),
        "attachments": [{"attachment_id": file.attachment_id, "filename": file.filename, "size": file.size}
                        for file in db.query(AssignmentAttachment).filter_by(assignment_id=row.assignment_id)
                        .order_by(AssignmentAttachment.attachment_id)],
    }


def publish(db, user, id, version):
    row = find(db, user, id)
    if row.author_id != user.user_id or not can_create(db, user):
        raise HTTPException(403, "Chỉ tác giả mới được giao bài.")
    if row.status == "published":
        return row  # A retry after a successful commit never duplicates delivery.
    row = claim_draft(db, user, id, version)
    require_future(row.due_at)  # A draft may have expired since it was saved.
    classroom = target(db, user, row.class_subject_id, row.session_id)
    ids = recipient_ids(db, classroom.class_id)
    if not ids:
        raise HTTPException(409, "Lớp chưa có học viên đang học để nhận bài tập.")
    now = clock.utcnow()
    row.status, row.published_at = "published", now
    deadline = (row.due_at + timedelta(hours=7)).strftime("%H:%M %d/%m/%Y")
    db.add_all([AssignmentRecipient(assignment_id=id, student_id=student_id,
        title=f"Bài tập mới: {row.title}", message=f"Lớp {classroom.class_name}\n{row.title}\nHạn nộp: {deadline} (giờ Việt Nam)\nXem bài tại /assignments?assignment_id={id}",
        created_at=now) for student_id in sorted(ids)])
    db.commit()
    return row
