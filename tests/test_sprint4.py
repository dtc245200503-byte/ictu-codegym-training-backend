from datetime import date, datetime, time, timedelta

import pytest

from app.core.security import hash_password
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
    override_audit = test_db.query(AuditLog).filter_by(
        actor_id=manager.id, action="schedule.generated"
    ).one()
    assert f"class_id={first.id}" in override_audit.details
    assert "overridden=True" in override_audit.details


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
    filtered_schedule = client.get(
        f"/api/me/schedule?start_date={date.today().isoformat()}&end_date={(date.today() + timedelta(days=7)).isoformat()}&class_id={own_class.id}",
        headers=headers,
    )
    assert filtered_schedule.status_code == 200
    assert [item["class_id"] for item in filtered_schedule.json()] == [own_class.id]
    empty_schedule = client.get(
        f"/api/me/schedule?start_date={date.today().isoformat()}&end_date={(date.today() + timedelta(days=7)).isoformat()}&class_id={other_class.id}",
        headers=headers,
    )
    assert empty_schedule.status_code == 200
    assert empty_schedule.json() == []


def test_schedule_queries_filter_class_teacher_room_and_open_authorized_detail(client, test_db):
    manager = make_user(test_db, "manager", "schedule-filters")
    teacher_one = make_user(test_db, "teacher", "schedule-filter-one")
    teacher_two = make_user(test_db, "teacher", "schedule-filter-two")
    class_one = make_class(test_db, teacher_one, "S4-FILTER-A", "ROOM-A")
    class_two = make_class(test_db, teacher_two, "S4-FILTER-B", "ROOM-B")
    target_date = date.today() + timedelta(days=3)
    session_one = make_session(test_db, class_one, target_date)
    session_two = make_session(test_db, class_two, target_date)
    headers = login_headers(client, manager)
    base = f"/api/schedule/sessions?start_date={target_date.isoformat()}&end_date={target_date.isoformat()}"

    all_sessions = client.get(base, headers=headers)
    assert all_sessions.status_code == 200
    assert {row["id"] for row in all_sessions.json()} == {session_one.id, session_two.id}
    by_class = client.get(f"{base}&class_id={class_one.id}", headers=headers)
    by_teacher = client.get(f"{base}&teacher_id={teacher_two.id}", headers=headers)
    by_room = client.get(f"{base}&room=ROOM-A", headers=headers)
    assert [row["id"] for row in by_class.json()] == [session_one.id]
    assert [row["id"] for row in by_teacher.json()] == [session_two.id]
    assert [row["id"] for row in by_room.json()] == [session_one.id]
    detail = client.get(f"/api/schedule/sessions/{session_two.id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["class_id"] == class_two.id
    unmatched_room = client.get(f"{base}&room=UNKNOWN", headers=headers)
    assert unmatched_room.status_code == 200
    assert unmatched_room.json() == []


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


def test_suspend_and_withdraw_take_effect_on_date_and_are_audited_in_history(client, test_db):
    manager = make_user(test_db, "manager", "lifecycle-actions")
    teacher = make_user(test_db, "teacher", "lifecycle-actions")
    effective_date = date.today() + timedelta(days=2)
    cases = []
    for action in ("suspend", "withdraw"):
        student = make_user(test_db, "student", f"lifecycle-{action}")
        training_class = make_class(test_db, teacher, f"S4-{action.upper()}")
        test_db.add(ClassEnrollment(class_id=training_class.id, student_id=student.id))
        test_db.commit()
        response = client.post("/api/student-enrollment-changes",
            headers=login_headers(client, manager), json={
                "student_id": student.id, "class_id": training_class.id,
                "action": action, "effective_date": effective_date.isoformat(),
                "reason": f"Approved {action} request",
            })
        assert response.status_code == 200
        assert response.json()["action"] == action
        assert response.json()["reason"] == f"Approved {action} request"
        session = make_session(test_db, training_class, effective_date + timedelta(days=1))
        roster = client.get(f"/api/attendance/sessions/{session.id}",
            headers=login_headers(client, manager))
        assert roster.status_code == 200
        assert student.id not in {entry["id"] for entry in roster.json()["students"]}
        cases.append(student.id)
    history = client.get(f"/api/students/{cases[0]}/enrollment-history",
        headers=login_headers(client, manager))
    assert history.status_code == 200
    assert history.json()[0]["action"] == "suspend"
    assert history.json()[0]["created_at"]


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


def test_sprint3_4_enhancements(client, test_db):
    manager = make_user(test_db, "manager", "enhancer")
    teacher = make_user(test_db, "teacher", "enhancer_t")
    student = make_user(test_db, "student", "enhancer_s")
    manager_headers = login_headers(client, manager)
    student_headers = login_headers(client, student)

    # 1. Test class session progress and class filters
    training_class = make_class(test_db, teacher, code="ENH-CLASS", room="ENH-ROOM")
    training_class.status = "preparing"
    test_db.commit()
    session = make_session(
        test_db,
        training_class,
        date.today() + timedelta(days=1),
    )
    test_db.add(
        ClassEnrollment(
            class_id=training_class.id,
            student_id=student.id,
            enrollment_status="active",
        )
    )
    test_db.commit()

    # Verify GET /classes filter by status and teacher_id
    classes_res = client.get(
        f"/api/classes?status=preparing&teacher_id={teacher.id}",
        headers=manager_headers,
    )
    assert classes_res.status_code == 200
    matched = [c for c in classes_res.json() if c["id"] == training_class.id]
    assert len(matched) == 1
    assert matched[0]["total_sessions"] == 1
    assert "session_progress" in matched[0]

    # 2. Test GET /schedule/sessions/{session_id} directly
    session_res = client.get(
        f"/api/schedule/sessions/{session.id}",
        headers=student_headers,
    )
    assert session_res.status_code == 200
    assert session_res.json()["id"] == session.id
    assert session_res.json()["class_code"] == training_class.code

    # 3. Test Lead callback reminders and overdue flag
    from app.models.lead import Lead, LeadActivity
    lead = Lead(
        full_name="Lead Callback Test",
        email="lead.cb@example.edu.vn",
        phone="0912345678",
        status="contacted",
    )
    test_db.add(lead)
    test_db.flush()
    # Add an overdue activity
    from datetime import timezone
    past_due = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
    test_db.add(
        LeadActivity(
            lead_id=lead.id,
            message="Gọi lại khách hàng",
            scheduled_for=past_due,
        )
    )
    test_db.commit()

    admissions = make_user(test_db, "admissions", "enhancer_adm")
    adm_headers = login_headers(client, admissions)
    leads_res = client.get(f"/api/leads?q=Lead Callback Test", headers=adm_headers)
    assert leads_res.status_code == 200
    found_lead = leads_res.json()["items"][0]
    assert found_lead["has_overdue_callback"] is True


def test_auto_schedule_60_sessions_exceeding_syllabus_lessons(client, test_db):
    manager = make_user(test_db, "manager", "s60_mgr")
    teacher = make_user(test_db, "teacher", "s60_tch")
    # make_class creates a course with 3 lessons
    training_class = make_class(test_db, teacher, "S60-CLASS", "S60-ROOM")
    headers = login_headers(client, manager)

    # Generate 60 sessions for a class with only 3 syllabus lessons
    payload = {
        "weekdays": [0, 2, 4],  # Mon, Wed, Fri
        "start_date": (date.today() + timedelta(days=1)).isoformat(),
        "session_count": 60,
    }
    preview = client.post(
        f"/api/classes/{training_class.id}/sessions/preview",
        headers=headers,
        json=payload,
    )
    assert preview.status_code == 200
    data = preview.json()
    assert data["session_count"] == 60
    assert len(data["sessions"]) == 60
    # First 3 sessions link to syllabus lessons
    assert data["sessions"][0]["lesson_id"] is not None
    assert data["sessions"][1]["lesson_id"] is not None
    assert data["sessions"][2]["lesson_id"] is not None
    # Subsequent sessions link to thematic sequence
    assert data["sessions"][3]["lesson_id"] is None
    assert data["sessions"][3]["lesson_sequence"] == 4
    assert data["sessions"][59]["lesson_sequence"] == 60

    # Save all 60 sessions
    create_res = client.post(
        f"/api/classes/{training_class.id}/sessions",
        headers=headers,
        json=payload,
    )
    assert create_res.status_code == 201
    assert create_res.json()["created_count"] == 60


def test_schedule_conflicts_scanning_endpoint(client, test_db):
    manager = make_user(test_db, "manager", "scan_mgr")
    teacher = make_user(test_db, "teacher", "scan_tch")
    c1 = make_class(test_db, teacher, "C1-SCAN", "ROOM-101")
    c2 = make_class(test_db, teacher, "C2-SCAN", "ROOM-101")

    # Create 2 conflicting sessions manually for same teacher and same room on same day/time
    session_date = date.today() + timedelta(days=2)
    s1 = AttendanceSession(
        class_id=c1.id,
        session_date=session_date,
        start_time=time(9, 0),
        end_time=time(11, 0),
        teacher_id=teacher.id,
        room="ROOM-101",
    )
    s2 = AttendanceSession(
        class_id=c2.id,
        session_date=session_date,
        start_time=time(10, 0),
        end_time=time(12, 0),
        teacher_id=teacher.id,
        room="ROOM-101",
    )
    test_db.add_all([s1, s2])
    test_db.commit()

    headers = login_headers(client, manager)
    conflicts_res = client.get(
        f"/api/schedule/conflicts?start_date={session_date.isoformat()}&end_date={session_date.isoformat()}",
        headers=headers,
    )
    assert conflicts_res.status_code == 200
    data = conflicts_res.json()
    assert data["total_conflicts"] >= 1
    assert data["teacher_conflicts"] >= 1
    assert data["room_conflicts"] >= 1
    first_conflict = data["conflicts"][0]
    assert first_conflict["conflict_type"] == "both"
    assert first_conflict["status"] == "unresolved"
    too_wide = client.get(
        f"/api/schedule/conflicts?start_date={session_date.isoformat()}&end_date={(session_date + timedelta(days=91)).isoformat()}",
        headers=headers,
    )
    assert too_wide.status_code == 422


def test_student_profile_detail_and_classes_program_filter(client, test_db):
    admin = make_user(test_db, "admin", "std_admin")
    student = make_user(test_db, "student", "std_profile")
    headers = login_headers(client, admin)

    # 1. Test GET /students/{student_id}
    res = client.get(f"/api/students/{student.id}", headers=headers)
    assert res.status_code == 200
    profile_data = res.json()
    assert profile_data["id"] == student.id
    assert "enrollments" in profile_data
    assert "enrollment_history" in profile_data
    assert "attendance_stats" in profile_data
    assert profile_data["attendance_stats"]["attendance_rate"] == 100.0

    # 2. Test GET /classes?program_id=...
    from app.models.study_program import StudyProgram, program_subjects
    prog = StudyProgram(
        code="PROG-S4",
        name="Study Program S4",
        degree_level="Đại học",
        total_credits=120,
    )
    test_db.add(prog)
    test_db.flush()

    teacher = make_user(test_db, "teacher", "prog_tch")
    class_prog = make_class(test_db, teacher, "CLS-PROG", "ROOM-P")
    # attach class course to program
    test_db.execute(
        program_subjects.insert().values(
            program_id=prog.id,
            subject_id=class_prog.course_id,
            order_index=1,
        )
    )
    test_db.commit()

    classes_res = client.get(f"/api/classes?program_id={prog.id}", headers=headers)
    assert classes_res.status_code == 200
    items = classes_res.json()
    assert any(c["id"] == class_prog.id for c in items)


@pytest.mark.parametrize(
    "old_status",
    ["present", "late", "excused_absent", "unexcused_absent"],
)
def test_bulk_attendance_can_clear_one_student_without_touching_other_records(
    client, test_db, old_status
):
    manager = make_user(test_db, "manager", f"clear_{old_status}")
    teacher = make_user(test_db, "teacher", f"clear_{old_status}")
    target, control = [
        make_user(test_db, "student", f"clear_{old_status}_{index}")
        for index in range(2)
    ]
    training_class = make_class(test_db, teacher, f"CLEAR-{old_status}")
    test_db.add_all(
        [
            ClassEnrollment(class_id=training_class.id, student_id=target.id),
            ClassEnrollment(class_id=training_class.id, student_id=control.id),
        ]
    )
    test_db.commit()
    target_session = make_session(
        test_db, training_class, date.today() - timedelta(days=1)
    )
    other_session = make_session(
        test_db, training_class, date.today() - timedelta(days=2)
    )
    test_db.add_all(
        [
            AttendanceRecord(
                session_id=target_session.id,
                student_id=target.id,
                marked_by=teacher.id,
                status=old_status,
            ),
            AttendanceRecord(
                session_id=target_session.id,
                student_id=control.id,
                marked_by=teacher.id,
                status="absent",
            ),
            AttendanceRecord(
                session_id=other_session.id,
                student_id=target.id,
                marked_by=teacher.id,
                status="late",
            ),
        ]
    )
    test_db.commit()

    response = client.put(
        f"/api/attendance/sessions/{target_session.id}",
        headers=login_headers(client, manager),
        json={"records": [{"student_id": target.id, "status": None}]},
    )

    assert response.status_code == 200
    assert response.json() == {"session_id": target_session.id, "updated_count": 1}
    assert test_db.query(AttendanceRecord).filter_by(
        session_id=target_session.id, student_id=target.id
    ).one_or_none() is None
    assert test_db.query(AttendanceRecord).filter_by(
        session_id=target_session.id, student_id=control.id
    ).one().status == "absent"
    assert test_db.query(AttendanceRecord).filter_by(
        session_id=other_session.id, student_id=target.id
    ).one().status == "late"

    enrollment = test_db.query(ClassEnrollment).filter_by(
        class_id=training_class.id, student_id=target.id
    ).one()
    assert enrollment.attendance_status == "not_recorded"
    assert enrollment.last_attendance_at is None

    session_data = client.get(
        f"/api/attendance/sessions/{target_session.id}",
        headers=login_headers(client, manager),
    ).json()
    target_row = next(row for row in session_data["students"] if row["id"] == target.id)
    assert target_row["status"] is None

    matrix = client.get(
        f"/api/classes/{training_class.id}/attendance/summary",
        headers=login_headers(client, manager),
    )
    assert matrix.status_code == 200
    target_summary = next(
        row for row in matrix.json()["students"] if row["id"] == target.id
    )
    assert target_summary["unrecorded_count"] == 1
    assert target_summary["absent_count"] == 0
    control_summary = next(
        row for row in matrix.json()["students"] if row["id"] == control.id
    )
    assert control_summary["legacy_absent_count"] == 1
    assert control_summary["absent_count"] == 1


def test_bulk_attendance_clear_is_idempotent_for_missing_record(client, test_db):
    manager = make_user(test_db, "manager", "clear_missing")
    teacher = make_user(test_db, "teacher", "clear_missing")
    student = make_user(test_db, "student", "clear_missing")
    training_class = make_class(test_db, teacher, "CLEAR-MISSING")
    test_db.add(ClassEnrollment(class_id=training_class.id, student_id=student.id))
    test_db.commit()
    session = make_session(test_db, training_class, date.today() - timedelta(days=1))

    response = client.put(
        f"/api/attendance/sessions/{session.id}",
        headers=login_headers(client, manager),
        json={"records": [{"student_id": student.id, "status": None}]},
    )

    assert response.status_code == 200
    assert response.json() == {"session_id": session.id, "updated_count": 0}
    assert test_db.query(AttendanceRecord).filter_by(session_id=session.id).count() == 0


def test_bulk_attendance_clear_respects_access_and_rejects_not_recorded_value(
    client, test_db
):
    teacher = make_user(test_db, "teacher", "clear_access_owner")
    other_teacher = make_user(test_db, "teacher", "clear_access_other")
    student = make_user(test_db, "student", "clear_access_student")
    training_class = make_class(test_db, teacher, "CLEAR-ACCESS")
    test_db.add(ClassEnrollment(class_id=training_class.id, student_id=student.id))
    test_db.commit()
    session = make_session(test_db, training_class, date.today())
    record = AttendanceRecord(
        session_id=session.id,
        student_id=student.id,
        marked_by=teacher.id,
        status="present",
    )
    test_db.add(record)
    test_db.commit()

    forbidden = client.put(
        f"/api/attendance/sessions/{session.id}",
        headers=login_headers(client, other_teacher),
        json={"records": [{"student_id": student.id, "status": None}]},
    )
    invalid_status = client.put(
        f"/api/attendance/sessions/{session.id}",
        headers=login_headers(client, teacher),
        json={"records": [{"student_id": student.id, "status": "not_recorded"}]},
    )
    missing_session = client.put(
        "/api/attendance/sessions/999999",
        headers=login_headers(client, teacher),
        json={"records": [{"student_id": student.id, "status": None}]},
    )

    assert forbidden.status_code == 403
    assert invalid_status.status_code == 422
    assert missing_session.status_code == 404
    assert test_db.query(AttendanceRecord).filter_by(id=record.id).one().status == "present"
