from datetime import time

from app.core.security import hash_password
from app.models.course import Course
from app.models.notification import Notification, NotificationOutbox
from app.models.role import Role
from app.models.training_class import ClassEnrollment, TrainingClass
from app.models.user import User
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


def _headers(client, user):
    response = client.post("/api/auth/login", json={
        "email": user.email, "password": "CorrectPassword123!",
    })
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_manager_announcement_deduplicates_recipients_tracks_reads_and_queues_email(client, test_db):
    manager = _user(test_db, "announcement_manager", "manager")
    teacher = _user(test_db, "announcement_teacher", "teacher")
    shared_student = _user(test_db, "announcement_shared", "student")
    second_student = _user(test_db, "announcement_second", "student")
    classes = []
    for code in ("ANN-A", "ANN-B"):
        course = Course(code=f"{code}-COURSE", name=code, department="IT", credits=3)
        test_db.add(course)
        test_db.flush()
        training_class = TrainingClass(code=code, name=code, course_id=course.id,
            teacher_id=teacher.id, room="R1", term="2026-Fall", day_of_week=1,
            start_time=time(9), end_time=time(11), capacity=20, status="active")
        test_db.add(training_class)
        test_db.flush()
        classes.append(training_class)
    test_db.add_all([
        ClassEnrollment(class_id=classes[0].id, student_id=shared_student.id),
        ClassEnrollment(class_id=classes[1].id, student_id=shared_student.id),
        ClassEnrollment(class_id=classes[1].id, student_id=second_student.id),
    ])
    test_db.commit()

    manager_headers = _headers(client, manager)
    teacher_headers = _headers(client, teacher)
    path = "/api/announcements"
    payload = {"class_ids": [item.id for item in classes], "title": "Schedule update", "body": "Room change tomorrow."}
    denied = client.post(path, headers=teacher_headers, json=payload)
    assert denied.status_code == 403
    invalid_duplicate = client.post(path, headers=manager_headers,
        json={**payload, "class_ids": [classes[0].id, classes[0].id]})
    assert invalid_duplicate.status_code == 422
    created = client.post(path, headers=manager_headers, json=payload)
    assert created.status_code == 201
    assert created.json()["recipient_count"] == 2
    assert test_db.query(Notification).filter_by(announcement_id=created.json()["id"]).count() == 2
    assert test_db.query(NotificationOutbox).count() == 2

    shared_headers = _headers(client, shared_student)
    notification_page = client.get("/api/notifications", headers=shared_headers)
    item = notification_page.json()["items"][0]
    assert item["announcement_id"] == created.json()["id"]
    assert item["navigation_url"] == f"/announcements/{created.json()['id']}"
    assert item["message"] == payload["body"]
    read = client.post(f"/api/notifications/{item['id']}/read", headers=shared_headers)
    assert read.status_code == 200

    count_path = f"/api/announcements/{created.json()['id']}/read-count"
    assert client.get(count_path, headers=manager_headers).json() == {
        "announcement_id": created.json()["id"], "recipient_count": 2,
        "read_count": 1, "unread_count": 1,
    }
    assert client.get(count_path, headers=teacher_headers).status_code == 403

    sent = []
    report = process_class_reminders(test_db, email_sender=lambda email, title, body: sent.append(email), send_emails=True)
    assert report["sent"] == 2
    assert set(sent) == {shared_student.email, second_student.email}
    assert all(item.status == "sent" and item.attempt_count == 1
        for item in test_db.query(NotificationOutbox).all())


def test_announcement_unknown_class_is_rejected_without_partial_rows(client, test_db):
    manager = _user(test_db, "announcement_missing_manager", "manager")
    response = client.post("/api/announcements", headers=_headers(client, manager), json={
        "class_ids": [987654], "title": "Test", "body": "No recipients",
    })
    assert response.status_code == 404
    assert test_db.query(Notification).count() == 0
    assert test_db.query(NotificationOutbox).count() == 0
