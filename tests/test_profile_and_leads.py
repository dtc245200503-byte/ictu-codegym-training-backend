import base64
from io import BytesIO
from datetime import datetime, timedelta

from PIL import Image

from app.core.security import hash_password, verify_password
from app.core.config import settings
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


def test_self_profile_rejects_invalid_phone_and_protected_identity_fields(client, test_db):
    user = make_user(test_db, "student")
    headers = login_headers(client, user)
    for phone in ("123", "12345678901", "09ABCDEF12"):
        assert client.patch("/api/me", headers=headers, json={"phone": phone}).status_code == 422
    for protected in ({"email": "changed@example.edu.vn"}, {"role": "admin"}, {"roles": ["admin"]}):
        assert client.patch("/api/me", headers=headers, json=protected).status_code == 422
    unchanged = test_db.get(User, user.id)
    assert unchanged.email == user.email
    assert [role.code for role in unchanged.roles] == ["student"]
    valid = client.patch("/api/me", headers=headers, json={"phone": "+84901234567"})
    assert valid.status_code == 200 and valid.json()["phone"] == "+84901234567"


def test_profile_identity_and_role_error_support_role_navigation(client, test_db):
    student = make_user(test_db, "student")
    headers = login_headers(client, student)
    identity = client.get("/api/me", headers=headers)
    assert identity.status_code == 200
    assert identity.json()["full_name"] == student.full_name
    assert identity.json()["roles"] == ["student"]
    forbidden = client.get("/api/accounts", headers=headers)
    assert forbidden.status_code == 403
    assert "quyền" in forbidden.json()["detail"].lower()


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
    original_response = client.get("/api/me/avatar", headers=headers)
    assert original_response.headers["content-type"] == "image/png"
    with Image.open(BytesIO(original_response.content)) as processed:
        assert processed.size == (512, 512)
    thumbnail_response = client.get("/api/me/avatar?variant=thumbnail", headers=headers)
    with Image.open(BytesIO(thumbnail_response.content)) as thumbnail:
        assert thumbnail.size == (128, 128)
    assert thumbnail_response.headers["cache-control"] == "private, no-store"
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

    too_large = client.put("/api/me/avatar", headers=login_headers(client, user), json={
        "content_type": "image/png",
        "data_base64": base64.b64encode(b"x" * (2 * 1024 * 1024 + 1)).decode("ascii"),
    })
    assert too_large.status_code == 413

    corrupt_png = client.put("/api/me/avatar", headers=login_headers(client, user), json={
        "content_type": "image/png",
        "data_base64": base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"IHDR" + b"invalid" * 5).decode("ascii"),
    })
    assert corrupt_png.status_code == 422


def test_avatar_crop_coordinates_are_square_bounded_and_metadata_is_removed(client, test_db):
    user = make_user(test_db, "student")
    headers = login_headers(client, user)
    image = Image.new("RGB", (4, 2), "red")
    for x in range(2, 4):
        for y in range(2):
            image.putpixel((x, y), (0, 0, 255))
    source = BytesIO()
    image.save(source, format="PNG", pnginfo=None)
    uploaded = client.put("/api/me/avatar", headers=headers, json={
        "content_type": "image/png",
        "data_base64": base64.b64encode(source.getvalue()).decode("ascii"),
        "crop_x": 2, "crop_y": 0, "crop_size": 2,
    })
    assert uploaded.status_code == 200
    with Image.open(BytesIO(client.get("/api/me/avatar", headers=headers).content)) as result:
        assert result.size == (512, 512)
        assert result.getpixel((256, 256))[2] > 240

    outside_crop = client.put("/api/me/avatar", headers=headers, json={
        "content_type": "image/png",
        "data_base64": base64.b64encode(source.getvalue()).decode("ascii"),
        "crop_x": 3, "crop_y": 0, "crop_size": 2,
    })
    assert outside_crop.status_code == 422
    incomplete_crop = client.put("/api/me/avatar", headers=headers, json={
        "content_type": "image/png",
        "data_base64": base64.b64encode(source.getvalue()).decode("ascii"),
        "crop_x": 0,
    })
    assert incomplete_crop.status_code == 422


def test_avatar_jpeg_is_reencoded_without_exif_and_self_scoped(client, test_db):
    first = make_user(test_db, "student")
    second = make_user(test_db, "teacher")
    headers = login_headers(client, first)
    image = Image.new("RGB", (20, 10), "green")
    exif = Image.Exif()
    exif[270] = "private location note"
    source = BytesIO()
    image.save(source, format="JPEG", exif=exif)
    response = client.put("/api/me/avatar", headers=headers, json={
        "content_type": "image/jpeg",
        "data_base64": base64.b64encode(source.getvalue()).decode("ascii"),
    })
    assert response.status_code == 200
    own = client.get("/api/me/avatar", headers=headers)
    with Image.open(BytesIO(own.content)) as processed:
        assert processed.size == (512, 512)
        assert dict(processed.getexif()) == {}
    second_headers = login_headers(client, second)
    assert client.get("/api/me/avatar", headers=second_headers).status_code == 204


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
    assert response.json()["duplicate_phone_warning"] is False
    created = response.json()
    assert created["full_name"] == "Nguyen Thi A"
    assert created["phone"] == "0901234567"
    assert created["interested_program"] == "Công nghệ thông tin"
    assert created["status"] == "new"
    assert test_db.query(Lead).count() == 1


    admissions = make_user(test_db, "admissions")
    manager = make_user(test_db, "manager")
    assert client.post("/api/leads/bulk-assignment", headers=login_headers(client, manager), json={
        "lead_ids": [created["id"]], "assigned_to_user_id": admissions.id,
    }).status_code == 200
    headers = login_headers(client, admissions)
    listed = client.get("/api/leads?status=new&q=applicant", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["page_size"] == 20
    assert listed.json()["pages"] == 1
    assert listed.json()["has_previous"] is False
    assert listed.json()["has_next"] is False
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


def test_duplicate_lead_phone_warning_and_manager_only_delete(client, test_db):
    payload = {"full_name": "First Applicant", "email": "first.applicant@example.edu.vn", "phone": "0905550101"}
    first = client.post("/api/leads", json=payload)
    assert first.status_code == 201 and first.json()["duplicate_phone_warning"] is False
    duplicate = client.post("/api/leads", json={**payload, "full_name": "Second Applicant",
        "email": "second.applicant@example.edu.vn"})
    assert duplicate.status_code == 201 and duplicate.json()["duplicate_phone_warning"] is True
    admissions = make_user(test_db, "admissions")
    admissions_headers = login_headers(client, admissions)
    assert client.delete(f"/api/leads/{duplicate.json()['id']}", headers=admissions_headers).status_code == 403
    manager = make_user(test_db, "manager")
    deleted = client.delete(f"/api/leads/{duplicate.json()['id']}", headers=login_headers(client, manager))
    assert deleted.status_code == 204
    assert client.get(f"/api/leads/{duplicate.json()['id']}", headers=admissions_headers).status_code == 404


def test_public_lead_submission_rate_limit_is_configurable_and_persisted(client, test_db, monkeypatch):
    monkeypatch.setattr(settings, "LEAD_SUBMISSION_LIMIT", 2)
    monkeypatch.setattr(settings, "LEAD_SUBMISSION_WINDOW_SECONDS", 3600)
    for index in (1, 2):
        response = client.post("/api/leads", json={
            "full_name": f"Rate Limited Applicant {index}",
            "email": f"rate.limit.{index}@example.edu.vn", "phone": f"09055502{index:02d}",
        })
        assert response.status_code == 201
    blocked = client.post("/api/leads", json={
        "full_name": "Third Applicant", "email": "rate.limit.3@example.edu.vn", "phone": "0905550203",
    })
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) > 0
    assert test_db.query(Lead).count() == 2


def test_lead_bulk_assignment_history_and_consultant_object_scope(client, test_db):
    manager = make_user(test_db, "manager")
    advisor = make_user(test_db, "admissions")
    advisor2 = User(username="admissions_advisor_two", email="advisor.two@example.edu.vn",
        full_name="Advisor Two", hashed_password=hash_password("CorrectPassword123!"), roles=advisor.roles)
    test_db.add(advisor2)
    test_db.commit()
    manager_headers = login_headers(client, manager)
    advisor_headers = login_headers(client, advisor)
    leads = []
    for number in range(1, 4):
        response = client.post("/api/leads", json={"full_name": f"Scope Applicant {number}",
            "email": f"scope.{number}@example.edu.vn", "phone": f"09055503{number:02d}"})
        assert response.status_code == 201
        leads.append(response.json())

    assigned = client.post("/api/leads/bulk-assignment", headers=manager_headers, json={
        "lead_ids": [leads[0]["id"], leads[1]["id"]], "assigned_to_user_id": advisor.id,
    })
    assert assigned.status_code == 200 and assigned.json()["assigned_count"] == 2
    assigned_other = client.post("/api/leads/bulk-assignment", headers=manager_headers, json={
        "lead_ids": [leads[2]["id"]], "assigned_to_user_id": advisor2.id,
    })
    assert assigned_other.status_code == 200
    advisor_list = client.get("/api/leads", headers=advisor_headers)
    assert advisor_list.status_code == 200
    assert {item["id"] for item in advisor_list.json()["items"]} == {leads[0]["id"], leads[1]["id"]}
    assert client.get(f"/api/leads/{leads[2]['id']}", headers=advisor_headers).status_code == 404

    self_assignment = client.patch(f"/api/leads/{leads[2]['id']}", headers=advisor_headers,
        json={"assigned_to_user_id": advisor.id})
    assert self_assignment.status_code == 404
    reassigned = client.patch(f"/api/leads/{leads[0]['id']}", headers=manager_headers,
        json={"assigned_to_user_id": advisor2.id})
    assert reassigned.status_code == 200
    history = client.get(f"/api/leads/{leads[0]['id']}/assignment-history", headers=manager_headers)
    assert history.status_code == 200 and len(history.json()) == 2
    assert history.json()[0]["new_assignee_id"] == advisor2.id
    assert history.json()[1]["new_assignee_id"] == advisor.id
    assert client.get(f"/api/leads/{leads[0]['id']}", headers=advisor_headers).status_code == 404

    partial_missing = client.post("/api/leads/bulk-assignment", headers=manager_headers, json={
        "lead_ids": [leads[1]["id"], 999999], "assigned_to_user_id": advisor.id,
    })
    assert partial_missing.status_code == 404
    assert client.get(f"/api/leads/{leads[1]['id']}", headers=advisor_headers).status_code == 200


def test_lead_search_date_sort_pagination_and_assignment_filter_scope(client, test_db):
    manager = make_user(test_db, "manager")
    manager_headers = login_headers(client, manager)
    ids = []
    for name in ("Zeta Applicant", "Alpha Applicant", "Mike Applicant"):
        response = client.post("/api/leads", json={
            "full_name": name, "email": f"{name.split()[0].lower()}@example.edu.vn",
            "phone": f"09177{len(ids):06d}",
        })
        assert response.status_code == 201
        ids.append(response.json()["id"])

    page = client.get("/api/leads?q=Applicant&sort_by=full_name&sort_order=asc&page=1&page_size=2",
        headers=manager_headers)
    assert page.status_code == 200
    assert [row["full_name"] for row in page.json()["items"]] == ["Alpha Applicant", "Mike Applicant"]
    assert page.json()["total"] == 3 and page.json()["has_next"] is True
    second = client.get("/api/leads?q=Applicant&sort_by=full_name&sort_order=asc&page=2&page_size=2",
        headers=manager_headers)
    all_pages = page.json()["items"] + second.json()["items"]
    assert len({row["id"] for row in all_pages}) == 3
    assert [row["full_name"] for row in all_pages] == ["Alpha Applicant", "Mike Applicant", "Zeta Applicant"]
    assert second.json()["items"][0]["full_name"] == "Zeta Applicant"

    lower = (datetime.now() - timedelta(days=1)).isoformat()
    upper = (datetime.now() + timedelta(days=1)).isoformat()
    filtered = client.get(f"/api/leads?created_from={lower}&created_to={upper}&status=new",
        headers=manager_headers)
    assert filtered.status_code == 200, filtered.text
    assert filtered.json()["total"] == 3
    assert client.get("/api/leads?source=website", headers=manager_headers).json()["total"] == 3
    assert client.get(f"/api/leads?created_from={upper}&created_to={lower}",
        headers=manager_headers).status_code == 422

    advisor = make_user(test_db, "admissions")
    other = User(username="other_admissions", email="other.admissions@example.edu.vn",
        full_name="Other Admissions", hashed_password=hash_password("CorrectPassword123!"), roles=advisor.roles)
    test_db.add(other)
    test_db.commit()
    assert client.post("/api/leads/bulk-assignment", headers=manager_headers, json={
        "lead_ids": [ids[0]], "assigned_to_user_id": other.id,
    }).status_code == 200
    advisor_headers = login_headers(client, advisor)
    assert client.get(f"/api/leads?assigned_to_user_id={other.id}", headers=advisor_headers).status_code == 403
    assert client.get("/api/leads?assigned_to_user_id=1", headers=manager_headers).status_code == 200


def test_managed_lead_creation_source_duplicate_warning_and_role_scope(client, test_db):
    public = client.post("/api/leads", json={"full_name": "Existing Source Applicant",
        "email": "existing.source@example.edu.vn", "phone": "0917777788"})
    assert public.status_code == 201 and public.json()["source"] == "website"
    manager = make_user(test_db, "manager")
    manager_headers = login_headers(client, manager)
    denied = client.post("/api/leads/managed", headers=login_headers(client, make_user(test_db, "student")), json={
        "full_name": "Forbidden", "email": "forbidden@example.edu.vn", "phone": "0917777789",
    })
    assert denied.status_code == 403
    created = client.post("/api/leads/managed", headers=manager_headers, json={
        "full_name": "Referred Applicant", "email": "referred@example.edu.vn",
        "phone": "0917777788", "source": "  referral  ", "interested_program": "Software",
    })
    assert created.status_code == 201
    assert created.json()["source"] == "referral"
    assert created.json()["duplicate_phone_warning"] is True
    assert client.get("/api/leads?source=referral", headers=manager_headers).json()["total"] == 1
    admissions = make_user(test_db, "admissions")
    assert client.post("/api/leads/bulk-assignment", headers=manager_headers, json={
        "lead_ids": [created.json()["id"]], "assigned_to_user_id": admissions.id,
    }).status_code == 200
    admissions_headers = login_headers(client, admissions)
    updated = client.patch(f"/api/leads/{created.json()['id']}", headers=admissions_headers,
        json={"source": "event"})
    assert updated.status_code == 200 and updated.json()["source"] == "event"


def test_lead_funnel_counts_and_rejection_transition_audit(client, test_db):
    manager = make_user(test_db, "manager")
    headers = login_headers(client, manager)
    created = client.post("/api/leads", json={"full_name": "Funnel Applicant",
        "email": "funnel@example.edu.vn", "phone": "0917777777"})
    lead_id = created.json()["id"]
    assert client.get("/api/leads/funnel-summary", headers=headers).json()["counts"]["new"] == 1
    assert client.patch(f"/api/leads/{lead_id}", headers=headers, json={"status": "rejected"}).status_code == 422
    rejected = client.patch(f"/api/leads/{lead_id}", headers=headers,
        json={"status": "rejected", "rejection_reason": "Không phù hợp thời gian"})
    assert rejected.status_code == 200 and rejected.json()["rejection_reason"] == "Không phù hợp thời gian"
    activities = client.get(f"/api/leads/{lead_id}/activities", headers=headers)
    assert activities.status_code == 200
    assert any("new" in item["message"] and "rejected" in item["message"] for item in activities.json())
    counts = client.get("/api/leads/funnel-summary", headers=headers).json()["counts"]
    assert counts["new"] == 0 and counts["rejected"] == 1
    restored = client.patch(f"/api/leads/{lead_id}", headers=headers, json={"status": "contacted"})
    assert restored.status_code == 200 and restored.json()["rejection_reason"] is None


def test_lead_activity_types_callback_timezone_and_overdue_flag(client, test_db):
    admissions = make_user(test_db, "admissions")
    headers = login_headers(client, admissions)
    lead = client.post("/api/leads", json={"full_name": "Callback Types",
        "email": "callback.types@example.edu.vn", "phone": "0917777778"}).json()
    manager = make_user(test_db, "manager")
    assert client.post("/api/leads/bulk-assignment", headers=login_headers(client, manager), json={
        "lead_ids": [lead["id"]], "assigned_to_user_id": admissions.id,
    }).status_code == 200
    for activity_type in ("call", "message", "meeting"):
        response = client.post(f"/api/leads/{lead['id']}/activities", headers=headers, json={
            "message": f"Follow up by {activity_type}", "activity_type": activity_type,
            "scheduled_for": "2030-04-05T16:30:00+07:00",
        })
        assert response.status_code == 201, response.text
        assert response.json()["scheduled_for"].endswith("Z")
        assert response.json()["is_overdue"] is False
    first = test_db.query(LeadActivity).filter_by(lead_id=lead["id"]).order_by(LeadActivity.id).first()
    first.scheduled_for = datetime.now() - timedelta(days=1)
    test_db.commit()
    activities = client.get(f"/api/leads/{lead['id']}/activities", headers=headers)
    assert activities.status_code == 200
    overdue = next(row for row in activities.json() if row["id"] == first.id)
    assert overdue["is_overdue"] is True
    past = client.post(f"/api/leads/{lead['id']}/activities", headers=headers, json={
        "message": "Past callback", "scheduled_for": "2020-01-01T00:00:00Z",
    })
    assert past.status_code == 422


def test_lead_conversion_rolls_back_when_configured_credential_delivery_fails(client, test_db, monkeypatch):
    async def failed_delivery(*_args):
        raise RuntimeError("fake SMTP failure")
    monkeypatch.setattr("app.api.routes.leads.send_account_credentials_email", failed_delivery)
    for field, value in (("MAIL_USERNAME", "fake"), ("MAIL_PASSWORD", "fake"),
        ("MAIL_FROM", "fake@example.edu.vn"), ("MAIL_SERVER", "smtp.invalid")):
        monkeypatch.setattr(settings, field, value)
    admissions = make_user(test_db, "manager")
    headers = login_headers(client, admissions)
    lead = client.post("/api/leads", json={"full_name": "SMTP Failure Applicant",
        "email": "smtp.failure@example.edu.vn", "phone": "0917777790"}).json()
    assert client.patch(f"/api/leads/{lead['id']}", headers=headers,
        json={"status": "closed"}).status_code == 200
    make_user(test_db, "student")
    failed = client.post(f"/api/leads/{lead['id']}/convert", headers=headers)
    assert failed.status_code == 502
    assert test_db.query(User).filter_by(email="smtp.failure@example.edu.vn").count() == 0
    refreshed_lead = test_db.get(Lead, lead["id"])
    assert refreshed_lead.status == "closed" and refreshed_lead.converted_user_id is None


def test_lead_care_journal_conversion_and_student_profile_management(client, test_db, monkeypatch):
    delivered_credentials = []
    async def fake_send_credentials(email, full_name, username, temporary_password):
        delivered_credentials.append((email, full_name, username, temporary_password))
    monkeypatch.setattr("app.api.routes.leads.send_account_credentials_email", fake_send_credentials)
    monkeypatch.setattr(settings, "MAIL_USERNAME", "fake-user")
    monkeypatch.setattr(settings, "MAIL_PASSWORD", "fake-password")
    monkeypatch.setattr(settings, "MAIL_FROM", "fake@example.edu.vn")
    monkeypatch.setattr(settings, "MAIL_SERVER", "smtp.invalid")
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
    manager = make_user(test_db, "manager")
    assert client.post("/api/leads/bulk-assignment", headers=login_headers(client, manager), json={
        "lead_ids": [lead_id], "assigned_to_user_id": admissions.id,
    }).status_code == 200
    headers = login_headers(client, admissions)

    detail = client.get(f"/api/leads/{lead_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["id"] == lead_id
    assert detail.json()["full_name"] == "New Applicant"

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
    assert result["credential_delivery"] == "email"
    assert "initial_password" not in result
    student_id = result["student"]["id"]
    student = test_db.get(User, student_id)
    assert student.student_code.startswith("SV")
    assert student.must_change_password is True
    assert student.hashed_password.startswith("$argon2id$")
    assert len(delivered_credentials) == 1
    assert delivered_credentials[0][:3] == (student.email, student.full_name, student.username)
    temporary_password = delivered_credentials[0][3]
    assert verify_password(temporary_password, student.hashed_password)
    assert test_db.query(StudentProfile).filter_by(user_id=student_id).one().study_program == "Công nghệ thông tin"
    assert test_db.query(LeadActivity).filter_by(lead_id=lead_id).count() >= 4
    login = client.post(
        "/api/auth/login",
        json={"identifier": student.username, "password": temporary_password},
    )
    assert login.status_code == 200
    assert login.json()["user"]["must_change_password"] is True

    repeated = client.post(f"/api/leads/{lead_id}/convert", headers=headers)
    assert repeated.status_code == 200
    assert repeated.json()["created"] is False
    assert repeated.json()["credential_delivery"] == "already_created"
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
            "entry_level": "Intermediate",
            "notes": "Needs evening schedule",
        },
    )
    assert updated_profile.status_code == 200
    assert updated_profile.json()["study_program"] == "Kỹ thuật phần mềm"
    assert updated_profile.json()["entry_level"] == "Intermediate"
    assert updated_profile.json()["notes"] == "Needs evening schedule"
    phone_search = client.get("/api/students?q=0900000000", headers=headers)
    assert phone_search.status_code == 200 and phone_search.json()["total"] == 1
    profile_read = client.get(f"/api/students/{student_id}", headers=headers)
    assert profile_read.status_code == 200
    assert profile_read.json()["entry_level"] == "Intermediate"
    assert profile_read.json()["notes"] == "Needs evening schedule"


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

    manager = make_user(test_db, "manager")
    assigned = client.patch(
        f"/api/leads/{lead_id}",
        headers=login_headers(client, manager),
        json={"assigned_to_user_id": advisor.id},
    )
    assert assigned.status_code == 200
    assert assigned.json()["assigned_to_user_id"] == advisor.id
    assignment_notification = test_db.query(Notification).filter_by(
        user_id=advisor.id, lead_id=lead_id
    ).one()
    assert assignment_notification.title == "Lead được giao cho bạn"

    advisor_headers = login_headers(client, advisor)
    scheduled = client.post(
        f"/api/leads/{lead_id}/activities",
        headers=advisor_headers,
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
    assert client.get("/api/leads/1", headers=headers).status_code == 403

    created = client.post(
        "/api/leads",
        json={
            "full_name": "Applicant",
            "email": "valid@example.edu.vn",
            "phone": "0901234567",
        },
    )
    assert created.status_code == 201
    admissions = make_user(test_db, "admissions")
    assert client.get(
        "/api/leads/999999", headers=login_headers(client, admissions)
    ).status_code == 404
    assert client.patch(
        f"/api/leads/{created.json()['id']}",
        headers=headers,
        json={"status": "not-a-status"},
    ).status_code == 403
