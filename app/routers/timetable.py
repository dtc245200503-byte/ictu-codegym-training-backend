from app.room_catalog import room_info
from app.models.room import Room
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.core.permissions import require_permission, user_has_permission
from app.crud.attendance import today
from app.crud.leads import user_roles
from app.database import get_db
from app.models.attendance import ClassSession, SessionAttendance
from app.models.subject import ClassSubject, ClassSubjectAssistant, Subject
from app.models.training_program import TrainingClass
from app.models.user import User
from app.schedule_conflicts import same_room


def require_manager(user: User = Depends(require_permission("TIMETABLE_VIEW")), db: Session = Depends(get_db)):
    if not user_roles(db, user) & {"TRAINING_MANAGER", "MANAGER"}:
        raise HTTPException(403, "Thời khóa biểu này dành cho Quản lý đào tạo.")
    return user


router = APIRouter(prefix="/api/timetable", tags=["Thời khóa biểu"], dependencies=[Depends(require_manager)])


def room_key(value):
    return " ".join((value or "").split()).casefold()


def calendar_items(db, start, end):
    rows = db.query(ClassSession, ClassSubject, TrainingClass, Subject).join(
        ClassSubject, ClassSubject.id == ClassSession.class_subject_id).join(
        TrainingClass, TrainingClass.class_id == ClassSubject.class_id).join(
        Subject, Subject.id == ClassSubject.subject_id).filter(
        ClassSession.session_date.between(start, end)).order_by(ClassSession.session_date, ClassSession.start_time, ClassSession.session_id).all()
    subject_ids = {subject.id for _, subject, _, _ in rows}
    assistants = {}
    for assignment in db.query(ClassSubjectAssistant).filter(ClassSubjectAssistant.class_subject_id.in_(subject_ids)):
        assistants.setdefault(assignment.class_subject_id, set()).add(assignment.user_id)
    ids = {subject.instructor_id for _, subject, _, _ in rows if subject.instructor_id}
    ids.update(session.substitute_instructor_id for session, _, _, _ in rows if session.substitute_instructor_id)
    ids.update(user_id for values in assistants.values() for user_id in values)
    names = {row.user_id: row.full_name for row in db.query(User).filter(User.user_id.in_(ids))}
    attended = {id for (id,) in db.query(SessionAttendance.session_id).filter(SessionAttendance.session_id.in_([row[0].session_id for row in rows])).distinct()}
    items = []
    for session, subject, classroom, course in rows:
        instructor_id = session.substitute_instructor_id or subject.instructor_id
        staff_ids = assistants.get(subject.id, set()) | ({instructor_id} if instructor_id else set())
        items.append({"session_id": session.session_id, "class_subject_id": session.class_subject_id,
            "lesson_id": session.lesson_id, "lesson_sequence": session.lesson_sequence, "lesson_objectives": session.lesson_objectives, "session_date": session.session_date.isoformat(),
            "start_time": session.start_time.strftime("%H:%M"), "end_time": session.end_time.strftime("%H:%M") if session.end_time else None,
            **room_info(db, session.room_id, session.room_name, classroom.class_id, session.session_date), "topic": session.topic, "class_id": classroom.class_id,
            "class_name": classroom.class_name, "class_status": classroom.status, "subject_name": course.subject_name,
            "instructor_id": instructor_id, "instructor_name": names.get(instructor_id),
            "substitute_instructor_id": session.substitute_instructor_id, "schedule_version": session.schedule_version,
            "status": session.status, "cancellation_reason": session.cancellation_reason,
            "staff": [{"user_id": id, "name": names.get(id, "Giảng viên cũ"), "is_main": id == instructor_id} for id in sorted(staff_ids)],
            "attendance_saved": bool(session.attendance_version or session.saved_at or session.session_id in attended), "conflicts": []})
    # Detect across the entire range BEFORE filtering, including sessions hidden by filters.
    # Half-open intervals. Unknown duration conservatively blocks later slots in that day.
    by_day = {}
    for item in items:
        if item["class_status"] != "cancelled" and item["status"] != "cancelled":
            by_day.setdefault(item["session_date"], []).append(item)
    for sessions in by_day.values():
        active = []
        for current in sessions:
            active = [other for other in active if (other["end_time"] or "24:00") > current["start_time"]]
            for other in active:
                reasons = []
                if current["class_id"] == other["class_id"]:
                    reasons.append("class")
                if {staff["user_id"] for staff in current["staff"]} & {staff["user_id"] for staff in other["staff"]}:
                    reasons.append("instructor")
                if same_room(current, other):
                    reasons.append("room")
                if reasons:
                    for item, peer in [(current, other), (other, current)]:
                        item["conflicts"].append({"session_id": peer["session_id"], "class_name": peer["class_name"],
                            "topic": peer["topic"], "start_time": peer["start_time"], "end_time": peer["end_time"], "reasons": reasons})
            active.append(current)
    return items


@router.get("/options")
def options(db: Session = Depends(get_db)):
    classes = db.query(TrainingClass).order_by(TrainingClass.class_name, TrainingClass.class_id).all()
    ids = {id for (id,) in db.query(ClassSubject.instructor_id) if id is not None}
    ids.update(id for (id,) in db.query(ClassSubjectAssistant.user_id))
    ids.update(id for (id,) in db.query(ClassSession.substitute_instructor_id) if id)
    teachers = db.query(User).filter(User.user_id.in_(ids)).order_by(User.full_name, User.user_id).all()
    rooms = {}
    catalog_names = {row.room_id: row.name for row in db.query(Room)}
    for room_id, old_name in db.query(ClassSession.room_id, ClassSession.room_name).distinct():
        room = catalog_names.get(room_id, old_name)
        if room_key(room):
            rooms.setdefault(room_key(room), " ".join(room.split()))
    return {"today": today(), "classes": [{"id": item.class_id, "name": item.class_name} for item in classes],
            "instructors": [{"id": item.user_id, "name": item.full_name} for item in teachers],
            "rooms": sorted(rooms.values(), key=room_key)}


@router.get("/sessions")
def list_sessions(date_from: date, date_to: date, class_id: int | None = Query(None, gt=0),
                  instructor_id: int | None = Query(None, ge=0), room: str | None = Query(None, max_length=100),
                  unassigned_room: bool = False, db: Session = Depends(get_db)):
    if date_to < date_from or (date_to - date_from).days > 41:
        raise HTTPException(422, "Chọn khoảng lịch từ 1 đến 42 ngày.")
    if room and unassigned_room:
        raise HTTPException(422, "Chỉ chọn một bộ lọc phòng.")
    items = calendar_items(db, date_from, date_to)
    if class_id is not None:
        items = [item for item in items if item["class_id"] == class_id]
    if instructor_id is not None:
        items = [item for item in items if (not item["staff"] if instructor_id == 0 else any(staff["user_id"] == instructor_id for staff in item["staff"]))]
    if room is not None:
        items = [item for item in items if room_key(item["room_name"]) == room_key(room)]
    if unassigned_room:
        items = [item for item in items if not room_key(item["room_name"])]
    return {"items": items, "total": len(items), "today": today(), "date_from": date_from, "date_to": date_to}


@router.get("/sessions/{session_id}")
def detail(session_id: int, user: User = Depends(require_manager), db: Session = Depends(get_db)):
    session = db.get(ClassSession, session_id)
    if session is None:
        raise HTTPException(404, "Không tìm thấy buổi học.")
    item = next(item for item in calendar_items(db, session.session_date, session.session_date) if item["session_id"] == session_id)
    reason = ("Buổi đã điểm danh, không được sửa, dời hoặc hủy." if item["attendance_saved"] else
              "Buổi hoặc lớp đã hủy." if item["status"] == "cancelled" or item["class_status"] == "cancelled" else
              "Bạn chưa có quyền sửa thời khóa biểu." if not user_has_permission(user.user_id, "TIMETABLE_EDIT", db) else None)
    return {**item, "can_manage": reason is None, "manage_reason": reason}
