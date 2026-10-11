import base64

from app.core.security import hash_password, verify_password
from app.models.lead import Lead, LeadActivity
from app.models.notification import Notification
from app.models.role import Role
from app.models.student_profile import StudentProfile
from app.models.user import User


def make_user(db, role_code):
    role = db.query(Role).filter(Role.code == role_code).one_or_none()
    if role is None:
        role = Role(code=role_code, name=role_code.title())
        db.add(role)
        db.flush()
    user = User(
        username=f"{role_code}_user",
        email=f"{role_code}@example.edu.vn",
        full_name=f"{role_code.title()} User",
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
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_current_user_can_update_own_profile(client, test_db):
    student = make_user(test_db, "student")
    headers = login_headers(client, student)

    response = client.patch(
        "/api/me",
        headers=headers,
        json={
            "full_name": "  Nguyen Van Student  ",
            "phone": "  0901234567 ",
            "birth_date": "2004-03-02",
            "address": "  Thai Nguyen ",
        },
    )

    assert response.status_code == 200
    assert response.json()["full_name"] == "Nguyen Van Student"
    assert response.json()["phone"] == "0901234567"
    assert response.json()["birth_date"] == "2004-03-02"
    assert response.json()["address"] == "Thai Nguyen"
    assert test_db.get(User, student.id).full_name == "Nguyen Van Student"


def test_profile_rejects_blank_name_and_requires_authentication(client, test_db):
    user = make_user(test_db, "student")
    headers = login_headers(client, user)

    assert client.patch(
        "/api/me", headers=headers, json={"full_name": "   "}
    ).status_code == 422
    assert client.get("/api/me").status_code == 401


def test_missing_avatar_returns_no_content_and_uploaded_avatar_can_be_read_and_removed(client, test_db):
    user = make_user(test_db, "student")
    headers = login_headers(client, user)
    image = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a7L8AAAAASUVORK5CYII="
    )

    missing_avatar = client.get("/api/me/avatar", headers=headers)
    assert missing_avatar.status_code == 204
    assert missing_avatar.content == b""

    response = client.put(
        "/api/me/avatar",
        headers=headers,
        json={
            "content_type": "image/png",
            "data_base64": base64.b64encode(image).decode("ascii"),
        },
    )
    assert response.status_code == 200
    assert client.get("/api/me/avatar", headers=headers).content == image
    assert client.get("/api/me/avatar", headers=headers).headers["content-type"] == "image/png"
    assert client.delete("/api/me/avatar", headers=headers).status_code == 204
    assert client.get("/api/me/avatar", headers=headers).status_code == 204


def test_avatar_upload_rejects_mismatched_image_type(client, test_db):
    user = make_user(test_db, "student")
    response = client.put(
        "/api/me/avatar",
        headers=login_headers(client, user),
        json={
            "content_type": "image/png",
            "data_base64": base64.b64encode(b"<svg></svg>").decode("ascii"),
        },
    )
    assert response.status_code == 422
    assert test_db.query(User).filter(User.id == user.id).one()


def test_public_lead_submission_and_admissions_management(client, test_db):
    response = client.post(
        "/api/leads",
        json={
            "full_name": "  Nguyen Thi A ",
            "email": "applicant@example.edu.vn",
            "phone": " 0901234567 ",
            "interested_program": "  Công nghệ thông tin ",
            "message": "  Xin tư vấn học phí ",
        },
    )
    assert response.status_code == 201
    created = response.json()
    assert created["full_name"] == "Nguyen Thi A"
    assert created["phone"] == "0901234567"
    assert created["interested_program"] == "Công nghệ thông tin"
    assert created["status"] == "new"
    assert test_db.query(Lead).count() == 1

    admissions = make_user(test_db, "admissions")
    headers = login_headers(client, admissions)
    listed = client.get("/api/leads?status=new&q=applicant", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == created["id"]

    updated = client.patch(
        f"/api/leads/{created['id']}",
        headers=headers,
        json={"status": "contacted", "notes": "Đã gọi lần đầu."},
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "contacted"
    assert updated.json()["notes"] == "Đã gọi lần đầu."
    assert test_db.get(Lead, created["id"]).updated_by_id == admissions.id
    for next_status in ("consulting", "trial_scheduled", "closed"):
        progressed = client.patch(
            f"/api/leads/{created['id']}",
            headers=headers,
            json={"status": next_status},
        )
        assert progressed.status_code == 200
        assert progressed.json()["status"] == next_status
    rejected_without_reason = client.patch(
        f"/api/leads/{created['id']}",
        headers=headers,
        json={"status": "rejected"},
    )
    assert rejected_without_reason.status_code == 422
    rejected = client.patch(
        f"/api/leads/{created['id']}",
        headers=headers,
        json={"status": "rejected", "rejection_reason": "Không còn nhu cầu."},
    )
    assert rejected.status_code == 200
    assert rejected.json()["rejection_reason"] == "Không còn nhu cầu."


def test_lead_care_journal_conversion_and_student_profile_management(client, test_db):
    lead_response = client.post(
        "/api/leads",
        json={
            "full_name": "New Applicant",
            "email": "new.applicant@example.edu.vn",
            "phone": "0901234567",
            "interested_program": "Công nghệ thông tin",
        },
    )
    assert lead_response.status_code == 201
    lead_id = lead_response.json()["id"]
    admissions = make_user(test_db, "admissions")
    headers = login_headers(client, admissions)

    initial_activities = client.get(
        f"/api/leads/{lead_id}/activities", headers=headers
    )
    assert initial_activities.status_code == 200
    assert "trực tuyến" in initial_activities.json()[0]["message"]

    journal = client.post(
        f"/api/leads/{lead_id}/activities",
        headers=headers,
        json={"message": "Đã tư vấn chương trình và học phí."},
    )
    assert journal.status_code == 201
    assert journal.json()["actor_user_id"] == admissions.id
    make_user(test_db, "student")

    not_ready = client.post(f"/api/leads/{lead_id}/convert", headers=headers)
    assert not_ready.status_code == 409

    qualified = client.patch(
        f"/api/leads/{lead_id}",
        headers=headers,
        json={"status": "qualified"},
    )
    assert qualified.status_code == 200
    converted = client.post(f"/api/leads/{lead_id}/convert", headers=headers)
    assert converted.status_code == 200
    result = converted.json()
    assert result["created"] is True
    assert result["initial_password"] == "123456"
    student_id = result["student"]["id"]
    student = test_db.get(User, student_id)
    assert student.student_code.startswith("SV")
    assert student.must_change_password is True
    assert student.hashed_password.startswith("$argon2id$")
    assert verify_password("123456", student.hashed_password)
    assert test_db.query(StudentProfile).filter_by(user_id=student_id).one().study_program == "Công nghệ thông tin"
    assert test_db.query(LeadActivity).filter_by(lead_id=lead_id).count() >= 4
    login = client.post(
        "/api/auth/login",
        json={"identifier": student.username, "password": "123456"},
    )
    assert login.status_code == 200
    assert login.json()["user"]["must_change_password"] is True

    repeated = client.post(f"/api/leads/{lead_id}/convert", headers=headers)
    assert repeated.status_code == 200
    assert repeated.json()["created"] is False
    assert repeated.json()["student"]["id"] == student_id

    directory = client.get("/api/students?q=New%20Applicant", headers=headers)
    assert directory.status_code == 200
    assert directory.json()["total"] == 1
    assert directory.json()["items"][0]["lead_id"] == lead_id

    updated_profile = client.patch(
        f"/api/students/{student_id}",
        headers=headers,
        json={
            "full_name": "New Applicant Updated",
            "phone": "0900000000",
            "birth_date": "2005-02-03",
            "address": "Thai Nguyen",
            "study_program": "Kỹ thuật phần mềm",
        },
    )
    assert updated_profile.status_code == 200
    assert updated_profile.json()["study_program"] == "Kỹ thuật phần mềm"


def test_lead_assignment_and_callback_create_notifications(client, test_db):
    lead_response = client.post(
        "/api/leads",
        json={
            "full_name": "Callback Applicant",
            "email": "callback@example.edu.vn",
            "phone": "0901234567",
        },
    )
    lead_id = lead_response.json()["id"]
    advisor = make_user(test_db, "admissions")
    advisor.username = "admissions_advisor"
    advisor.email = "admissions.advisor@example.edu.vn"
    test_db.commit()
    operator = User(
        username="admissions_operator",
        email="admissions.operator@example.edu.vn",
        full_name="Admissions Operator",
        hashed_password=hash_password("CorrectPassword123!"),
        roles=advisor.roles,
    )
    test_db.add(operator)
    test_db.commit()
    headers = login_headers(client, operator)

    assigned = client.patch(
        f"/api/leads/{lead_id}",
        headers=headers,
        json={"assigned_to_user_id": advisor.id},
    )
    assert assigned.status_code == 200
    assert assigned.json()["assigned_to_user_id"] == advisor.id
    assignment_notification = test_db.query(Notification).filter_by(
        user_id=advisor.id, lead_id=lead_id
    ).one()
    assert assignment_notification.title == "Lead được giao cho bạn"

    scheduled = client.post(
        f"/api/leads/{lead_id}/activities",
        headers=headers,
        json={
            "message": "Gọi lại theo lịch hẹn.",
            "scheduled_for": "2030-04-05T09:30:00",
        },
    )
    assert scheduled.status_code == 201
    assert scheduled.json()["activity_type"] == "call"
    assert scheduled.json()["scheduled_for"].startswith("2030-04-05T09:30")
    callback_notification = (
        test_db.query(Notification)
        .filter_by(user_id=advisor.id, lead_id=lead_id)
        .order_by(Notification.id.desc())
        .first()
    )
    assert callback_notification.title == "Đã đặt lịch gọi lại"
    assert callback_notification.scheduled_for.isoformat().startswith("2030-04-05T09:30")
    notifications = client.get("/api/notifications", headers=login_headers(client, advisor))
    assert notifications.status_code == 200
    assert any(item["scheduled_for"] for item in notifications.json()["items"])


def test_lead_submission_validation_and_role_protection(client, test_db):
    invalid = client.post(
        "/api/leads",
        json={"full_name": "Applicant", "email": "not-an-email", "phone": "123"},
    )
    assert invalid.status_code == 422

    student = make_user(test_db, "student")
    headers = login_headers(client, student)
    assert client.get("/api/leads", headers=headers).status_code == 403

    created = client.post(
        "/api/leads",
        json={
            "full_name": "Applicant",
            "email": "valid@example.edu.vn",
            "phone": "0901234567",
        },
    )
    assert created.status_code == 201
    assert client.patch(
        f"/api/leads/{created.json()['id']}",
        headers=headers,
        json={"status": "not-a-status"},
    ).status_code == 403
