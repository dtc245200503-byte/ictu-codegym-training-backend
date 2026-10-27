from datetime import date, time, timedelta

from app.core.security import hash_password
from app.models.attendance import AttendanceRiskState, AttendanceSession
from app.models.course import Course
from app.models.notification import Notification, NotificationOutbox
from app.models.role import Role
from app.models.study_program import StudyProgram
from app.models.training_class import ClassEnrollment, TrainingClass
from app.models.user import User
from app.services.attendance_risk import attendance_warning


def test_attendance_warning_uses_floor_and_all_boundary_states():
    expected = {
        0: "normal",
        1: "near_threshold",
        2: "at_threshold",
        3: "over_threshold",
        8: "over_threshold",
    }
    for absent_count, state in expected.items():
        result = attendance_warning(absent_count, 10, 20)
        assert result["warning_status"] == state
        assert result["allowed_absences"] == 2


def test_attendance_warning_handles_zero_allowance_and_no_eligible_sessions():
    assert attendance_warning(0, 10, 0)["warning_status"] == "normal"
    zero_allowance = attendance_warning(1, 10, 0)
    assert zero_allowance["warning_status"] == "over_threshold"
    assert zero_allowance["allowed_absences"] == 0
    assert attendance_warning(1, 0, 20) == {
        "warning_status": "insufficient_data",
        "allowed_absences": None,
        "remaining_allowed_absences": None,
    }


def _user(db, name, role_code):
    role = db.query(Role).filter_by(code=role_code).first()
    if role is None:
        role = Role(code=role_code, name=role_code.title())
        db.add(role)
        db.flush()
    user = User(username=name, email=f"{name}@example.edu.vn", full_name=name,
        hashed_password=hash_password("CorrectPassword123!"), roles=[role])
    db.add(user)
    db.flush()
    return user


def _headers(client, user):
    result = client.post("/api/auth/login", json={
        "email": user.email, "password": "CorrectPassword123!",
    })
    assert result.status_code == 200
    return {"Authorization": f"Bearer {result.json()['access_token']}"}


def test_attendance_risk_activation_persists_and_notifies_assigned_teacher_and_manager(client, test_db):
    manager = _user(test_db, "risk_manager", "manager")
    teacher = _user(test_db, "risk_teacher", "teacher")
    unassigned_teacher = _user(test_db, "risk_unassigned_teacher", "teacher")
    student = _user(test_db, "risk_student", "student")
    program = StudyProgram(code="RISK-PROG", name="Risk Program", total_credits=120)
    course = Course(code="RISK-COURSE", name="Risk Course", department="IT", credits=3)
    test_db.add_all([program, course])
    test_db.flush()
    training_class = TrainingClass(code="RISK-CLASS", name="Risk Class", course_id=course.id,
        program_id=program.id, teacher_id=teacher.id, room="R1", term="2026-Fall",
        day_of_week=1, start_time=time(9), end_time=time(11), capacity=10, status="active")
    test_db.add(training_class)
    test_db.flush()
    test_db.add(ClassEnrollment(class_id=training_class.id, student_id=student.id))
    sessions = [AttendanceSession(class_id=training_class.id,
        session_date=date.today() - timedelta(days=2 - i), start_time=time(9), end_time=time(11),
        teacher_id=teacher.id, room="R1") for i in range(2)]
    test_db.add_all(sessions)
    test_db.commit()
    manager_headers = _headers(client, manager)
    student_headers = _headers(client, student)

    present = client.put(f"/api/attendance/sessions/{sessions[0].id}", headers=manager_headers,
        json={"records": [{"student_id": student.id, "status": "present"}]})
    assert present.status_code == 200
    state = test_db.query(AttendanceRiskState).filter_by(class_id=training_class.id, student_id=student.id).one()
    assert state.is_at_risk is False
    assert test_db.query(Notification).filter(Notification.title == "Cảnh báo nguy cơ nghỉ học").count() == 0

    absent = client.put(f"/api/attendance/sessions/{sessions[1].id}", headers=manager_headers,
        json={"records": [{"student_id": student.id, "status": "unexcused_absent"}]})
    assert absent.status_code == 200
    test_db.refresh(state)
    assert state.is_at_risk is True
    assert state.absent_count == 1 and state.completed_session_count == 2
    assert state.activation_count == 1 and state.flagged_at is not None
    warnings = test_db.query(Notification).filter(Notification.title == "Cảnh báo nguy cơ nghỉ học").all()
    assert {item.user_id for item in warnings} == {manager.id, teacher.id}
    assert test_db.query(NotificationOutbox).filter(NotificationOutbox.dedupe_key.like("attendance-risk:%")).count() == 2
    profile_risk = client.get("/api/students/me/attendance-risk", headers=student_headers)
    assert profile_risk.status_code == 200
    assert profile_risk.json()[0]["class_id"] == training_class.id
    manager_profile = client.get(f"/api/students/{student.id}", headers=_headers(client, manager))
    assert manager_profile.json()["attendance_risks"][0]["class_id"] == training_class.id
    assert client.get(f"/api/students/{student.id}", headers=_headers(client, unassigned_teacher)).status_code == 403

    client.put(f"/api/attendance/sessions/{sessions[1].id}", headers=manager_headers,
        json={"records": [{"student_id": student.id, "status": "unexcused_absent"}]})
    assert test_db.query(Notification).filter(Notification.title == "Cảnh báo nguy cơ nghỉ học").count() == 2

    cleared = client.put(f"/api/attendance/sessions/{sessions[1].id}", headers=manager_headers,
        json={"records": [{"student_id": student.id, "status": "present"}]})
    assert cleared.status_code == 200
    test_db.refresh(state)
    assert state.is_at_risk is False and state.cleared_at is not None
    assert client.get("/api/students/me/attendance-risk", headers=student_headers).json() == []


def test_policy_change_recalculates_persisted_risk_and_queues_transition(client, test_db):
    manager = _user(test_db, "risk_policy_manager", "manager")
    teacher = _user(test_db, "risk_policy_teacher", "teacher")
    student = _user(test_db, "risk_policy_student", "student")
    program = StudyProgram(code="RISK-POLICY", name="Risk Policy", total_credits=120,
        absence_threshold_percent=100)
    course = Course(code="RISK-POLICY-COURSE", name="Risk Policy Course", department="IT", credits=3)
    test_db.add_all([program, course])
    test_db.flush()
    training_class = TrainingClass(code="RISK-POLICY-CLASS", name="Risk Policy Class", course_id=course.id,
        program_id=program.id, teacher_id=teacher.id, room="R1", term="2026-Fall",
        day_of_week=1, start_time=time(9), end_time=time(11), capacity=10, status="active")
    test_db.add(training_class)
    test_db.flush()
    test_db.add(ClassEnrollment(class_id=training_class.id, student_id=student.id))
    sessions = [AttendanceSession(class_id=training_class.id,
        session_date=date.today() - timedelta(days=2 - i), start_time=time(9), end_time=time(11),
        teacher_id=teacher.id, room="R1") for i in range(2)]
    test_db.add_all(sessions)
    test_db.commit()
    manager_headers = _headers(client, manager)

    for session, status in zip(sessions, ("present", "unexcused_absent")):
        result = client.put(f"/api/attendance/sessions/{session.id}", headers=manager_headers,
            json={"records": [{"student_id": student.id, "status": status}]})
        assert result.status_code == 200
    state = test_db.query(AttendanceRiskState).filter_by(class_id=training_class.id, student_id=student.id).one()
    assert state.is_at_risk is False
    assert test_db.query(Notification).filter(Notification.title == "Cảnh báo nguy cơ nghỉ học").count() == 0

    changed = client.put(f"/api/study-programs/{program.id}/attendance-policy", headers=manager_headers,
        json={"absence_threshold_percent": 40})
    assert changed.status_code == 200
    test_db.refresh(state)
    assert state.is_at_risk is True
    assert state.absence_threshold_percent == 40
    assert test_db.query(Notification).filter(Notification.title == "Cảnh báo nguy cơ nghỉ học").count() == 2
