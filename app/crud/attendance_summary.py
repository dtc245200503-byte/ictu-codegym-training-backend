"""Read-only class attendance matrix with effective enrollment dates."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from fractions import Fraction

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import load_only

from app.crud.leads import user_roles
from app.models.attendance import ClassSession, SessionAttendance, StudentClass
from app.models.student_enrollment import StudentEnrollment
from app.models.subject import ClassSubject, Subject
from app.models.training_program import TrainingClass
from app.models.user import User

STATUSES = {"present", "late", "excused_absence", "unexcused_absence"}
STAFF_ROLES = {"TRAINING_MANAGER", "MANAGER", "ADMIN", "INSTRUCTOR", "TEACHER"}


def today():
    return (datetime.now(timezone.utc) + timedelta(hours=7)).date()


def class_scope(db, user):
    query = db.query(TrainingClass)
    if not user_roles(db, user) & {"TRAINING_MANAGER", "MANAGER", "ADMIN"}:
        assigned = db.query(ClassSubject.class_id).filter(ClassSubject.instructor_id == user.user_id)
        substitutes = db.query(ClassSubject.class_id).join(ClassSession, ClassSession.class_subject_id == ClassSubject.id).filter(
            ClassSession.substitute_instructor_id == user.user_id, ClassSession.status != "cancelled")
        query = query.filter(or_(TrainingClass.class_id.in_(assigned), TrainingClass.class_id.in_(substitutes)))
    return query


def summary(db, user, class_id, sort, page=1, page_size=50):
    classroom = class_scope(db, user).filter(TrainingClass.class_id == class_id).first()
    if classroom is None:
        raise HTTPException(404, "Không tìm thấy lớp học hoặc bạn không được phân công.")
    day = today()
    sessions = db.query(ClassSession, Subject.subject_name).options(load_only(
        ClassSession.session_id, ClassSession.session_date, ClassSession.start_time, ClassSession.topic, ClassSession.status)).join(
        ClassSubject, ClassSubject.id == ClassSession.class_subject_id).join(Subject, Subject.id == ClassSubject.subject_id).filter(
        ClassSubject.class_id == class_id).order_by(ClassSession.session_date, ClassSession.start_time, ClassSession.session_id).all()
    records = db.query(SessionAttendance).join(ClassSession, ClassSession.session_id == SessionAttendance.session_id).join(
        ClassSubject, ClassSubject.id == ClassSession.class_subject_id).filter(ClassSubject.class_id == class_id).all()
    by_cell = {(row.student_id, row.session_id): row for row in records}
    snapshots = {}
    dates = {session.session_id: (session.session_date, session.start_time, session.session_id) for session, _ in sessions}
    for row in sorted(records, key=lambda row: dates[row.session_id]):
        snapshots[row.student_id] = row
    periods = defaultdict(list)
    for period in db.query(StudentEnrollment).filter_by(class_id=class_id):
        periods[period.student_id].append(period)
    legacy = {row.student_id: row for row in db.query(StudentClass).filter_by(class_id=class_id)}
    ids = set(periods) | set(legacy) | set(snapshots)
    users = {person.user_id: person for person in db.query(User).filter(User.user_id.in_(ids))}
    rows = []
    for student_id in ids:
        person, snapshot = users.get(student_id), snapshots.get(student_id)
        if person is None and snapshot is None:
            continue
        student_periods, enrollment = periods.get(student_id, []), legacy.get(student_id)

        def enrolled(on_date):
            if student_periods:
                return any((period.started_on is None or period.started_on <= on_date) and
                           (period.ended_on is None or on_date < period.ended_on) for period in student_periods)
            return enrollment is not None and (enrollment.enrolled_date is None or enrollment.enrolled_date <= on_date)

        current = enrolled(day)
        upcoming = any(period.started_on and period.started_on > day for period in student_periods) if student_periods else bool(
            enrollment and enrollment.enrolled_date and enrollment.enrolled_date > day)
        counts = dict.fromkeys(STATUSES, 0)
        cells = []
        for session, _ in sessions:
            record = by_cell.get((student_id, session.session_id))
            # Formal membership wins over archived attendance outside its interval.
            # Deleted accounts without enrollment retain their recorded history.
            eligible = enrolled(session.session_date) if student_periods or enrollment else record is not None
            if not eligible:
                state = "not_enrolled"
            elif session.status == "cancelled":
                state = "cancelled"
            elif session.session_date > day:
                state = "upcoming"
            else:
                state = record.status if record and record.status in STATUSES else "unmarked"
            if state in counts:
                counts[state] += 1
            cells.append(state)
        recorded = sum(counts.values())
        attended = counts["present"] + counts["late"]
        rows.append({"student_id": student_id, "student_name": person.full_name if person else snapshot.student_name,
                     "student_code": person.username if person else snapshot.student_code,
                     "membership": "active" if current else "upcoming" if upcoming else "former" if student_periods or enrollment else "historical",
                     "cells": cells, "recorded_sessions": recorded, "attended_sessions": attended,
                     "absent_sessions": counts["excused_absence"] + counts["unexcused_absence"],
                     "excused_absences": counts["excused_absence"], "unexcused_absences": counts["unexcused_absence"],
                     "attendance_rate": round(attended * 100 / recorded, 2) if recorded else None})
    name_key = lambda row: (row["student_name"].casefold(), row["student_code"], row["student_id"])
    if sort == "rate_asc":
        rows.sort(key=lambda row: (row["recorded_sessions"] == 0,
            Fraction(row["attended_sessions"], row["recorded_sessions"]) if row["recorded_sessions"] else Fraction(0), *name_key(row)))
    else:
        rows.sort(key=name_key)
    total = len(rows)
    # Internal exports request the complete, globally sorted matrix in one read.
    if page_size is None:
        page, page_size = 1, max(1, total)
    page = min(page, max(1, (total + page_size - 1) // page_size))
    return {"class_id": classroom.class_id, "class_name": classroom.class_name,
            "class_code": classroom.class_code or f"LOP-{classroom.class_id}",
            "today": day, "sort": sort, "students": rows[(page - 1) * page_size:page * page_size],
            "total": total, "page": page, "page_size": page_size,
            "sessions": [{"session_id": session.session_id, "session_date": session.session_date,
                          "start_time": session.start_time.strftime("%H:%M"), "subject_name": subject_name,
                          "topic": session.topic, "status": session.status,
                          "upcoming": session.session_date > day} for session, subject_name in sessions]}
