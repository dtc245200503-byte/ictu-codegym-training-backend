from app.crud.attendance_risk import refresh_after_save
from app.room_catalog import room_options, resolve_room, room_info
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.core.auth import get_current_user
from app.core.permissions import require_permission
from app.crud import attendance as crud
from app.crud.leads import user_roles
from app.database import get_db
from app.models.attendance import ClassSession, StudentClass, SessionAttendance, AttendanceHistory
from app.models.subject import ClassSubject, Subject
from app.models.training_program import TrainingClass
from app.models.user import User
from app.models.role import Role
from app.models.user_role import UserRole
from app.schemas.attendance import SaveAttendance, CreateClassSession, EnrollStudents
from app.schedule_conflicts import lock_schedule, session_snapshot, enforce_conflicts
from app.schemas.schedule_conflict import CONFIRMATION_FIELDS


def require_staff(user: User = Depends(require_permission("ATTENDANCE_VIEW")), db: Session = Depends(get_db)):
    if not user_roles(db, user) & (crud.MANAGERS | crud.LECTURERS | {"ADMIN"}):
        raise HTTPException(403, "Chức năng này dành cho giảng viên và quản lý đào tạo.")
    return user


router = APIRouter(prefix="/api/attendance", tags=["Điểm danh"], dependencies=[Depends(require_staff)])


def require_prepare(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not crud.can_prepare(db, user):
        raise HTTPException(403, "Chỉ quản lý có quyền cập nhật điểm danh mới được chuẩn bị buổi học.")
    return user


@router.get("/options")
def options(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = crud.subject_scope(db, user).join(TrainingClass, TrainingClass.class_id == ClassSubject.class_id).join(
        Subject, Subject.id == ClassSubject.subject_id).filter(TrainingClass.status != "cancelled").with_entities(
        ClassSubject.id, TrainingClass.class_id, TrainingClass.class_name, Subject.subject_name).order_by(
        TrainingClass.class_name, Subject.subject_name).all()
    return {"today": crud.today(), "can_prepare": crud.can_prepare(db, user), "can_enroll": crud.can_prepare(db, user) and crud.is_manager(db, user), "rooms": room_options(db) if crud.can_prepare(db, user) else [],
            "subjects": [{"class_subject_id": row[0], "class_id": row[1], "class_name": row[2], "subject_name": row[3]} for row in rows]}


@router.get("/sessions")
def list_sessions(on_date: date | None = None, class_id: int | None = Query(None, gt=0),
                  page: int = Query(1, ge=1), page_size: int = Query(30, ge=1, le=100),
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    selected_date = on_date or crud.today()
    query = crud.sessions_query(db, user).filter(ClassSession.session_date == selected_date)
    if class_id is not None:
        query = query.filter(ClassSession.class_subject_id.in_(db.query(ClassSubject.id).filter_by(class_id=class_id)))
    rows = query.order_by(ClassSession.start_time, ClassSession.session_id).offset((page-1)*page_size).limit(page_size).all()
    return {"items": [crud.session_info(db, user, row) for row in rows], "total": query.count(),
            "page": page, "page_size": page_size, "today": crud.today()}


@router.post("/sessions", status_code=201)
def create_session(data: CreateClassSession, user: User = Depends(require_prepare), db: Session = Depends(get_db)):
    lock_schedule(db)
    subject = db.query(ClassSubject).filter_by(id=data.class_subject_id).populate_existing().with_for_update().first()
    if subject is None:
        raise HTTPException(404, "Không tìm thấy môn học trong lớp.")
    classroom = db.query(TrainingClass).filter_by(class_id=subject.class_id).with_for_update().one()
    if classroom.status == "cancelled":
        raise HTTPException(400, "Không thể tạo buổi cho lớp đã hủy.")
    if classroom.start_date and data.session_date < classroom.start_date:
        raise HTTPException(400, "Ngày học không được trước ngày khai giảng.")
    room = resolve_room(db, data.room_id, data.room_name)
    values = data.model_dump(exclude=CONFIRMATION_FIELDS)
    if room:
        values.update(room_id=room.room_id, room_name=room.name)
    candidate = session_snapshot(db, subject, classroom, **{key: value for key, value in values.items() if key != "class_subject_id"})
    enforce_conflicts(db, [candidate], user, data, "create-session")
    session = ClassSession(**values)
    db.add(session)
    try:
        db.commit()
        refresh_after_save(db)
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Môn học đã có buổi vào ngày và giờ này.")
    db.refresh(session)
    return {**crud.session_info(db, user, session), **room_info(db, session.room_id, session.room_name, subject.class_id, session.session_date)}


@router.get("/students")
def student_candidates(search: str = Query("", max_length=100), user: User = Depends(require_prepare), db: Session = Depends(get_db)):
    query = db.query(User).join(UserRole, UserRole.user_id == User.user_id).join(Role, Role.role_id == UserRole.role_id).filter(
        Role.role_name == "STUDENT")
    if search.strip():
        term = search.strip().lower()
        query = query.filter(func.lower(User.full_name).contains(term, autoescape=True) |
                             func.lower(User.username).contains(term, autoescape=True))
    query = query.distinct().order_by(User.full_name, User.user_id)
    return {"items": [{"student_id": item.user_id, "student_name": item.full_name, "student_code": item.username}
                      for item in query.limit(50)], "total": query.count()}


@router.post("/classes/{class_id}/students")
def enroll(class_id: int, data: EnrollStudents, user: User = Depends(require_prepare), db: Session = Depends(get_db)):
    from app.crud.class_enrollments import enroll as enroll_batch
    return enroll_batch(db, class_id, data.student_ids, user)


@router.get("/sessions/{session_id}")
def get_session(session_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return crud.detail(db, user, crud.find_session(db, user, session_id))


@router.put("/sessions/{session_id}")
def save_attendance(session_id: int, data: SaveAttendance, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    session = crud.find_session(db, user, session_id, lock=True)
    allowed, reason = crud.edit_policy(db, user, session)
    if not allowed:
        raise HTTPException(403, reason)
    if session.attendance_version != data.expected_version:
        raise HTTPException(409, "Điểm danh đã được người khác cập nhật. Tải lại dữ liệu trước khi sửa.")
    students = crud.roster(db, session, lock=True)
    if {item.student_id for item in data.items} != {item["student_id"] for item in students}:
        raise HTTPException(409, "Danh sách học viên đã thay đổi hoặc chưa được chọn đủ. Vui lòng tải lại.")
    requested = {item.student_id: item.status for item in data.items}
    if any(student.get('approved_absence') and requested[student['student_id']] != 'excused_absence' for student in students):
        raise HTTPException(409, 'Có học viên đã được duyệt xin vắng. Giữ trạng thái Vắng có phép và tải lại danh sách.')
    changes = [student for student in students if student["status"] != requested[student["student_id"]]]
    if not changes and not session.absence_prefilled:
        return {**crud.detail(db, user, session), "changed": 0}
    version = session.attendance_version + 1
    now = crud.utcnow()
    try:
        changed = db.query(ClassSession).filter_by(session_id=session_id, attendance_version=data.expected_version,
            schedule_version=session.schedule_version, status="scheduled").update(
            {"attendance_version": version, "absence_prefilled": False, "saved_at": now, "saved_by_name": user.full_name}, synchronize_session=False)
        if changed != 1:
            raise HTTPException(409, "Điểm danh đã thay đổi. Vui lòng tải lại.")
        for student in changes:
            student_id = student["student_id"]
            record = db.get(SessionAttendance, (session_id, student_id))
            if record is None:
                record = SessionAttendance(session_id=session_id, student_id=student_id,
                    student_name=student["student_name"], student_code=student["student_code"])
                db.add(record)
            record.status = requested[student_id]
            db.add(AttendanceHistory(session_id=session_id, version=version, student_id=student_id,
                student_name=student["student_name"], from_status=student["status"], to_status=requested[student_id],
                actor_id=user.user_id, actor_name=user.full_name, created_at=now))
        db.commit()
        refresh_after_save(db)
    except Exception:
        db.rollback()
        raise
    db.refresh(session)
    return {**crud.detail(db, user, session), "changed": len(changes)}


@router.get("/sessions/{session_id}/history")
def history(session_id: int, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
            user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    crud.find_session(db, user, session_id)
    query = db.query(AttendanceHistory).filter_by(session_id=session_id)
    return {"items": [{"history_id": row.history_id, "version": row.version, "student_id": row.student_id,
                      "student_name": row.student_name, "from_status": row.from_status, "to_status": row.to_status,
                      "actor_name": row.actor_name, "actor_id": row.actor_id, "created_at": crud.iso(row.created_at)}
                     for row in query.order_by(AttendanceHistory.history_id.desc()).offset((page-1)*page_size).limit(page_size)],
            "total": query.count(), "page": page, "page_size": page_size}



@router.get("/rooms/{room_id}/capacity")
def room_capacity(room_id: int, class_id: int = Query(gt=0), on_date: date = Query(),
                  user: User = Depends(require_prepare), db: Session = Depends(get_db)):
    from app.routers.rooms import capacity_check
    return capacity_check(db, room_id, class_id, on_date)
