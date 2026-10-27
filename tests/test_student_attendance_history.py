from datetime import date, time, timedelta

from app.core.security import hash_password
from app.models.attendance import AttendanceRecord, AttendanceSession
from app.models.course import Course
from app.models.role import Role
from app.models.training_class import ClassEnrollment, TrainingClass
from app.models.user import User


def make_user(db, role_code: str, suffix: str) -> User:
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


def make_class(db, teacher: User, code: str) -> TrainingClass:
    course = Course(
        code=f"{code}-COURSE",
        name=f"{code} Course",
        department="IT",
        credits=3,
        session_count=3,
    )
    db.add(course)
    db.flush()
    training_class = TrainingClass(
        code=code,
        name=code,
        course_id=course.id,
        teacher_id=teacher.id,
        room="ROOM-1",
        term="2026-Fall",
        day_of_week=0,
        start_time=time(9, 0),
        end_time=time(11, 0),
        capacity=20,
        status="active",
    )
    db.add(training_class)
    db.commit()
    db.refresh(training_class)
    return training_class


def login_headers(client, user: User) -> dict[str, str]:
    response = client.post(
        "/api/auth/login",
        json={"email": user.email, "password": "CorrectPassword123!"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_student_attendance_history_is_self_scoped_and_paginated(client, test_db):
    student = make_user(test_db, "student", "my_attendance")
    other_student = make_user(test_db, "student", "other_attendance")
    teacher = make_user(test_db, "teacher", "my_attendance")
    training_class = make_class(test_db, teacher, "MY-ATTENDANCE")
    test_db.add(
        ClassEnrollment(class_id=training_class.id, student_id=student.id)
    )
    other_class = make_class(test_db, teacher, "OTHER-ATTENDANCE")
    test_db.add(
        ClassEnrollment(class_id=other_class.id, student_id=other_student.id)
    )

    older = AttendanceSession(
        class_id=training_class.id,
        session_date=date.today() - timedelta(days=10),
        start_time=time(9, 0),
        end_time=time(11, 0),
        teacher_id=teacher.id,
        room="ROOM-1",
    )
    recent = AttendanceSession(
        class_id=training_class.id,
        session_date=date.today() - timedelta(days=8),
        start_time=time(9, 0),
        end_time=time(11, 0),
        teacher_id=teacher.id,
        room="ROOM-1",
    )
    cancelled = AttendanceSession(
        class_id=training_class.id,
        session_date=date.today() - timedelta(days=7),
        start_time=time(9, 0),
        end_time=time(11, 0),
        teacher_id=teacher.id,
        room="ROOM-1",
        is_cancelled=True,
    )
    future = AttendanceSession(
        class_id=training_class.id,
        session_date=date.today() + timedelta(days=1),
        start_time=time(9, 0),
        end_time=time(11, 0),
        teacher_id=teacher.id,
        room="ROOM-1",
    )
    test_db.add_all([older, recent, cancelled, future])
    test_db.commit()
    test_db.add(
        AttendanceRecord(
            session_id=older.id,
            student_id=student.id,
            marked_by=teacher.id,
            status="present",
        )
    )
    test_db.add(
        AttendanceRecord(
            session_id=recent.id,
            student_id=student.id,
            marked_by=teacher.id,
            status="not_recorded",
        )
    )
    test_db.add(
        AttendanceRecord(
            session_id=older.id,
            student_id=other_student.id,
            marked_by=teacher.id,
            status="unexcused_absent",
        )
    )
    test_db.commit()

    response = client.get(
        "/api/students/me/attendance-history?page=1&page_size=1",
        headers=login_headers(client, student),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["pages"] == 2
    assert body["items"][0]["session_id"] == recent.id
    assert body["items"][0]["status"] is None
    assert body["items"][0]["class_id"] == training_class.id
    assert body["totals"] == {
        "present_count": 1,
        "late_count": 0,
        "absent_count": 0,
        "unrecorded_count": 1,
        "total_sessions": 2,
    }
    assert body["attendance_rate"] == 50.0
    assert body["classes"][0]["warning_status"] == "normal"
    assert body["classes"][0]["absence_threshold_percent"] == 20.0
    assert body["classes"][0]["allowed_absences"] == 0
    assert all(item["class_id"] != other_class.id for item in body["items"])

    class_filtered = client.get(
        f"/api/students/me/attendance-history?class_id={training_class.id}",
        headers=login_headers(client, student),
    )
    assert class_filtered.status_code == 200
    assert class_filtered.json()["total"] == 2
    assert {item["class_id"] for item in class_filtered.json()["items"]} == {
        training_class.id
    }
    inaccessible_class = client.get(
        f"/api/students/me/attendance-history?class_id={other_class.id}",
        headers=login_headers(client, student),
    )
    assert inaccessible_class.status_code == 200
    assert inaccessible_class.json()["items"] == []
    assert inaccessible_class.json()["classes"] == []

    second_page = client.get(
        "/api/students/me/attendance-history?page=2&page_size=1",
        headers=login_headers(client, student),
    )
    assert second_page.status_code == 200
    assert second_page.json()["items"][0]["session_id"] == older.id


def test_student_attendance_history_rejects_non_student_role(client, test_db):
    teacher = make_user(test_db, "teacher", "history_access")

    response = client.get(
        "/api/students/me/attendance-history",
        headers=login_headers(client, teacher),
    )

    assert response.status_code == 403
