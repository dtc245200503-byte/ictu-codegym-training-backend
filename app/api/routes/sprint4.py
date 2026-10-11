import base64
import binascii
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from app.api.dependencies import get_current_user, require_roles
from app.core.security import DEFAULT_ACCOUNT_PASSWORD
from app.db.session import get_db
from app.models.audit_log import AuditLog
from app.models.attendance import (
    AbsenceRequest,
    AttendanceChangeLog,
    AttendanceRecord,
    AttendanceScheduleHoliday,
    AttendanceSession,
)
from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.models.notification import Notification
from app.models.student_enrollment_history import StudentEnrollmentHistory
from app.models.training_class import ClassEnrollment, TrainingClass
from app.models.user import User
from app.schemas.sprint4 import (
    AbsenceRequestCreate,
    AbsenceRequestDecision,
    AttendanceBulkUpdate,
    EnrollmentChangeCreate,
    ScheduleGenerateRequest,
    ScheduleHolidayCreate,
    ScheduleSessionUpdate,
)

router = APIRouter(tags=["Sprint 4 schedule and attendance"])
MANAGER_ROLES = ("admin", "manager")
STAFF_ROLES = ("admin", "manager", "teacher", "ta")
DAY_NAMES = ("Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật")


def _has_class_staff_access(user: User, training_class: TrainingClass) -> bool:
    roles = {role.code for role in user.roles}
    if roles.intersection(MANAGER_ROLES):
        return True
    if "teacher" in roles and training_class.teacher_id == user.id:
        return True
    return "ta" in roles and (
        training_class.assistant_id == user.id
        or any(assistant.id == user.id for assistant in training_class.assistants)
    )


def _assert_class_staff_access(
    user: User,
    training_class: TrainingClass,
    session: AttendanceSession | None = None,
) -> None:
    if _has_class_staff_access(user, training_class):
        return
    if (
        session is not None
        and "teacher" in {role.code for role in user.roles}
        and _session_teacher(session) == user.id
    ):
        return
    raise HTTPException(status_code=403, detail="Bạn không được phân công vào lớp này.")


def _effective_enrollments(
    db: Session, training_class: TrainingClass, session_date: date
) -> list[ClassEnrollment]:
    enrollments = db.scalars(
        select(ClassEnrollment)
        .options(joinedload(ClassEnrollment.student))
        .where(ClassEnrollment.class_id == training_class.id)
        .order_by(ClassEnrollment.id)
    ).all()
    return [
        enrollment
        for enrollment in enrollments
        if (
            enrollment.enrollment_status == "active"
            and (
                enrollment.effective_date is None
                or enrollment.effective_date <= session_date
            )
        )
        or (
            enrollment.enrollment_status != "active"
            and enrollment.effective_date is not None
            and session_date < enrollment.effective_date
        )
    ]


def _session_teacher(session: AttendanceSession) -> int:
    return session.teacher_id or session.training_class.teacher_id


def _session_room(session: AttendanceSession) -> str:
    return session.room or session.training_class.room


def _session_payload(session: AttendanceSession) -> dict[str, Any]:
    training_class = session.training_class
    lesson = session.lesson
    teacher_id = _session_teacher(session)
    return {
        "id": session.id,
        "class_id": session.class_id,
        "class_code": training_class.code,
        "class_name": training_class.name,
        "course_id": training_class.course_id,
        "course_code": training_class.course.code,
        "course_name": training_class.course.name,
        "session_date": session.session_date.isoformat(),
        "weekday": DAY_NAMES[session.session_date.weekday()],
        "start_time": session.start_time.strftime("%H:%M"),
        "end_time": session.end_time.strftime("%H:%M"),
        "teacher_id": teacher_id,
        "teacher_name": (
            session.teacher.full_name
            if session.teacher_id is not None and session.teacher is not None
            else training_class.teacher.full_name
        ),
        "room": _session_room(session),
        "lesson_id": session.lesson_id,
        "lesson_sequence": lesson.sequence if lesson else None,
        "lesson_title": lesson.title if lesson else None,
        "is_cancelled": session.is_cancelled,
        "cancellation_reason": session.cancellation_reason,
        "conflict_override": session.conflict_override,
        "has_attendance": bool(session.records),
    }


def _load_class(db: Session, class_id: int) -> TrainingClass:
    training_class = db.scalar(
        select(TrainingClass)
        .options(
            joinedload(TrainingClass.course),
            joinedload(TrainingClass.teacher),
            selectinload(TrainingClass.assistants),
        )
        .where(TrainingClass.id == class_id)
    )
    if training_class is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy lớp học.")
    return training_class


def _candidate_conflicts(
    db: Session,
    training_class: TrainingClass,
    session_date: date,
    start_time: time,
    end_time: time,
    teacher_id: int,
    room: str,
    exclude_session_id: int | None = None,
) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    explicit_sessions = db.scalars(
        select(AttendanceSession)
        .options(
            joinedload(AttendanceSession.training_class).joinedload(TrainingClass.course),
            joinedload(AttendanceSession.training_class).joinedload(TrainingClass.teacher),
            joinedload(AttendanceSession.teacher),
            joinedload(AttendanceSession.lesson),
        )
        .join(TrainingClass, AttendanceSession.class_id == TrainingClass.id)
        .where(
            TrainingClass.id != training_class.id,
            AttendanceSession.session_date == session_date,
            AttendanceSession.id != (exclude_session_id or -1),
            AttendanceSession.is_cancelled.is_(False),
            AttendanceSession.start_time < end_time,
            AttendanceSession.end_time > start_time,
        )
    ).unique().all()
    explicit_class_ids = {session.class_id for session in explicit_sessions}
    for session in explicit_sessions:
        same_teacher = _session_teacher(session) == teacher_id
        same_room = _session_room(session).casefold() == room.casefold()
        if same_teacher or same_room:
            conflicts.append(
                {
                    "date": session_date.isoformat(),
                    "class_id": session.class_id,
                    "class_code": session.training_class.code,
                    "session_id": session.id,
                    "start_time": session.start_time.strftime("%H:%M"),
                    "end_time": session.end_time.strftime("%H:%M"),
                    "teacher_conflict": same_teacher,
                    "room_conflict": same_room,
                }
            )

    recurring_classes = db.scalars(
        select(TrainingClass)
        .options(joinedload(TrainingClass.teacher))
        .where(
            TrainingClass.id != training_class.id,
            TrainingClass.status.in_(("preparing", "active")),
            TrainingClass.day_of_week == session_date.weekday(),
            TrainingClass.start_time < end_time,
            TrainingClass.end_time > start_time,
        )
    ).all()
    for other_class in recurring_classes:
        if other_class.id in explicit_class_ids:
            continue
        same_teacher = other_class.teacher_id == teacher_id
        same_room = other_class.room.casefold() == room.casefold()
        if same_teacher or same_room:
            conflicts.append(
                {
                    "date": session_date.isoformat(),
                    "class_id": other_class.id,
                    "class_code": other_class.code,
                    "session_id": None,
                    "start_time": other_class.start_time.strftime("%H:%M"),
                    "end_time": other_class.end_time.strftime("%H:%M"),
                    "teacher_conflict": same_teacher,
                    "room_conflict": same_room,
                }
            )
    return conflicts


def _generate_preview(
    db: Session, training_class: TrainingClass, request: ScheduleGenerateRequest
) -> list[dict[str, Any]]:
    if training_class.status not in {"preparing", "active"}:
        raise HTTPException(status_code=409, detail="Lớp không ở trạng thái được xếp lịch.")
    teacher_id = request.teacher_id or training_class.teacher_id
    room = request.room or training_class.room
    start_time = request.start_time or training_class.start_time
    end_time = request.end_time or training_class.end_time
    if end_time <= start_time:
        raise HTTPException(status_code=422, detail="Giờ kết thúc phải sau giờ bắt đầu.")
    teacher = db.get(User, teacher_id)
    if teacher is None or "teacher" not in {role.code for role in teacher.roles}:
        raise HTTPException(status_code=422, detail="Giảng viên được phân công không hợp lệ.")

    lessons = db.scalars(
        select(CourseLesson)
        .where(CourseLesson.course_id == training_class.course_id)
        .order_by(CourseLesson.sequence)
    ).all()
    if len(lessons) < request.session_count:
        raise HTTPException(
            status_code=422,
            detail=f"Syllabus có {len(lessons)} buổi, không đủ {request.session_count} buổi để xếp lịch.",
        )
    holidays = {
        holiday.holiday_date: holiday.name
        for holiday in db.scalars(
            select(AttendanceScheduleHoliday).where(
                AttendanceScheduleHoliday.holiday_date >= request.start_date
            )
        ).all()
    }

    sessions = []
    current = request.start_date
    examined_days = 0
    while len(sessions) < request.session_count and examined_days < 3650:
        examined_days += 1
        if current.weekday() not in request.weekdays:
            current += timedelta(days=1)
            continue
        if current in holidays:
            current += timedelta(days=1)
            continue
        if db.scalar(
            select(AttendanceSession.id).where(
                AttendanceSession.class_id == training_class.id,
                AttendanceSession.session_date == current,
            )
        ):
            raise HTTPException(
                status_code=409,
                detail=f"Lớp đã có buổi học ngày {current.isoformat()}.",
            )
        lesson = lessons[len(sessions)]
        conflicts = _candidate_conflicts(
            db,
            training_class,
            current,
            start_time,
            end_time,
            teacher_id,
            room,
        )
        sessions.append(
            {
                "session_date": current.isoformat(),
                "weekday": DAY_NAMES[current.weekday()],
                "start_time": start_time.strftime("%H:%M"),
                "end_time": end_time.strftime("%H:%M"),
                "teacher_id": teacher_id,
                "teacher_name": teacher.full_name,
                "room": room,
                "lesson_id": lesson.id,
                "lesson_sequence": lesson.sequence,
                "lesson_title": lesson.title,
                "conflicts": conflicts,
            }
        )
        current += timedelta(days=1)
    if len(sessions) != request.session_count:
        raise HTTPException(status_code=422, detail="Không thể sinh đủ số buổi yêu cầu.")
    return sessions


def _notify_schedule_change(
    db: Session,
    training_class: TrainingClass,
    user_ids: set[int],
    title: str,
    message: str,
    teacher_id: int | None = None,
) -> None:
    recipients = user_ids | {training_class.teacher_id}
    if teacher_id is not None:
        recipients.add(teacher_id)
    db.add_all(
        Notification(
            user_id=user_id,
            class_id=training_class.id,
            title=title,
            message=message,
        )
        for user_id in recipients
    )


@router.get("/schedule/holidays")
def list_schedule_holidays(
    start_date: date | None = None,
    end_date: date | None = None,
    _user: User = Depends(require_roles(*MANAGER_ROLES)),
    db: Session = Depends(get_db),
) -> list[dict]:
    query = select(AttendanceScheduleHoliday)
    if start_date:
        query = query.where(AttendanceScheduleHoliday.holiday_date >= start_date)
    if end_date:
        query = query.where(AttendanceScheduleHoliday.holiday_date <= end_date)
    return [
        {"id": item.id, "holiday_date": item.holiday_date.isoformat(), "name": item.name}
        for item in db.scalars(query.order_by(AttendanceScheduleHoliday.holiday_date)).all()
    ]


@router.post("/schedule/holidays", status_code=status.HTTP_201_CREATED)
def create_schedule_holiday(
    request: ScheduleHolidayCreate,
    user: User = Depends(require_roles(*MANAGER_ROLES)),
    db: Session = Depends(get_db),
) -> dict:
    if db.scalar(
        select(AttendanceScheduleHoliday.id).where(
            AttendanceScheduleHoliday.holiday_date == request.holiday_date
        )
    ):
        raise HTTPException(status_code=409, detail="Ngày nghỉ đã được khai báo.")
    holiday = AttendanceScheduleHoliday(
        holiday_date=request.holiday_date,
        name=request.name,
        created_by=user.id,
    )
    db.add(holiday)
    db.commit()
    db.refresh(holiday)
    return {"id": holiday.id, "holiday_date": holiday.holiday_date.isoformat(), "name": holiday.name}


@router.delete("/schedule/holidays/{holiday_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_schedule_holiday(
    holiday_id: int,
    _user: User = Depends(require_roles(*MANAGER_ROLES)),
    db: Session = Depends(get_db),
) -> None:
    holiday = db.get(AttendanceScheduleHoliday, holiday_id)
    if holiday is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy ngày nghỉ.")
    db.delete(holiday)
    db.commit()


@router.post("/classes/{class_id}/sessions/preview")
def preview_class_sessions(
    class_id: int,
    request: ScheduleGenerateRequest,
    _manager: User = Depends(require_roles(*MANAGER_ROLES)),
    db: Session = Depends(get_db),
) -> dict:
    training_class = _load_class(db, class_id)
    sessions = _generate_preview(db, training_class, request)
    conflicts = [conflict for session in sessions for conflict in session["conflicts"]]
    return {
        "class_id": class_id,
        "session_count": len(sessions),
        "conflict_count": len(conflicts),
        "sessions": sessions,
    }


@router.post("/classes/{class_id}/sessions", status_code=status.HTTP_201_CREATED)
def create_class_sessions(
    class_id: int,
    request: ScheduleGenerateRequest,
    manager: User = Depends(require_roles(*MANAGER_ROLES)),
    db: Session = Depends(get_db),
) -> dict:
    training_class = _load_class(db, class_id)
    sessions = _generate_preview(db, training_class, request)
    conflicts = [conflict for session in sessions for conflict in session["conflicts"]]
    if conflicts and not request.allow_conflicts:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Có xung đột giảng viên hoặc phòng học.",
                "conflicts": conflicts,
                "override_required": True,
            },
        )
    records = [
        AttendanceSession(
            class_id=class_id,
            session_date=date.fromisoformat(item["session_date"]),
            start_time=time.fromisoformat(item["start_time"]),
            end_time=time.fromisoformat(item["end_time"]),
            teacher_id=item["teacher_id"],
            room=item["room"],
            lesson_id=item["lesson_id"],
            conflict_override=bool(item["conflicts"]),
        )
        for item in sessions
    ]
    db.add_all(records)
    try:
        db.flush()
        for item in records:
            enrolled_student_ids = {
                enrollment.student_id
                for enrollment in _effective_enrollments(
                    db, training_class, item.session_date
                )
            }
            _notify_schedule_change(
                db,
                training_class,
                enrolled_student_ids,
                "Thời khóa biểu mới",
                f"{training_class.code}: buổi học ngày {item.session_date.isoformat()} đã được xếp lịch.",
            )
        db.add(
            AuditLog(
                actor_id=manager.id,
                action="schedule.generated",
                details=f"class_id={class_id}; sessions={len(records)}; overridden={bool(conflicts)}",
            )
        )
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="Lịch có buổi trùng ngày; chưa lưu buổi nào.") from error
    return {
        "class_id": class_id,
        "created_count": len(records),
        "sessions": [
            {
                "id": item.id,
                "session_date": item.session_date.isoformat(),
                "lesson_id": item.lesson_id,
                "conflict_override": item.conflict_override,
            }
            for item in records
        ],
    }


def _schedule_query(db: Session):
    return select(AttendanceSession).options(
        joinedload(AttendanceSession.training_class).joinedload(TrainingClass.course),
        joinedload(AttendanceSession.training_class).joinedload(TrainingClass.teacher),
        joinedload(AttendanceSession.teacher),
        joinedload(AttendanceSession.lesson),
        selectinload(AttendanceSession.records),
    )


@router.get("/schedule/sessions")
def list_schedule_sessions(
    start_date: date,
    end_date: date,
    class_id: int | None = None,
    teacher_id: int | None = None,
    room: str | None = Query(default=None, max_length=80),
    user: User = Depends(require_roles("admin", "manager", "admissions", "accountant", "teacher", "ta", "student")),
    db: Session = Depends(get_db),
) -> list[dict]:
    if end_date < start_date or (end_date - start_date).days > 370:
        raise HTTPException(status_code=422, detail="Khoảng ngày không hợp lệ (tối đa 371 ngày).")
    query = _schedule_query(db).where(
        AttendanceSession.session_date.between(start_date, end_date)
    )
    roles = {role.code for role in user.roles}
    if class_id:
        query = query.where(AttendanceSession.class_id == class_id)
    if roles.intersection({"teacher", "ta"}):
        query = query.join(TrainingClass, AttendanceSession.class_id == TrainingClass.id).where(
            or_(
                TrainingClass.teacher_id == user.id,
                TrainingClass.assistant_id == user.id,
                TrainingClass.assistants.any(User.id == user.id),
                AttendanceSession.teacher_id == user.id,
            )
        )
    elif "student" in roles and not roles.intersection(MANAGER_ROLES):
        query = query.join(TrainingClass, AttendanceSession.class_id == TrainingClass.id).join(
            ClassEnrollment, ClassEnrollment.class_id == TrainingClass.id
        ).where(
            ClassEnrollment.student_id == user.id,
            or_(
                and_(
                    ClassEnrollment.enrollment_status == "active",
                    or_(
                        ClassEnrollment.effective_date.is_(None),
                        ClassEnrollment.effective_date <= AttendanceSession.session_date,
                    ),
                ),
                and_(
                    ClassEnrollment.enrollment_status != "active",
                    ClassEnrollment.effective_date > AttendanceSession.session_date,
                ),
            ),
        )
    if teacher_id:
        query = query.where(
            or_(
                AttendanceSession.teacher_id == teacher_id,
                and_(
                    AttendanceSession.teacher_id.is_(None),
                    AttendanceSession.class_id.in_(
                        select(TrainingClass.id).where(TrainingClass.teacher_id == teacher_id)
                    ),
                ),
            )
        )
    if room:
        query = query.where(
            or_(
                AttendanceSession.room == room,
                and_(
                    AttendanceSession.room.is_(None),
                    AttendanceSession.class_id.in_(
                        select(TrainingClass.id).where(TrainingClass.room == room)
                    ),
                ),
            )
        )
    sessions = db.scalars(
        query.order_by(AttendanceSession.session_date, AttendanceSession.start_time)
    ).unique().all()
    return [_session_payload(session) for session in sessions]


@router.get("/me/schedule")
def personal_schedule(
    start_date: date = Query(default_factory=date.today),
    end_date: date | None = None,
    user: User = Depends(require_roles("student")),
    db: Session = Depends(get_db),
) -> list[dict]:
    actual_end = end_date or start_date + timedelta(days=6)
    query = _schedule_query(db).join(
        ClassEnrollment, ClassEnrollment.class_id == AttendanceSession.class_id
    ).where(
        ClassEnrollment.student_id == user.id,
        or_(
            and_(
                ClassEnrollment.enrollment_status == "active",
                or_(
                    ClassEnrollment.effective_date.is_(None),
                    ClassEnrollment.effective_date <= AttendanceSession.session_date,
                ),
            ),
            and_(
                ClassEnrollment.enrollment_status != "active",
                ClassEnrollment.effective_date > AttendanceSession.session_date,
            ),
        ),
        AttendanceSession.session_date.between(start_date, actual_end),
    )
    sessions = db.scalars(
        query.order_by(AttendanceSession.session_date, AttendanceSession.start_time)
    ).unique().all()
    return [_session_payload(session) for session in sessions]


@router.get("/classes/{class_id}/sessions")
def list_class_sessions(
    class_id: int,
    start_date: date | None = None,
    end_date: date | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    training_class = _load_class(db, class_id)
    roles = {role.code for role in user.roles}
    session_teacher_only = False
    if not roles.intersection(MANAGER_ROLES + ("admissions", "accountant")):
        if "student" in roles:
            if not db.scalar(
                select(ClassEnrollment.id).where(
                    ClassEnrollment.class_id == class_id,
                    ClassEnrollment.student_id == user.id,
                    ClassEnrollment.enrollment_status == "active",
                )
            ):
                raise HTTPException(status_code=403, detail="Bạn không tham gia lớp này.")
        elif "teacher" in roles and not _has_class_staff_access(user, training_class):
            session_teacher_only = True
        else:
            _assert_class_staff_access(user, training_class)
    query = _schedule_query(db).where(AttendanceSession.class_id == class_id)
    if session_teacher_only:
        query = query.where(AttendanceSession.teacher_id == user.id)
    if start_date:
        query = query.where(AttendanceSession.session_date >= start_date)
    if end_date:
        query = query.where(AttendanceSession.session_date <= end_date)
    return [
        _session_payload(item)
        for item in db.scalars(
            query.order_by(AttendanceSession.session_date, AttendanceSession.start_time)
        ).unique().all()
    ]


@router.get("/students/{student_id}/enrollments")
def list_student_enrollments(
    student_id: int,
    _manager: User = Depends(require_roles(*MANAGER_ROLES)),
    db: Session = Depends(get_db),
) -> list[dict]:
    enrollments = db.scalars(
        select(ClassEnrollment)
        .options(
            joinedload(ClassEnrollment.training_class)
            .joinedload(TrainingClass.course)
        )
        .where(
            ClassEnrollment.student_id == student_id,
            ClassEnrollment.enrollment_status == "active",
        )
        .order_by(ClassEnrollment.id)
    ).all()
    return [
        {
            "class_id": enrollment.class_id,
            "class_code": enrollment.training_class.code,
            "class_name": enrollment.training_class.name,
            "course_name": enrollment.training_class.course.name,
        }
        for enrollment in enrollments
    ]


@router.patch("/schedule/sessions/{session_id}")
def update_schedule_session(
    session_id: int,
    request: ScheduleSessionUpdate,
    manager: User = Depends(require_roles(*MANAGER_ROLES)),
    db: Session = Depends(get_db),
) -> dict:
    session = db.scalar(
        _schedule_query(db).where(AttendanceSession.id == session_id)
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy buổi học.")
    if session.records:
        raise HTTPException(status_code=409, detail="Buổi học đã điểm danh nên không thể chỉnh sửa.")
    updates = request.model_dump(exclude_unset=True)
    allow_conflicts = updates.pop("allow_conflicts", False)
    if not updates:
        raise HTTPException(status_code=400, detail="Chưa có thay đổi cho buổi học.")
    if updates.get("is_cancelled") is True and not (updates.get("cancellation_reason") or "").strip():
        raise HTTPException(status_code=422, detail="Hủy buổi học bắt buộc nhập lý do.")
    if updates.get("is_cancelled") is False:
        updates["cancellation_reason"] = None
    next_date = updates.get("session_date", session.session_date)
    next_start = updates.get("start_time", session.start_time)
    next_end = updates.get("end_time", session.end_time)
    next_teacher_id = updates.get("teacher_id", _session_teacher(session))
    next_room = updates.get("room", _session_room(session))
    if next_end <= next_start:
        raise HTTPException(status_code=422, detail="Giờ kết thúc phải sau giờ bắt đầu.")
    if "teacher_id" in updates:
        teacher = db.get(User, next_teacher_id)
        if teacher is None or "teacher" not in {role.code for role in teacher.roles}:
            raise HTTPException(status_code=422, detail="Giảng viên dạy thay không hợp lệ.")
    conflicts = [] if updates.get("is_cancelled") is True else _candidate_conflicts(
        db,
        session.training_class,
        next_date,
        next_start,
        next_end,
        next_teacher_id,
        next_room,
        exclude_session_id=session.id,
    )
    if conflicts and not allow_conflicts:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Buổi học bị trùng giảng viên hoặc phòng.",
                "conflicts": conflicts,
                "override_required": True,
            },
        )
    for field, value in updates.items():
        setattr(session, field, value)
    session.conflict_override = bool(conflicts)
    active_students = {
        enrollment.student_id
        for enrollment in _effective_enrollments(db, session.training_class, next_date)
    }
    title = "Buổi học đã bị hủy" if session.is_cancelled else "Thời khóa biểu được cập nhật"
    _notify_schedule_change(
        db,
        session.training_class,
        active_students,
        title,
        f"{session.training_class.code}, ngày {next_date.isoformat()}: "
        f"{session.cancellation_reason if session.is_cancelled else 'lịch học đã thay đổi.'}",
        teacher_id=next_teacher_id,
    )
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="Lớp đã có buổi học trong ngày này.") from error
    db.refresh(session)
    return _session_payload(session)


@router.get("/attendance/sessions/{session_id}")
def get_session_attendance(
    session_id: int,
    user: User = Depends(require_roles(*STAFF_ROLES)),
    db: Session = Depends(get_db),
) -> dict:
    session = db.scalar(
        _schedule_query(db).where(AttendanceSession.id == session_id)
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy buổi học.")
    _assert_class_staff_access(user, session.training_class, session)
    enrollments = _effective_enrollments(db, session.training_class, session.session_date)
    records_by_student = {record.student_id: record for record in session.records}
    return {
        "session": _session_payload(session),
        "students": [
            {
                "id": enrollment.student_id,
                "student_code": enrollment.student.student_code or enrollment.student.username,
                "full_name": enrollment.student.full_name,
                "status": records_by_student[enrollment.student_id].status
                if enrollment.student_id in records_by_student
                else None,
            }
            for enrollment in enrollments
        ],
    }


@router.put("/attendance/sessions/{session_id}")
def mark_session_attendance(
    session_id: int,
    request: AttendanceBulkUpdate,
    user: User = Depends(require_roles(*STAFF_ROLES)),
    db: Session = Depends(get_db),
) -> dict:
    session = db.scalar(
        _schedule_query(db).where(AttendanceSession.id == session_id)
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy buổi học.")
    _assert_class_staff_access(user, session.training_class, session)
    if session.is_cancelled:
        raise HTTPException(status_code=409, detail="Buổi học đã bị hủy.")
    roles = {role.code for role in user.roles}
    if session.session_date < date.today() and not roles.intersection(MANAGER_ROLES):
        raise HTTPException(status_code=403, detail="Chỉ Quản lý đào tạo mới được sửa điểm danh ngày quá khứ.")
    active_student_ids = {
        enrollment.student_id
        for enrollment in _effective_enrollments(db, session.training_class, session.session_date)
    }
    requested_ids = {item.student_id for item in request.records}
    if not requested_ids.issubset(active_student_ids):
        raise HTTPException(status_code=422, detail="Danh sách có học viên không thuộc buổi học.")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    records_by_student = {record.student_id: record for record in session.records}
    for marked in request.records:
        record = records_by_student.get(marked.student_id)
        old_status = record.status if record else None
        if record is None:
            record = AttendanceRecord(
                session_id=session.id,
                student_id=marked.student_id,
                marked_by=user.id,
                status=marked.status,
                marked_at=now,
            )
            db.add(record)
            db.flush()
        else:
            record.status = marked.status
            record.marked_by = user.id
            record.marked_at = now
        if old_status != marked.status:
            db.add(
                AttendanceChangeLog(
                    record_id=record.id,
                    changed_by=user.id,
                    old_status=old_status,
                    new_status=marked.status,
                )
            )
        enrollment = db.scalar(
            select(ClassEnrollment).where(
                ClassEnrollment.class_id == session.class_id,
                ClassEnrollment.student_id == marked.student_id,
            )
        )
        if enrollment:
            enrollment.attendance_status = (
                "present" if marked.status == "present"
                else "late" if marked.status == "late"
                else "absent" if marked.status in ("excused_absent", "unexcused_absent")
                else "not_recorded"
            )
            enrollment.last_attendance_at = now
    db.commit()
    return {"session_id": session.id, "updated_count": len(request.records)}


def _absence_payload(request: AbsenceRequest) -> dict:
    return {
        "id": request.id,
        "session_id": request.session_id,
        "student_id": request.student_id,
        "reason": request.reason,
        "evidence_filename": request.evidence_filename,
        "has_evidence": request.evidence_data is not None,
        "status": request.status,
        "decision_reason": request.decision_reason,
        "decided_at": request.decided_at.isoformat() if request.decided_at else None,
        "created_at": request.created_at.isoformat(),
    }


@router.post("/attendance/sessions/{session_id}/absence-requests", status_code=status.HTTP_201_CREATED)
def create_absence_request(
    session_id: int,
    request: AbsenceRequestCreate,
    user: User = Depends(require_roles("student")),
    db: Session = Depends(get_db),
) -> dict:
    session = db.scalar(
        _schedule_query(db).where(AttendanceSession.id == session_id)
    )
    if session is None or session.is_cancelled:
        raise HTTPException(status_code=404, detail="Không tìm thấy buổi học.")
    if user.id not in {
        enrollment.student_id
        for enrollment in _effective_enrollments(
            db, session.training_class, session.session_date
        )
    }:
        raise HTTPException(status_code=403, detail="Bạn không tham gia lớp này.")
    if session.session_date < date.today():
        raise HTTPException(status_code=409, detail="Không thể xin phép cho buổi đã qua.")
    existing_pending = db.scalar(
        select(AbsenceRequest.id).where(
            AbsenceRequest.session_id == session.id,
            AbsenceRequest.student_id == user.id,
            AbsenceRequest.status == "pending",
        )
    )
    if existing_pending:
        raise HTTPException(status_code=409, detail="Đã có đơn chờ duyệt cho buổi học này.")
    evidence_data = None
    if request.evidence_base64:
        try:
            evidence_data = base64.b64decode(request.evidence_base64, validate=True)
        except (ValueError, binascii.Error) as error:
            raise HTTPException(status_code=422, detail="Tệp minh chứng không hợp lệ.") from error
        if len(evidence_data) > 10 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Tệp minh chứng vượt quá 10 MB.")
        if not request.evidence_filename or request.evidence_content_type not in {
            "application/pdf", "image/jpeg", "image/png", "image/webp",
        }:
            raise HTTPException(status_code=422, detail="Định dạng tệp minh chứng không được hỗ trợ.")
    absence = AbsenceRequest(
        session_id=session.id,
        student_id=user.id,
        reason=request.reason,
        evidence_filename=request.evidence_filename if evidence_data else None,
        evidence_content_type=request.evidence_content_type if evidence_data else None,
        evidence_data=evidence_data,
    )
    db.add(absence)
    db.commit()
    db.refresh(absence)
    return _absence_payload(absence)


@router.get("/me/absence-requests")
def list_my_absence_requests(
    user: User = Depends(require_roles("student")),
    db: Session = Depends(get_db),
) -> list[dict]:
    requests = db.scalars(
        select(AbsenceRequest)
        .where(AbsenceRequest.student_id == user.id)
        .order_by(AbsenceRequest.created_at.desc())
    ).all()
    return [_absence_payload(item) for item in requests]


@router.get("/classes/{class_id}/absence-requests")
def list_class_absence_requests(
    class_id: int,
    user: User = Depends(require_roles(*STAFF_ROLES)),
    db: Session = Depends(get_db),
) -> list[dict]:
    training_class = _load_class(db, class_id)
    session_teacher_only = (
        "teacher" in {role.code for role in user.roles}
        and not _has_class_staff_access(user, training_class)
    )
    if not session_teacher_only:
        _assert_class_staff_access(user, training_class)
    query = (
        select(AbsenceRequest)
        .join(AttendanceSession, AbsenceRequest.session_id == AttendanceSession.id)
        .where(AttendanceSession.class_id == class_id)
    )
    if session_teacher_only:
        has_assignment = db.scalar(
            select(AttendanceSession.id).where(
                AttendanceSession.class_id == class_id,
                AttendanceSession.teacher_id == user.id,
            ).limit(1)
        )
        if has_assignment is None:
            raise HTTPException(
                status_code=403, detail="Bạn không được phân công vào lớp này."
            )
        query = query.where(AttendanceSession.teacher_id == user.id)
    requests = db.scalars(query.order_by(AbsenceRequest.created_at.desc())).all()
    return [_absence_payload(item) for item in requests]


@router.get("/absence-requests/{request_id}/evidence")
def get_absence_evidence(
    request_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    absence = db.get(AbsenceRequest, request_id)
    if absence is None or absence.evidence_data is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy tệp minh chứng.")
    if absence.student_id != user.id:
        session = db.get(AttendanceSession, absence.session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy buổi học.")
        training_class = _load_class(db, session.class_id)
        _assert_class_staff_access(user, training_class, session)
    return Response(
        content=absence.evidence_data,
        media_type=absence.evidence_content_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{absence.evidence_filename or "evidence"}"'},
    )


@router.patch("/absence-requests/{request_id}")
def decide_absence_request(
    request_id: int,
    decision: AbsenceRequestDecision,
    user: User = Depends(require_roles(*STAFF_ROLES)),
    db: Session = Depends(get_db),
) -> dict:
    absence = db.get(AbsenceRequest, request_id)
    if absence is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy đơn xin vắng.")
    session = db.scalar(
        _schedule_query(db).where(AttendanceSession.id == absence.session_id)
    )
    _assert_class_staff_access(user, session.training_class, session)
    if absence.status != "pending":
        raise HTTPException(status_code=409, detail="Đơn đã được xử lý.")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    absence.status = decision.status
    absence.decision_reason = decision.decision_reason
    absence.decision_by = user.id
    absence.decided_at = now
    if decision.status == "approved":
        record = db.scalar(
            select(AttendanceRecord).where(
                AttendanceRecord.session_id == session.id,
                AttendanceRecord.student_id == absence.student_id,
            )
        )
        old_status = record.status if record else None
        if record is None:
            record = AttendanceRecord(
                session_id=session.id,
                student_id=absence.student_id,
                marked_by=user.id,
                status="excused_absent",
                marked_at=now,
            )
            db.add(record)
            db.flush()
        else:
            record.status = "excused_absent"
            record.marked_by = user.id
            record.marked_at = now
        if old_status != "excused_absent":
            db.add(
                AttendanceChangeLog(
                    record_id=record.id,
                    changed_by=user.id,
                    old_status=old_status,
                    new_status="excused_absent",
                )
            )
        enrollment = db.scalar(
            select(ClassEnrollment).where(
                ClassEnrollment.class_id == session.class_id,
                ClassEnrollment.student_id == absence.student_id,
            )
        )
        if enrollment:
            enrollment.attendance_status = "absent"
            enrollment.last_attendance_at = now
    db.add(
        Notification(
            user_id=absence.student_id,
            class_id=session.class_id,
            title="Đơn xin vắng đã được xử lý",
            message=f"{session.training_class.code} · {session.session_date}: {decision.status}. {decision.decision_reason}",
        )
    )
    db.commit()
    db.refresh(absence)
    return _absence_payload(absence)


@router.post("/student-enrollment-changes")
def process_student_enrollment_change(
    request: EnrollmentChangeCreate,
    user: User = Depends(require_roles(*MANAGER_ROLES)),
    db: Session = Depends(get_db),
) -> dict:
    old_enrollment = db.scalar(
        select(ClassEnrollment).where(
            ClassEnrollment.class_id == request.class_id,
            ClassEnrollment.student_id == request.student_id,
        )
    )
    if old_enrollment is None or old_enrollment.enrollment_status != "active":
        raise HTTPException(status_code=404, detail="Không tìm thấy ghi danh đang hoạt động.")
    student = db.get(User, request.student_id)
    if student is None or not any(role.code == "student" for role in student.roles):
        raise HTTPException(status_code=422, detail="Tài khoản không phải học viên.")
    target_class = None
    if request.action == "transfer":
        target_class = db.scalar(
            select(TrainingClass)
            .options(selectinload(TrainingClass.enrollments))
            .where(TrainingClass.id == request.target_class_id)
        )
        if (
            target_class is None
            or target_class.status not in {"preparing", "active"}
            or target_class.id == request.class_id
        ):
            raise HTTPException(status_code=422, detail="Lớp đích không hợp lệ.")
        if len(
            [item for item in target_class.enrollments if item.enrollment_status == "active"]
        ) >= target_class.capacity:
            raise HTTPException(status_code=409, detail="Lớp đích đã đủ sức chứa.")
        target_enrollment = db.scalar(
            select(ClassEnrollment).where(
                ClassEnrollment.class_id == target_class.id,
                ClassEnrollment.student_id == student.id,
            )
        )
        if target_enrollment and target_enrollment.enrollment_status == "active":
            raise HTTPException(status_code=409, detail="Học viên đã ghi danh lớp đích.")
        if target_enrollment:
            target_enrollment.enrollment_status = "active"
            target_enrollment.effective_date = request.effective_date
        else:
            db.add(
                ClassEnrollment(
                    class_id=target_class.id,
                    student_id=student.id,
                    enrollment_status="active",
                    effective_date=request.effective_date,
                )
            )
    old_enrollment.enrollment_status = {
        "suspend": "suspended",
        "transfer": "transferred",
        "withdraw": "withdrawn",
    }[request.action]
    old_enrollment.effective_date = request.effective_date
    history = StudentEnrollmentHistory(
        student_id=student.id,
        class_id=request.class_id,
        target_class_id=target_class.id if target_class else None,
        action=request.action,
        reason=request.reason,
        effective_date=request.effective_date,
        created_by=user.id,
    )
    db.add(history)
    db.add(
        Notification(
            user_id=student.id,
            class_id=request.class_id,
            title="Cập nhật tình trạng ghi danh",
            message=f"{request.action} có hiệu lực {request.effective_date.isoformat()}: {request.reason}",
        )
    )
    db.commit()
    db.refresh(history)
    return {
        "id": history.id,
        "student_id": history.student_id,
        "class_id": history.class_id,
        "target_class_id": history.target_class_id,
        "action": history.action,
        "reason": history.reason,
        "effective_date": history.effective_date.isoformat(),
    }


@router.get("/students/{student_id}/enrollment-history")
def get_student_enrollment_history(
    student_id: int,
    _manager: User = Depends(require_roles(*MANAGER_ROLES)),
    db: Session = Depends(get_db),
) -> list[dict]:
    history = db.scalars(
        select(StudentEnrollmentHistory)
        .where(StudentEnrollmentHistory.student_id == student_id)
        .order_by(StudentEnrollmentHistory.effective_date.desc(), StudentEnrollmentHistory.id.desc())
    ).all()
    return [
        {
            "id": item.id,
            "class_id": item.class_id,
            "target_class_id": item.target_class_id,
            "action": item.action,
            "reason": item.reason,
            "effective_date": item.effective_date.isoformat(),
            "created_at": item.created_at.isoformat(),
        }
        for item in history
    ]
