from app.crud import attendance_risk
from fastapi import APIRouter, Depends, HTTPException, Query
import re
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.core.auth import get_current_user
from app.core.permissions import require_permission
from app.crud import attendance as clock
from app.crud.leads import user_roles
from app.crud.lead_conversion import conversion_info
from app.models.lead_conversion import LeadConversion
from app.crud.student_enrollments import enrollment_rows, materialize_legacy
from app.database import get_db
from app.models.user import User
from app.models.role import Role
from app.models.user_role import UserRole
from app.models.training_program import TrainingClass
from app.models.subject import ClassSubject
from app.models.attendance import StudentClass, ClassSession, SessionAttendance
from app.models.student_enrollment import StudentEnrollment, StudentEnrollmentHistory
from app.schemas.student_enrollment import EnrollmentChange
from app.models.class_enrollment import ClassEnrollmentRecord
from app.crud.class_enrollments import lock_classes, ensure_capacity
from app.models.student_profile import StudentProfileDetails
from app.schemas.student_profile import UpdateStudentProfile
from app.crud.student_profile import personal_info, profile_token, update_profile


def require_manager(user: User = Depends(require_permission("STUDENT_MANAGE")), db: Session = Depends(get_db)):
    if not user_roles(db, user) & {"TRAINING_MANAGER", "MANAGER"}:
        raise HTTPException(403, "Chức năng này chỉ dành cho Quản lý đào tạo.")
    return user


router = APIRouter(prefix="/api/students", tags=["Hồ sơ học viên"], dependencies=[Depends(require_manager)])


def student_or_404(db, student_id):
    student = db.get(User, student_id)
    if student is None:
        raise HTTPException(404, "Không tìm thấy hồ sơ học viên.")
    # Read old history even if the account's roles later change.
    if "STUDENT" not in user_roles(db, student) and not db.query(StudentEnrollmentHistory).filter_by(student_id=student_id).first():
        raise HTTPException(404, "Không tìm thấy hồ sơ học viên.")
    return student


def history_item(row):
    return {key: getattr(row, key) for key in ["history_id", "student_id", "student_name", "student_code",
        "operation", "effective_date", "reason", "from_class_id", "from_class_name", "to_class_id",
        "to_class_name", "actor_id", "actor_name"]} | {"created_at": clock.iso(row.created_at)}


def profile(db, student):
    rows = enrollment_rows(db, student.user_id, clock.today())
    registrations = {row.enrollment_id: {"enrolled_on": row.enrolled_on, "actor_name": row.actor_name, "actor_id": row.actor_id, "created_at": clock.iso(row.created_at)} for row in db.query(ClassEnrollmentRecord).filter_by(student_id=student.user_id)}
    classes = {row.class_id: row for row in db.query(TrainingClass).filter(TrainingClass.class_id.in_([item["class_id"] for item in rows]))}
    details = db.get(StudentProfileDetails, student.user_id)
    today = clock.today()
    enrollments = []
    for row in rows:
        classroom = classes.get(row['class_id'])
        class_status = classroom.status if classroom else None
        ended = row['ended_on'] is not None and row['ended_on'] <= today
        group = 'history' if ended or class_status in {'completed', 'cancelled'} else (
            'upcoming' if row['status'] == 'upcoming' or class_status == 'planned' or (classroom and classroom.start_date and classroom.start_date > today) else 'current')
        display_status = row['status'] if ended else (class_status if class_status in {'completed', 'cancelled'} else ('upcoming' if group == 'upcoming' else row['status']))
        enrollments.append({**row, 'class_name': classroom.class_name if classroom else 'Lớp cũ',
            'class_status': class_status, 'group': group, 'display_status': display_status,
            'can_change': row['can_change'] and class_status in {'planned', 'in_progress'},
            'registration': registrations.get(row['enrollment_id'])})
    return {"student_id": student.user_id, "student_name": student.full_name, "student_code": student.username,
            **personal_info(student, details), 'profile_token': profile_token(student, details),
            'updated_by_name': details.updated_by_name if details else None,
            'updated_at': clock.iso(details.updated_at) if details else None,
            "admission": conversion_info(db.query(LeadConversion).filter_by(student_id=student.user_id).first()),
            "today": today, "enrollments": enrollments, "attendance_risks": attendance_risk.active_flags(db, student_ids=[student.user_id])}



@router.get("/classes")
def class_options(db: Session = Depends(get_db)):
    return [{"class_id": row.class_id, "class_name": row.class_name, "start_date": row.start_date, "status": row.status}
            for row in db.query(TrainingClass).order_by(TrainingClass.class_name, TrainingClass.class_id)]


@router.get("")
def list_students(search: str = Query("", max_length=100), class_id: int | None = Query(None, gt=0),
                  page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)):
    attendance_risk.refresh(db)
    ids = db.query(UserRole.user_id).join(Role, Role.role_id == UserRole.role_id).filter(Role.role_name == "STUDENT")
    query = db.query(User).filter(or_(User.user_id.in_(ids), User.user_id.in_(db.query(StudentEnrollmentHistory.student_id))))
    if search.strip():
        term = search.strip().lower()
        phone_term = re.sub(r"[\s().-]", "", term)
        if phone_term.startswith('+84'):
            phone_term = '0' + phone_term[3:]
        phone = User.phone
        for separator in [' ', '.', '-', '(', ')']:
            phone = func.replace(phone, separator, '')
        phone = func.replace(phone, '+84', '0')
        conditions = [func.lower(User.full_name).contains(term, autoescape=True), func.lower(User.username).contains(term, autoescape=True)]
        if phone_term and phone_term.isascii() and phone_term.isdigit():
            conditions.append(phone.contains(phone_term, autoescape=True))
        query = query.filter(or_(*conditions))
    if class_id is not None:
        query = query.filter(User.user_id.in_(db.query(StudentClass.student_id).filter_by(class_id=class_id)))
    return {"items": [profile(db, row) for row in query.order_by(User.full_name, User.user_id).offset((page-1)*page_size).limit(page_size)],
            "total": query.count(), "page": page, "page_size": page_size}


@router.get("/{student_id}")
def get_profile(student_id: int, db: Session = Depends(get_db)):
    attendance_risk.refresh(db)
    return profile(db, student_or_404(db, student_id))


@router.put("/{student_id}")
def edit_profile(student_id: int, data: UpdateStudentProfile, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return profile(db, update_profile(db, student_id, data, user))


@router.get("/{student_id}/history")
def history(student_id: int, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)):
    student_or_404(db, student_id)
    query = db.query(StudentEnrollmentHistory).filter_by(student_id=student_id)
    return {"items": [history_item(row) for row in query.order_by(StudentEnrollmentHistory.history_id.desc()).offset((page-1)*page_size).limit(page_size)],
            "total": query.count(), "page": page, "page_size": page_size}


@router.get("/{student_id}/attendance")
def attendance_history(student_id: int, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)):
    student_or_404(db, student_id)
    query = db.query(SessionAttendance, ClassSession, TrainingClass).join(
        ClassSession, ClassSession.session_id == SessionAttendance.session_id).join(
        ClassSubject, ClassSubject.id == ClassSession.class_subject_id).join(
        TrainingClass, TrainingClass.class_id == ClassSubject.class_id).filter(SessionAttendance.student_id == student_id)
    return {"items": [{"session_id": session.session_id, "class_id": classroom.class_id, "class_name": classroom.class_name,
                      "session_date": session.session_date, "topic": session.topic, "status": record.status}
                     for record, session, classroom in query.order_by(ClassSession.session_date.desc(), ClassSession.session_id.desc()).offset((page-1)*page_size).limit(page_size)],
            "total": query.count(), "page": page, "page_size": page_size}


@router.post("/{student_id}/changes", status_code=201)
def change_enrollment(student_id: int, data: EnrollmentChange, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    class_ids = sorted({data.class_id, *([data.to_class_id] if data.to_class_id else [])})
    lock_classes(db, class_ids)
    classrooms = {row.class_id: row for row in db.query(TrainingClass).filter(TrainingClass.class_id.in_(class_ids)).order_by(
        TrainingClass.class_id).populate_existing().with_for_update()}
    if len(classrooms) != len(class_ids):
        raise HTTPException(404, "Không tìm thấy lớp nguồn hoặc lớp đích.")
    student = db.query(User).filter_by(user_id=student_id).populate_existing().with_for_update().first()
    if student is None or "STUDENT" not in user_roles(db, student):
        raise HTTPException(404, "Không tìm thấy học viên.")
    try:
        periods = materialize_legacy(db, student_id, data.class_id)
        if data.enrollment_id is None:
            source = periods[0] if len(periods) == 1 and periods[0].origin == "legacy" and periods[0].ended_on is None else None
        else:
            source = next((row for row in periods if row.enrollment_id == data.enrollment_id), None)
        if source is None or source.ended_on is not None:
            raise HTTPException(409, "Hồ sơ đã thay đổi hoặc đã có quyết định cho lần học này. Vui lòng tải lại.")
        if source.started_on and data.effective_date < source.started_on:
            raise HTTPException(422, "Ngày hiệu lực không được trước ngày bắt đầu học tại lớp.")
        if data.operation == "transferred":
            target = classrooms[data.to_class_id]
            if target.status not in {"planned", "in_progress"}:
                raise HTTPException(422, "Lớp đích phải đang học hoặc sắp khai giảng.")
            if target.start_date and data.effective_date < target.start_date:
                raise HTTPException(422, "Ngày chuyển lớp không được trước ngày khai giảng lớp đích.")
            ensure_capacity(db, target, data.effective_date, 1)
            targets = materialize_legacy(db, student_id, target.class_id)
            if any(row.ended_on is None or row.ended_on > data.effective_date for row in targets):
                raise HTTPException(409, "Học viên đã có thời gian học trùng tại lớp đích.")
            db.add(StudentEnrollment(student_id=student_id, class_id=target.class_id,
                                     started_on=data.effective_date, origin="transfer"))
            if db.get(StudentClass, (student_id, target.class_id)) is None:
                db.add(StudentClass(student_id=student_id, class_id=target.class_id, enrolled_date=data.effective_date))
        changed = db.query(StudentEnrollment).filter_by(enrollment_id=source.enrollment_id, ended_on=None).update(
            {"ended_on": data.effective_date, "end_status": data.operation}, synchronize_session=False)
        if changed != 1:
            raise HTTPException(409, "Hồ sơ vừa được cập nhật. Vui lòng tải lại.")
        record = StudentEnrollmentHistory(student_id=student_id, student_name=student.full_name, student_code=student.username,
            enrollment_id=source.enrollment_id, operation=data.operation, effective_date=data.effective_date, reason=data.reason,
            from_class_id=data.class_id, from_class_name=classrooms[data.class_id].class_name,
            to_class_id=data.to_class_id, to_class_name=classrooms[data.to_class_id].class_name if data.to_class_id else None,
            actor_id=user.user_id, actor_name=user.full_name, created_at=clock.utcnow())
        db.add(record)
        db.commit()
        attendance_risk.refresh_after_save(db)
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Hồ sơ vừa thay đổi. Chưa thực hiện thao tác; vui lòng tải lại.")
    except Exception:
        db.rollback()
        raise
    db.refresh(record)
    return history_item(record)
