from datetime import datetime, timedelta, time

from app.core.security import hash_password
from app.core.config import settings
from app.models.attendance import AttendanceSession
from app.models.course import Course
from app.models.notification import Notification, NotificationOutbox
from app.models.role import Role
from app.models.student_profile import StudentProfile
from app.models.training_class import ClassEnrollment, TrainingClass
from app.models.user import User
from app.services.attendance_summary import HO_CHI_MINH
from app.services.class_reminders import process_class_reminders


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


def test_class_reminder_scheduler_is_deduplicated_respects_optout_and_skips_cancelled(client, test_db):
    teacher = _user(test_db, "reminder_teacher", "teacher")
    enabled_student = _user(test_db, "reminder_enabled", "student")
    opted_out_student = _user(test_db, "reminder_optout", "student")
    test_db.add(StudentProfile(user_id=opted_out_student.id, class_reminder_opt_out=True))
    course = Course(code="REM101", name="Reminder", department="IT", credits=3)
    test_db.add(course)
    test_db.flush()
    active_class = TrainingClass(code="REM101-A", name="Reminder active", course_id=course.id,
        teacher_id=teacher.id, room="R1", term="2026-Fall", day_of_week=1,
        start_time=time(9), end_time=time(11), capacity=10, status="active")
    cancelled_class = TrainingClass(code="REM101-C", name="Reminder cancelled", course_id=course.id,
        teacher_id=teacher.id, room="R2", term="2026-Fall", day_of_week=1,
        start_time=time(13), end_time=time(15), capacity=10, status="active")
    test_db.add_all([active_class, cancelled_class])
    test_db.flush()
    test_db.add_all([
        ClassEnrollment(class_id=active_class.id, student_id=enabled_student.id),
        ClassEnrollment(class_id=active_class.id, student_id=opted_out_student.id),
        ClassEnrollment(class_id=cancelled_class.id, student_id=enabled_student.id),
    ])
    now = datetime.now(HO_CHI_MINH).replace(second=0, microsecond=0)
    upcoming = now + timedelta(hours=10)
    canceled = AttendanceSession(class_id=cancelled_class.id, session_date=upcoming.date(),
        start_time=time(13), end_time=time(15), teacher_id=teacher.id, room="R2", is_cancelled=True)
    active = AttendanceSession(class_id=active_class.id, session_date=upcoming.date(),
        start_time=upcoming.time().replace(second=0, microsecond=0),
        end_time=(upcoming + timedelta(hours=2)).time().replace(second=0, microsecond=0),
        teacher_id=teacher.id, room="R1")
    test_db.add_all([active, canceled])
    test_db.commit()

    sent = []
    first = process_class_reminders(test_db, now=now, email_sender=lambda email, title, body: sent.append(email), send_emails=True)
    assert first["created"] == first["sent"] == 1
    assert first["skipped_opt_out"] == 1
    assert sent == [enabled_student.email]
    assert test_db.query(Notification).filter(Notification.title == "Nhắc lịch học sắp tới").count() == 1
    outbox = test_db.query(NotificationOutbox).one()
    assert outbox.status == "sent" and outbox.attempt_count == 1

    second = process_class_reminders(test_db, now=now + timedelta(minutes=1), email_sender=lambda *args: sent.append("duplicate"), send_emails=True)
    assert second["created"] == 0 and sent == [enabled_student.email]


def test_class_reminder_retries_three_times_then_fails(test_db):
    teacher = _user(test_db, "retry_teacher", "teacher")
    student = _user(test_db, "retry_student", "student")
    course = Course(code="RETRY101", name="Retry", department="IT", credits=3)
    test_db.add(course)
    test_db.flush()
    training_class = TrainingClass(code="RETRY101-A", name="Retry class", course_id=course.id,
        teacher_id=teacher.id, room="R1", term="2026-Fall", day_of_week=1,
        start_time=time(9), end_time=time(11), capacity=10, status="active")
    test_db.add(training_class)
    test_db.flush()
    test_db.add(ClassEnrollment(class_id=training_class.id, student_id=student.id))
    now = datetime.now(HO_CHI_MINH).replace(second=0, microsecond=0)
    starts = now + timedelta(hours=8)
    test_db.add(AttendanceSession(class_id=training_class.id, session_date=starts.date(),
        start_time=starts.time(), end_time=(starts + timedelta(hours=2)).time(), teacher_id=teacher.id))
    test_db.commit()

    def fail(*args):
        raise RuntimeError("temporary SMTP error")

    for attempt in range(3):
        process_class_reminders(test_db, now=now + timedelta(minutes=attempt), email_sender=fail, send_emails=True)
    outbox = test_db.query(NotificationOutbox).one()
    assert outbox.status == "failed" and outbox.attempt_count == 3


def test_student_can_change_class_reminder_optout(client, test_db):
    student = _user(test_db, "preference_student", "student")
    token = client.post("/api/auth/login", json={"email": student.email, "password": "CorrectPassword123!"}).json()["access_token"]
    response = client.put("/api/me/class-reminder-preferences",
        headers={"Authorization": f"Bearer {token}"}, json={"class_reminder_opt_out": True})
    assert response.status_code == 200 and response.json()["class_reminder_opt_out"] is True
    assert test_db.get(StudentProfile, student.id).class_reminder_opt_out is True


def test_session_learning_materials_are_private_to_enrolled_students(client, test_db, tmp_path, monkeypatch):
    teacher = _user(test_db, "material_teacher", "teacher")
    student = _user(test_db, "material_student", "student")
    outsider = _user(test_db, "material_outsider", "student")
    course = Course(code="MAT101", name="Materials", department="IT", credits=3)
    test_db.add(course)
    test_db.flush()
    training_class = TrainingClass(code="MAT101-A", name="Materials class", course_id=course.id,
        teacher_id=teacher.id, room="R1", term="2026-Fall", day_of_week=1,
        start_time=time(9), end_time=time(11), capacity=10, status="active")
    test_db.add(training_class)
    test_db.flush()
    test_db.add(ClassEnrollment(class_id=training_class.id, student_id=student.id))
    session = AttendanceSession(class_id=training_class.id, session_date=datetime.now(HO_CHI_MINH).date() + timedelta(days=7),
        start_time=time(9), end_time=time(11), teacher_id=teacher.id)
    test_db.add(session)
    test_db.commit()
    monkeypatch.setattr(settings, "PRIVATE_FILE_STORAGE_DIR", str(tmp_path / "private"))
    teacher_token = client.post("/api/auth/login", json={"email": teacher.email, "password": "CorrectPassword123!"}).json()["access_token"]
    student_token = client.post("/api/auth/login", json={"email": student.email, "password": "CorrectPassword123!"}).json()["access_token"]
    outsider_token = client.post("/api/auth/login", json={"email": outsider.email, "password": "CorrectPassword123!"}).json()["access_token"]
    path = f"/api/sessions/{session.id}/materials"
    uploaded = client.post(path, headers={"Authorization": f"Bearer {teacher_token}",
        "X-File-Name": "lesson.pdf", "Content-Type": "application/pdf"}, content=b"%PDF-1.7\nlesson")
    assert uploaded.status_code == 201
    assert uploaded.json()["uploaded_by"] == teacher.id and uploaded.json()["created_at"]
    second_upload = client.post(path, headers={"Authorization": f"Bearer {teacher_token}",
        "X-File-Name": "worksheet.txt", "Content-Type": "text/plain"}, content=b"worksheet")
    assert second_upload.status_code == 201
    assert client.get(path, headers={"Authorization": f"Bearer {student_token}"}).status_code == 403
    link_path = f"{path}/links"
    assert client.post(link_path, headers={"Authorization": f"Bearer {teacher_token}"},
        json={"title": "Lesson reference", "url": "http://example.edu.vn/lesson"}).status_code == 422
    created_link = client.post(link_path, headers={"Authorization": f"Bearer {teacher_token}"},
        json={"title": "Lesson reference", "url": "https://example.edu.vn/lesson"})
    assert created_link.status_code == 201
    assert client.get(link_path, headers={"Authorization": f"Bearer {student_token}"}).status_code == 403
    opened_early = client.patch(f"/api/sessions/{session.id}/materials/visibility",
        headers={"Authorization": f"Bearer {teacher_token}"}, json={"materials_open_early": True})
    assert opened_early.status_code == 200 and opened_early.json()["materials_open_early"] is True
    listed = client.get(path, headers={"Authorization": f"Bearer {student_token}"})
    assert listed.status_code == 200 and len(listed.json()) == 2
    first_page = client.get(f"{path}?page=1&page_size=1", headers={"Authorization": f"Bearer {student_token}"})
    second_page = client.get(f"{path}?page=2&page_size=1", headers={"Authorization": f"Bearer {student_token}"})
    assert len(first_page.json()) == len(second_page.json()) == 1
    assert first_page.headers["x-total-count"] == "2"
    assert first_page.headers["x-has-next"] == "true"
    assert second_page.headers["x-has-previous"] == "true"
    assert first_page.json()[0]["id"] != second_page.json()[0]["id"]
    listed_links = client.get(link_path, headers={"Authorization": f"Bearer {student_token}"})
    assert listed_links.status_code == 200 and listed_links.json()[0]["url"] == "https://example.edu.vn/lesson"
    assert client.get(link_path, headers={"Authorization": f"Bearer {outsider_token}"}).status_code == 404
    assert client.get(uploaded.json()["download_url"], headers={"Authorization": f"Bearer {student_token}"}).status_code == 200
    assert client.get(uploaded.json()["download_url"], headers={"Authorization": f"Bearer {outsider_token}"}).status_code == 404
