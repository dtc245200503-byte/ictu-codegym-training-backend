"""Shared, transaction-scoped checks for every schedule writer.

The mutex serializes check + save across classes, including SQLite, where FOR
UPDATE alone is ineffective. Acquire it before class/room/calendar row locks.
"""
import hashlib
import hmac
import json
from datetime import date
from fastapi import HTTPException
from sqlalchemy import text
from app.core.security import SECRET_KEY
from app.crud import attendance as clock
from app.crud.leads import user_roles
from app.models.attendance import ClassSession
from app.models.subject import ClassSubject, ClassSubjectAssistant, Subject
from app.models.training_program import TrainingClass
from app.models.user import User
from app.models.room import Room
from app.models.schedule_conflict import ScheduleConflictOverride
from app.room_catalog import name_key


def lock_schedule(db):
    if db.bind.dialect.name == "mysql":
        sql = "INSERT INTO schedule_write_lock (id, revision) VALUES (1, 1) ON DUPLICATE KEY UPDATE revision = revision + 1"
    else:
        sql = "INSERT INTO schedule_write_lock (id, revision) VALUES (1, 1) ON CONFLICT(id) DO UPDATE SET revision = revision + 1"
    db.execute(text(sql))


def session_snapshot(db, subject, classroom, *, session_id=None, number=None, session_date,
                     start_time, end_time, room_id=None, room_name=None, topic, substitute_instructor_id=None,
                     instructor_id=None, assistant_ids=None):
    main = substitute_instructor_id or (instructor_id if instructor_id is not None else subject.instructor_id)
    if assistant_ids is None:
        assistant_ids = [row[0] for row in db.query(ClassSubjectAssistant.user_id).filter_by(class_subject_id=subject.id).with_for_update()]
    ids = set(assistant_ids) | ({main} if main else set())
    staff = sorted(({'user_id': row.user_id, 'name': row.full_name} for row in db.query(User).filter(User.user_id.in_(ids))), key=lambda row: row['user_id'])
    room = db.query(Room).filter_by(room_id=room_id).populate_existing().with_for_update().first() if room_id else None
    course = db.get(Subject, subject.subject_id)
    return {"session_id": session_id, "number": number, "class_subject_id": subject.id,
        "class_id": classroom.class_id, "class_name": classroom.class_name,
        "subject_name": course.subject_name if course else "Môn học cũ", "topic": topic,
        "session_date": session_date.isoformat(), "start_time": start_time.strftime("%H:%M"),
        "end_time": end_time.strftime("%H:%M") if end_time else None,
        "room_id": room_id, "room_name": room.name if room else room_name,
        "instructor_id": main, "staff": staff}


def existing_sessions(db, first, last):
    # Current reads avoid stale MySQL repeatable-read snapshots after waiting for the mutex.
    rows = db.query(ClassSession, ClassSubject, TrainingClass).join(ClassSubject, ClassSubject.id == ClassSession.class_subject_id).join(
        TrainingClass, TrainingClass.class_id == ClassSubject.class_id).filter(ClassSession.session_date.between(first, last),
        ClassSession.status != "cancelled", TrainingClass.status != "cancelled").order_by(ClassSession.session_id).populate_existing().with_for_update().all()
    return [session_snapshot(db, subject, classroom, session_id=row.session_id, session_date=row.session_date,
        start_time=row.start_time, end_time=row.end_time, room_id=row.room_id, room_name=row.room_name,
        topic=row.topic, substitute_instructor_id=row.substitute_instructor_id) for row, subject, classroom in rows]


def same_room(a, b):
    if a.get("room_id") and b.get("room_id"):
        return a["room_id"] == b["room_id"]
    return bool(name_key(a.get("room_name"))) and name_key(a.get("room_name")) == name_key(b.get("room_name"))


def conflict_pair(candidate, peer):
    if candidate["session_date"] != peer["session_date"]:
        return None
    # Unknown duration occupies the remaining day conservatively; never assume it is free.
    if candidate["start_time"] >= (peer["end_time"] or "24:00") or peer["start_time"] >= (candidate["end_time"] or "24:00"):
        return None
    common = {row["user_id"] for row in candidate["staff"]} & {row["user_id"] for row in peer["staff"]}
    reasons = (["class"] if candidate["class_id"] == peer["class_id"] else []) + (["instructor"] if common else []) + (["room"] if same_room(candidate, peer) else [])
    if not reasons:
        return None
    return {"candidate": candidate, "existing": peer, "reasons": reasons,
        "instructors": [row for row in candidate["staff"] if row["user_id"] in common],
        "unknown_duration": not candidate["end_time"] or not peer["end_time"]}


def find_conflicts(db, candidates):
    if not candidates:
        return []
    days = [row["session_date"] for row in candidates]
    excluded = {row["session_id"] for row in candidates if row["session_id"] is not None}
    existing = [row for row in existing_sessions(db, date.fromisoformat(min(days)), date.fromisoformat(max(days))) if row["session_id"] not in excluded]
    pairs = []
    for index, candidate in enumerate(candidates):
        for peer in existing + candidates[:index]:
            conflict = conflict_pair(candidate, peer)
            if conflict:
                pairs.append(conflict)
    return pairs


def enforce_conflicts(db, candidates, user, data, action, *, reviewed=False):
    conflicts = find_conflicts(db, candidates)
    if not conflicts:
        return []
    can_override = bool(user_roles(db, user) & {"TRAINING_MANAGER", "MANAGER"})
    encoded = json.dumps([user.user_id, action, getattr(data, "expected_version", None), candidates, conflicts], sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    token = hmac.new(SECRET_KEY.encode(), encoded, hashlib.sha256).hexdigest()
    acknowledged = data.acknowledge_conflicts and (reviewed or (data.conflict_token and hmac.compare_digest(data.conflict_token, token)))
    if not can_override or not acknowledged:
        raise HTTPException(409, {"code": "schedule_conflict", "message": "Lịch bị trùng. Chưa lưu thay đổi; hãy điều chỉnh hoặc yêu cầu Quản lý đào tạo xác nhận ghi đè.",
            "conflicts": conflicts, "can_override": can_override, "conflict_token": token if can_override else None})
    db.add(ScheduleConflictOverride(action=action, actor_id=user.user_id, actor_name=user.full_name,
        conflicts=conflicts, created_at=clock.utcnow()))
    return conflicts


def assignment_candidates(db, assignments):
    result = []
    for subject, main, assistants in assignments:
        current_assistants = {row[0] for row in db.query(ClassSubjectAssistant.user_id).filter_by(class_subject_id=subject.id).with_for_update()}
        if subject.instructor_id == main and current_assistants == set(assistants):
            continue
        classroom = db.get(TrainingClass, subject.class_id)
        if classroom.status == "cancelled":
            continue
        for row in db.query(ClassSession).filter_by(class_subject_id=subject.id, status="scheduled").order_by(ClassSession.session_id).populate_existing().with_for_update():
            result.append(session_snapshot(db, subject, classroom, session_id=row.session_id, session_date=row.session_date,
                start_time=row.start_time, end_time=row.end_time, room_id=row.room_id, room_name=row.room_name, topic=row.topic,
                substitute_instructor_id=row.substitute_instructor_id, instructor_id=main, assistant_ids=assistants))
    return result
