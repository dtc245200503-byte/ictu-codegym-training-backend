from datetime import date, time, timedelta

from app.core.security import hash_password
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
from app.models.role import Role
from app.models.student_enrollment_history import StudentEnrollmentHistory
from app.models.training_class import ClassEnrollment, TrainingClass
from app.models.user import User


def make_user(db, role_code, suffix):
    role = db.query(Role).filter_by(code=role_code).one_or_none()
    if role is None:
        role = Role(code=role_code, name=role_code.title())
        db.add(role)
        db.flush()
    user = User(
        username=f"{role_code}.{suffix}",
        email=f"{role_code}.{suffix}@example.edu.vn",
        full_name=f"{role_code.title()} {suffix}",
        hashed_password=hash_password("CorrectPassword123!"),
        roles=[role],
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def login_headers(client, user):
    response = client.post(
        "/api/auth/login",
        json={"email": user.email, "password": "CorrectPassword123!"},
    )
    assert response.status_code == 200
    return {"Authorization": "Bearer " + response.json()["access_token"]}


def make_class(db, teacher, code="S4-CLASS", room="S4-ROOM", weekday=None, start="09:00", end="11:00"):
    course = Course(
        code=code + "-COURSE",
        name=code + " Course",
        department="IT",
        credits=3,
        session_count=3,
    )
    db.add(course)
    db.flush()
    db.add_all(
        [
            CourseLesson(course_id=course.id, sequence=index, title=f"Lesson {index}", objectives="Learn the topic")
            for index in range(1, 4)
        ]
    )
    training_class = TrainingClass(
        code=code,
        name=code,
        course_id=course.id,
        teacher_id=teacher.id,
        room=room,
        term="2026-Fall",
        day_of_week=weekday if weekday is not None else date.today().weekday(),
        start_time=time.fromisoformat(start),
        end_time=time.fromisoformat(end),
        capacity=20,
        status="active",
    )
    db.add(training_class)
    db.commit()
    db.refresh(training_class)
    return training_class


def make_session(db, training_class, session_date=None, start="09:00", end="11:00"):
    session = AttendanceSession(
        class_id=training_class.id,
        session_date=session_date or date.today(),
        start_time=time.fromisoformat(start),
        end_time=time.fromisoformat(end),
        teacher_id=training_class.teacher_id,
        room=training_class.room,
        lesson_id=db.query(CourseLesson).filter_by(course_id=training_class.course_id).order_by(CourseLesson.sequence).first().id,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def test_schedule_generation_previews_holidays_and_attaches_syllabus(client, test_db):
    manager = make_user(test_db, "manager", "schedule")
    teacher = make_user(test_db, "teacher", "schedule")
    training_class = make_class(test_db, teacher)
    first_day = date.today() + timedelta(days=(7 - date.today().weekday()) % 7)
    if first_day <= date.today():
        first_day += timedelta(days=7)
    test_db.add(
        AttendanceScheduleHoliday(
            holiday_date=first_day,
            name="Holiday",
            created_by=manager.id,
        )
    )
    test_db.commit()
    headers = login_headers(client, manager)
    payload = {
        "weekdays": [first_day.weekday()],
        "start_date": first_day.isoformat(),
        "session_count": 2,
    }
    preview = client.post(
        f"/api/classes/{training_class.id}/sessions/preview",
        headers=headers,
        json=payload,
    )
    assert preview.status_code == 200
    data = preview.json()
    assert data["session_count"] == 2
    assert data["sessions"][0]["session_date"] != first_day.isoformat()
    assert [item["lesson_sequence"] for item in data["sessions"]] == [1, 2]

    created = client.post(
        f"/api/classes/{training_class.id}/sessions",
        headers=headers,
        json=payload,
    )
    assert created.status_code == 201
    assert created.json()["created_count"] == 2
    assert test_db.query(AttendanceSession).filter_by(class_id=training_class.id).count() == 2


def test_schedule_conflicts_block_unless_manager_confirms_override(client, test_db):
    manager = make_user(test_db, "manager", "conflict")
    teacher = make_user(test_db, "teacher", "conflict")
    weekday = (date.today().weekday() + 1) % 7
    first = make_class(test_db, teacher, "S4-CONFLICT-A", "ROOM-A", weekday)
    make_class(test_db, teacher, "S4-CONFLICT-B", "ROOM-B", weekday)
    requested_day = date.today() + timedelta(days=(weekday - date.today().weekday()) % 7)
    if requested_day <= date.today():
        requested_day += timedelta(days=7)
    headers = login_headers(client, manager)
    payload = {
        "weekdays": [weekday],
        "start_date": requested_day.isoformat(),
        "session_count": 1,
    }
    preview = client.post(
        f"/api/classes/{first.id}/sessions/preview", headers=headers, json=payload
    )
    assert preview.status_code == 200
    assert preview.json()["conflict_count"] >= 1
    blocked = client.post(
        f"/api/classes/{first.id}/sessions", headers=headers, json=payload
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["override_required"] is True
    overridden = client.post(
        f"/api/classes/{first.id}/sessions",
        headers=headers,
        json={**payload, "allow_conflicts": True},
    )
    assert overridden.status_code == 201
    saved = test_db.query(AttendanceSession).filter_by(class_id=first.id).one()
    assert saved.conflict_override is True


def test_session_edits_require_cancel_reason_notify_and_lock_after_attendance(client, test_db):
    manager = make_user(test_db, "manager", "edit")
    teacher = make_user(test_db, "teacher", "edit")
    student = make_user(test_db, "student", "edit")
    training_class = make_class(test_db, teacher)
    test_db.add(ClassEnrollment(class_id=training_class.id, student_id=student.id))
    test_db.commit()
    session = make_session(test_db, training_class, date.today() + timedelta(days=2))
    headers = login_headers(client, manager)
    missing_reason = client.patch(
        f"/api/schedule/sessions/{session.id}",
        headers=headers,
        json={"is_cancelled": True},
    )
    assert missing_reason.status_code == 422
    moved = client.patch(
        f"/api/schedule/sessions/{session.id}",
        headers=headers,
        json={"room": "S4-NEW-ROOM", "cancellation_reason": "Moved"},
    )
    assert moved.status_code == 200
    assert moved.json()["room"] == "S4-NEW-ROOM"
    assert test_db.query(Notification).filter_by(user_id=student.id, class_id=training_class.id).count() == 1

    cancellation = client.patch(
        f"/api/schedule/sessions/{session.id}",
        headers=headers,
        json={"is_cancelled": True, "cancellation_reason": "Campus closed"},
    )
    assert cancellation.status_code == 200
    assert cancellation.json()["is_cancelled"] is True
    session.is_cancelled = False
    test_db.add(
        AttendanceRecord(session_id=session.id, student_id=student.id, status="present")
    )
    test_db.commit()
    locked = client.patch(
        f"/api/schedule/sessions/{session.id}",
        headers=headers,
        json={"room": "S4-LOCKED"},
    )
    assert locked.status_code == 409


def test_student_schedule_is_limited_to_enrolled_classes(client, test_db):
    student = make_user(test_db, "student", "calendar")
    teacher = make_user(test_db, "teacher", "calendar")
    own_class = make_class(test_db, teacher, "S4-OWN")
    other_class = make_class(test_db, teacher, "S4-OTHER", "OTHER", (date.today().weekday() + 2) % 7)
    test_db.add(ClassEnrollment(class_id=own_class.id, student_id=student.id))
    test_db.commit()
    make_session(test_db, own_class)
    make_session(test_db, other_class, date.today() + timedelta(days=1))
    headers = login_headers(client, student)
    schedule = client.get(
        f"/api/me/schedule?start_date={date.today().isoformat()}&end_date={(date.today() + timedelta(days=7)).isoformat()}",
        headers=headers,
    )
    assert schedule.status_code == 200
    assert [item["class_id"] for item in schedule.json()] == [own_class.id]


def test_student_lifecycle_effective_date_preserves_attendance_history(client, test_db):
    manager = make_user(test_db, "manager", "lifecycle")
    teacher = make_user(test_db, "teacher", "lifecycle")
    student = make_user(test_db, "student", "lifecycle")
    old_class = make_class(test_db, teacher, "S4-OLD")
    new_class = make_class(test_db, teacher, "S4-NEW", "NEW-ROOM", (date.today().weekday() + 2) % 7)
    enrollment = ClassEnrollment(class_id=old_class.id, student_id=student.id)
    test_db.add(enrollment)
    test_db.commit()
    old_session = make_session(test_db, old_class, date.today() + timedelta(days=2))
    test_db.add(
        AttendanceRecord(session_id=old_session.id, student_id=student.id, status="late")
    )
    test_db.commit()
    headers = login_headers(client, manager)
    transfer = client.post(
        "/api/student-enrollment-changes",
        headers=headers,
        json={
            "student_id": student.id,
            "class_id": old_class.id,
            "target_class_id": new_class.id,
            "action": "transfer",
            "effective_date": (date.today() + timedelta(days=1)).isoformat(),
            "reason": "Schedule change",
        },
    )
    assert transfer.status_code == 200
    assert test_db.query(StudentEnrollmentHistory).filter_by(student_id=student.id).count() == 1
    assert test_db.query(AttendanceRecord).filter_by(
        session_id=old_session.id, student_id=student.id
    ).one().status == "late"
    target_enrollment = test_db.query(ClassEnrollment).filter_by(
        class_id=new_class.id, student_id=student.id
    ).one()
    assert target_enrollment.effective_date == date.today() + timedelta(days=1)
    attendance = client.get(
        f"/api/attendance/sessions/{old_session.id}", headers=headers
    )
    assert attendance.status_code == 200
    assert student.id not in {
        item["id"] for item in attendance.json()["students"]
    }


def test_bulk_attendance_supports_four_states_and_logs_changes(client, test_db):
    manager = make_user(test_db, "manager", "attendance")
    teacher = make_user(test_db, "teacher", "attendance")
    students = [make_user(test_db, "student", f"attendance{index}") for index in range(4)]
    training_class = make_class(test_db, teacher)
    test_db.add_all(
        ClassEnrollment(class_id=training_class.id, student_id=student.id)
        for student in students
    )
    test_db.commit()
    session = make_session(test_db, training_class)
    teacher_headers = login_headers(client, teacher)
    payload = {
        "records": [
            {"student_id": students[0].id, "status": "present"},
            {"student_id": students[1].id, "status": "late"},
            {"student_id": students[2].id, "status": "excused_absent"},
            {"student_id": students[3].id, "status": "unexcused_absent"},
        ]
    }
    saved = client.put(
        f"/api/attendance/sessions/{session.id}", headers=teacher_headers, json=payload
    )
    assert saved.status_code == 200
    assert saved.json()["updated_count"] == 4
    assert {record.status for record in test_db.query(AttendanceRecord).all()} == {
        "present", "late", "excused_absent", "unexcused_absent",
    }
    assert test_db.query(AttendanceChangeLog).count() == 4
    session.session_date = date.today() - timedelta(days=1)
    test_db.commit()
    denied = client.put(
        f"/api/attendance/sessions/{session.id}",
        headers=teacher_headers,
        json=payload,
    )
    assert denied.status_code == 403
    allowed = client.put(
        f"/api/attendance/sessions/{session.id}",
        headers=login_headers(client, manager),
        json=payload,
    )
    assert allowed.status_code == 200
    assert test_db.query(AttendanceChangeLog).count() == 4


def test_absence_request_evidence_approval_updates_attendance_and_notifies(client, test_db):
    teacher = make_user(test_db, "teacher", "leave")
    substitute = make_user(test_db, "teacher", "substitute")
    student = make_user(test_db, "student", "leave")
    training_class = make_class(test_db, teacher)
    test_db.add(ClassEnrollment(class_id=training_class.id, student_id=student.id))
    test_db.commit()
    session = make_session(test_db, training_class, date.today() + timedelta(days=1))
    session.teacher_id = substitute.id
    test_db.commit()
    substitute_headers = login_headers(client, substitute)
    schedule = client.get(
        f"/api/schedule/sessions?start_date={date.today().isoformat()}&end_date={(date.today() + timedelta(days=7)).isoformat()}",
        headers=substitute_headers,
    )
    assert schedule.status_code == 200
    assert any(item["id"] == session.id for item in schedule.json())
    class_schedule = client.get(
        f"/api/classes/{training_class.id}/sessions", headers=substitute_headers
    )
    assert class_schedule.status_code == 200
    assert [item["id"] for item in class_schedule.json()] == [session.id]
    attendance_roster = client.get(
        f"/api/attendance/sessions/{session.id}", headers=substitute_headers
    )
    assert attendance_roster.status_code == 200
    requests = client.get(
        f"/api/classes/{training_class.id}/absence-requests",
        headers=substitute_headers,
    )
    assert requests.status_code == 200
    student_headers = login_headers(client, student)
    submitted = client.post(
        f"/api/attendance/sessions/{session.id}/absence-requests",
        headers=student_headers,
        json={
            "reason": "Medical appointment",
            "evidence_filename": "proof.pdf",
            "evidence_content_type": "application/pdf",
            "evidence_base64": "JVBERi0xLjQ=",
        },
    )
    assert submitted.status_code == 201
    request_id = submitted.json()["id"]
    evidence = client.get(
        f"/api/absence-requests/{request_id}/evidence", headers=student_headers
    )
    assert evidence.status_code == 200
    assert evidence.content == b"%PDF-1.4"

    teacher_headers = substitute_headers
    rejected_fields = client.patch(
        f"/api/absence-requests/{request_id}",
        headers=teacher_headers,
        json={"status": "approved", "decision_reason": " "},
    )
    assert rejected_fields.status_code == 422
    decision = client.patch(
        f"/api/absence-requests/{request_id}",
        headers=teacher_headers,
        json={"status": "approved", "decision_reason": "Verified."},
    )
    assert decision.status_code == 200
    attendance = test_db.query(AttendanceRecord).filter_by(
        session_id=session.id, student_id=student.id
    ).one()
    assert attendance.status == "excused_absent"
    assert test_db.query(Notification).filter_by(
        user_id=student.id, title="Đơn xin vắng đã được xử lý"
    ).count() == 1
    duplicate_decision = client.patch(
        f"/api/absence-requests/{request_id}",
        headers=teacher_headers,
        json={"status": "rejected", "decision_reason": "Already processed."},
    )
    assert duplicate_decision.status_code == 409
    assert test_db.query(AbsenceRequest).filter_by(id=request_id).one().status == "approved"
