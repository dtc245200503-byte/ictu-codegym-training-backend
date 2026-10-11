from datetime import time
from io import BytesIO

from openpyxl import Workbook
from app.core.security import hash_password
from app.core.security import verify_password
from app.models.course import Course
from app.models.classroom import Classroom
from app.models.role import Role
from app.models.study_program import StudyProgram
from app.models.training_class import ClassEnrollment, TrainingClass
from app.models.user import User


def create_user(db, *, username, email, full_name, role, student_code=None):
    assigned_role = db.query(Role).filter(Role.code == role).one()
    user = User(
        username=username,
        email=email,
        full_name=full_name,
        student_code=student_code,
        hashed_password=hash_password("CorrectPassword123!"),
        roles=[assigned_role],
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def create_role(db, code, name):
    role = Role(code=code, name=name)
    db.add(role)
    db.commit()
    return role


def get_admin(client, db):
    for code, name in (
        ("admin", "Admin"),
        ("manager", "Training Manager"),
        ("admissions", "Admissions"),
        ("accountant", "Accountant"),
        ("teacher", "Instructor"),
        ("ta", "TA"),
        ("student", "Student"),
        ("guest", "Guest"),
    ):
        create_role(db, code, name)
    user = create_user(
        db,
        username="admin01",
        email="admin@example.edu.vn",
        full_name="System Admin",
        role="admin",
    )
    response = client.post(
        "/api/auth/login",
        json={"email": user.email, "password": "CorrectPassword123!"},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def test_account_create_and_role_filtered_list(client, test_db):
    admin_token = get_admin(client, test_db)
    headers = {"Authorization": f"Bearer {admin_token}"}

    response = client.post(
        "/api/accounts",
        headers=headers,
        json={
            "username": "sv0001",
            "email": "sv0001@example.edu.vn",
            "full_name": "Nguyen Van A",
            "role": "student",
            "student_code": "SV0001",
        },
    )
    assert response.status_code == 201
    account = response.json()
    assert account["credential_delivery"] == "manual"
    assert account["temporary_password"] == "123456"
    assert account["roles"] == ["student"]
    created_user = test_db.query(User).filter(User.username == "sv0001").one()
    assert created_user.must_change_password is True
    assert created_user.hashed_password.startswith("$argon2id$")
    assert verify_password("123456", created_user.hashed_password)
    first_login = client.post(
        "/api/auth/login",
        json={"identifier": "sv0001@example.edu.vn", "password": "123456"},
    )
    assert first_login.status_code == 200
    assert first_login.json()["user"]["must_change_password"] is True
    blocked = client.get(
        "/api/dashboard/summary",
        headers={"Authorization": "Bearer " + first_login.json()["access_token"]},
    )
    assert blocked.status_code == 403

    response = client.get(
        "/api/accounts?role=student&q=SV0001&page=1&page_size=10",
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["student_code"] == "SV0001"


def test_account_create_uses_admin_selected_initial_password(client, test_db):
    admin_token = get_admin(client, test_db)
    response = client.post(
        "/api/accounts",
        headers={"Authorization": "Bearer " + admin_token},
        json={
            "username": "teacher.custom",
            "email": "teacher.custom@example.edu.vn",
            "full_name": "Custom Password Teacher",
            "role": "teacher",
            "password": "TeacherTemp123!",
        },
    )

    assert response.status_code == 201
    assert response.json()["temporary_password"] == "TeacherTemp123!"
    user = test_db.query(User).filter(User.username == "teacher.custom").one()
    assert user.must_change_password is True
    assert verify_password("TeacherTemp123!", user.hashed_password)


def test_account_create_and_role_update_support_multiple_roles(client, test_db):
    admin_token = get_admin(client, test_db)
    headers = {"Authorization": f"Bearer {admin_token}"}
    created = client.post(
        "/api/accounts",
        headers=headers,
        json={
            "username": "teacher.admissions",
            "email": "teacher.admissions@example.edu.vn",
            "full_name": "Teacher and Advisor",
            "roles": ["teacher", "admissions"],
        },
    )
    assert created.status_code == 201
    assert set(created.json()["roles"]) == {"teacher", "admissions"}

    updated = client.put(
        f"/api/accounts/{created.json()['id']}/role",
        headers=headers,
        json={"roles": ["teacher", "ta", "admissions"]},
    )
    assert updated.status_code == 200
    assert set(updated.json()["roles"]) == {"teacher", "ta", "admissions"}

    invalid = client.put(
        f"/api/accounts/{created.json()['id']}/role",
        headers=headers,
        json={"roles": ["teacher", "unknown"]},
    )
    assert invalid.status_code == 422


def test_account_lock_and_unlock_require_audit_reason(client, test_db):
    admin_token = get_admin(client, test_db)
    student = create_user(
        test_db,
        username="lock.student",
        email="lock.student@example.edu.vn",
        full_name="Lock Student",
        role="student",
    )
    headers = {"Authorization": f"Bearer {admin_token}"}
    assert client.post(f"/api/accounts/{student.id}/lock", headers=headers, json={}).status_code == 422

    locked = client.post(
        f"/api/accounts/{student.id}/lock",
        headers=headers,
        json={"reason": "Tạm khóa để xác minh hồ sơ."},
    )
    assert locked.status_code == 200
    assert locked.json()["is_locked"] is True
    unlocked = client.post(
        f"/api/accounts/{student.id}/unlock",
        headers=headers,
        json={"reason": "Đã xác minh thông tin."},
    )
    assert unlocked.status_code == 200
    assert unlocked.json()["is_locked"] is False


def test_course_syllabus_sessions_can_be_managed(client, test_db):
    admin_token = get_admin(client, test_db)
    headers = {"Authorization": f"Bearer {admin_token}"}
    course = Course(code="SYL101", name="Syllabus Course", department="IT", credits=3)
    test_db.add(course)
    test_db.commit()
    dependent = client.post(
        "/api/courses",
        headers=headers,
        json={
            "code": "SYL102",
            "name": "Advanced Syllabus",
            "department": "IT",
            "credits": 3,
            "prerequisite_id": course.id,
        },
    )
    assert dependent.status_code == 201
    assert dependent.json()["prerequisite_id"] == course.id

    second = client.post(
        f"/api/courses/{course.id}/lessons",
        headers=headers,
        json={"sequence": 2, "title": "Databases", "objectives": "Model relational data", "description": "Relational design"},
    )
    first = client.post(
        f"/api/courses/{course.id}/lessons",
        headers=headers,
        json={"sequence": 1, "title": "Introduction", "objectives": "Understand course foundations"},
    )
    assert second.status_code == first.status_code == 201
    beyond_limit = client.post(
        f"/api/courses/{course.id}/lessons",
        headers=headers,
        json={
            "sequence": 17,
            "title": "Beyond syllabus",
            "objectives": "Should be rejected",
        },
    )
    assert beyond_limit.status_code == 422
    cannot_shrink = client.patch(
        f"/api/courses/{course.id}",
        headers=headers,
        json={"session_count": 1},
    )
    assert cannot_shrink.status_code == 409
    duplicate = client.post(
        f"/api/courses/{course.id}/lessons",
        headers=headers,
        json={"sequence": 1, "title": "Duplicate sequence", "objectives": "Duplicate lesson"},
    )
    assert duplicate.status_code == 409
    listed = client.get(f"/api/courses/{course.id}/lessons", headers=headers)
    assert [lesson["sequence"] for lesson in listed.json()] == [1, 2]
    edited = client.patch(
        f"/api/courses/{course.id}/lessons/{second.json()['id']}",
        headers=headers,
        json={"title": "SQL and databases"},
    )
    assert edited.status_code == 200
    assert edited.json()["title"] == "SQL and databases"
    clone = client.post(
        f"/api/courses/{dependent.json()['id']}/lessons/clone",
        headers=headers,
        json={"source_course_id": course.id},
    )
    assert clone.status_code == 201
    assert [lesson["objectives"] for lesson in clone.json()] == [
        "Understand course foundations",
        "Model relational data",
    ]
    assert client.delete(
        f"/api/courses/{course.id}/lessons/{first.json()['id']}",
        headers=headers,
    ).status_code == 204


def test_new_admin_created_account_can_login_with_email_or_username(client, test_db):
    admin_token = get_admin(client, test_db)
    response = client.post(
        "/api/accounts",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "username": "new.admin",
            "email": "new.admin@example.edu.vn",
            "full_name": "New Administrator",
            "role": "admin",
        },
    )

    assert response.status_code == 201
    created = response.json()
    account = test_db.query(User).filter(User.username == "new.admin").one()
    assert account.is_active is True
    assert account.must_change_password is True
    assert account.hashed_password != created["temporary_password"]
    assert verify_password(created["temporary_password"], account.hashed_password)

    for identifier in (created["email"], created["username"]):
        login = client.post(
            "/api/auth/login",
            json={
                "identifier": identifier,
                "password": created["temporary_password"],
            },
        )
        assert login.status_code == 200
        assert login.json()["access_token"]
        assert login.json()["refresh_token"]
        assert login.json()["user"]["must_change_password"] is True


def test_admin_must_change_password_before_accessing_other_apis(client, test_db):
    admin_token = get_admin(client, test_db)
    response = client.post(
        "/api/accounts",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "username": "first.login.admin",
            "email": "first.login.admin@example.edu.vn",
            "full_name": "First Login Admin",
            "role": "admin",
        },
    )
    created = response.json()
    login = client.post(
        "/api/auth/login",
        json={
            "identifier": created["username"],
            "password": created["temporary_password"],
        },
    )
    assert login.status_code == 200
    access_token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}
    assert client.get("/api/me", headers=headers).json()["must_change_password"] is True
    blocked = client.get("/api/dashboard/summary", headers=headers)
    assert blocked.status_code == 403
    assert blocked.json()["detail"]["code"] == "password_change_required"

    changed = client.post(
        "/api/auth/change-password",
        headers=headers,
        json={
            "current_password": created["temporary_password"],
            "new_password": "NewAdminPassword456!",
        },
    )
    assert changed.status_code == 200
    assert client.get("/api/me", headers=headers).json()["must_change_password"] is False
    assert client.get("/api/dashboard/summary", headers=headers).status_code == 200


def test_non_admin_cannot_manage_accounts(client, test_db):
    create_role(test_db, "student", "Học viên")
    student = create_user(
        test_db,
        username="student01",
        email="student@example.edu.vn",
        full_name="Student",
        role="student",
    )
    token = client.post(
        "/api/auth/login",
        json={"email": student.email, "password": "CorrectPassword123!"},
    ).json()["access_token"]

    response = client.get(
        "/api/accounts", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 403


def test_excel_import_skips_invalid_rows_and_creates_valid_accounts(client, test_db):
    admin_token = get_admin(client, test_db)
    headers = {
        "Authorization": f"Bearer {admin_token}",
        "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["username", "email", "full_name", "role", "student_code"])
    sheet.append(["sv0001", "duplicate@example.edu.vn", "Student One", "student", "SV0001"])
    sheet.append(["sv0002", "duplicate@example.edu.vn", "Student Two", "student", "SV0002"])
    invalid_file = BytesIO()
    workbook.save(invalid_file)

    preview = client.post(
        "/api/accounts/import?preview=true",
        headers=headers,
        content=invalid_file.getvalue(),
    )
    assert preview.status_code == 200
    assert preview.json()["valid_count"] == 1
    assert preview.json()["invalid_count"] == 1
    assert preview.json()["rows"][1]["errors"] == ["email đã tồn tại"]
    assert test_db.query(User).count() == 1

    response = client.post(
        "/api/accounts/import",
        headers=headers,
        content=invalid_file.getvalue(),
    )
    assert response.status_code == 200
    assert response.json()["created_count"] == 1
    assert response.json()["skipped_count"] == 1
    assert response.json()["row_errors"][0]["row"] == 3
    assert test_db.query(User).count() == 2

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["username", "email", "full_name", "role", "student_code"])
    sheet.append(["sv0003", "sv0003@example.edu.vn", "Student Three", "student", "SV0003"])
    valid_file = BytesIO()
    workbook.save(valid_file)

    response = client.post(
        "/api/accounts/import",
        headers=headers,
        content=valid_file.getvalue(),
    )
    assert response.status_code == 200
    assert response.json()["created_count"] == 1
    assert response.json()["accounts"][0]["credential_delivery"] == "manual"
    assert response.json()["accounts"][0]["temporary_password"] == "123456"
    imported_user = test_db.query(User).filter(User.username == "sv0003").one()
    assert imported_user.must_change_password is True
    assert imported_user.hashed_password.startswith("$argon2id$")
    assert verify_password("123456", imported_user.hashed_password)


def test_change_password_revokes_refresh_session(client, test_db):
    create_role(test_db, "student", "Học viên")
    student = create_user(
        test_db,
        username="student01",
        email="student@example.edu.vn",
        full_name="Student",
        role="student",
    )
    login = client.post(
        "/api/auth/login",
        json={"email": student.email, "password": "CorrectPassword123!"},
    ).json()

    response = client.post(
        "/api/auth/change-password",
        headers={"Authorization": f"Bearer {login['access_token']}"},
        json={
            "current_password": "CorrectPassword123!",
            "new_password": "NewPassword456!",
        },
    )
    assert response.status_code == 200
    assert client.post(
        "/api/auth/refresh",
        json={"refresh_token": login["refresh_token"]},
    ).status_code == 401
    assert client.post(
        "/api/auth/login",
        json={"email": student.email, "password": "NewPassword456!"},
    ).status_code == 200


def test_class_enrollment_rejects_student_schedule_conflict(client, test_db):
    admin_token = get_admin(client, test_db)
    teacher = create_user(
        test_db,
        username="teacher01",
        email="teacher@example.edu.vn",
        full_name="Teacher",
        role="teacher",
    )
    second_teacher = create_user(
        test_db,
        username="teacher02",
        email="teacher02@example.edu.vn",
        full_name="Second Teacher",
        role="teacher",
    )
    student = create_user(
        test_db,
        username="student01",
        email="student@example.edu.vn",
        full_name="Student",
        role="student",
    )
    course_a = Course(
        code="CS101", name="Programming", department="Computer Science", credits=3
    )
    course_b = Course(
        code="CS102", name="Algorithms", department="Computer Science", credits=3
    )
    test_db.add_all([course_a, course_b])
    test_db.commit()

    class_a = TrainingClass(
        code="CS101-01",
        name="Programming 01",
        course_id=course_a.id,
        teacher_id=teacher.id,
        room="A101",
        term="2026-1",
        day_of_week=0,
        start_time=time(9, 0),
        end_time=time(11, 0),
        capacity=30,
    )
    test_db.add(class_a)
    test_db.commit()
    test_db.add(ClassEnrollment(class_id=class_a.id, student_id=student.id))
    test_db.commit()

    response = client.post(
        "/api/classes",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "code": "CS102-01",
            "name": "Algorithms 01",
            "course_id": course_b.id,
            "teacher_id": second_teacher.id,
            "room": "A102",
            "term": "2026-1",
            "day_of_week": 0,
            "start_time": "10:30",
            "end_time": "12:00",
            "capacity": 30,
        },
    )
    assert response.status_code == 201
    response = client.post(
        f"/api/classes/{response.json()['id']}/students",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"student_ids": [student.id]},
    )
    assert response.status_code == 409
    assert "trùng" in response.json()["detail"]


def test_classroom_catalog_program_class_creation_and_capacity_rules(client, test_db):
    admin_token = get_admin(client, test_db)
    headers = {"Authorization": "Bearer " + admin_token}
    teacher = create_user(
        test_db,
        username="room.teacher",
        email="room.teacher@example.edu.vn",
        full_name="Room Teacher",
        role="teacher",
    )
    course = Course(
        code="ROOM101",
        name="Room Catalog",
        department="Computer Science",
        credits=3,
    )
    test_db.add(course)
    test_db.flush()
    program = StudyProgram(
        code="ROOM-PROG",
        name="Room Program",
        total_credits=120,
        courses=[course],
    )
    test_db.add(program)
    test_db.commit()

    room_response = client.post(
        "/api/rooms",
        headers=headers,
        json={
            "code": "LAB-9",
            "capacity": 30,
            "online_url": "https://meet.example.edu.vn/lab-9",
        },
    )
    assert room_response.status_code == 201
    room = room_response.json()
    assert room["capacity"] == 30
    assert client.get("/api/rooms", headers=headers).json()[0]["code"] == "LAB-9"

    class_payload = {
        "code": "ROOM101-01",
        "name": "Room Catalog Section 01",
        "course_id": course.id,
        "program_id": program.id,
        "teacher_id": teacher.id,
        "room": "LAB-9",
        "term": "2026-2",
        "day_of_week": 2,
        "start_time": "13:00",
        "end_time": "15:00",
        "capacity": 31,
    }
    oversized = client.post("/api/classes", headers=headers, json=class_payload)
    assert oversized.status_code == 422

    class_payload["capacity"] = 20
    created = client.post("/api/classes", headers=headers, json=class_payload)
    assert created.status_code == 201
    assert created.json()["room"] == "LAB-9"
    assert len(client.get("/api/classes?term=2026-2&q=ROOM101", headers=headers).json()) == 1
    assert client.get("/api/classes?term=2026-1", headers=headers).json() == []

    students = [
        create_user(
            test_db,
            username=f"bulk.student{index}",
            email=f"bulk.student{index}@example.edu.vn",
            full_name=f"Bulk Student {index}",
            role="student",
        )
        for index in (1, 2)
    ]
    enrolled = client.post(
        f"/api/classes/{created.json()['id']}/students",
        headers=headers,
        json={"student_ids": [student.id for student in students]},
    )
    assert enrolled.status_code == 201
    assert enrolled.json()["enrolled_count"] == 2

    too_small = client.patch(
        f"/api/rooms/{room['id']}",
        headers=headers,
        json={"capacity": 10},
    )
    assert too_small.status_code == 409
    renamed = client.patch(
        f"/api/rooms/{room['id']}",
        headers=headers,
        json={"code": "LAB-10"},
    )
    assert renamed.status_code == 200
    assert test_db.query(TrainingClass).filter_by(code="ROOM101-01").one().room == "LAB-10"

    wrong_program = client.post(
        "/api/classes",
        headers=headers,
        json={
            **class_payload,
            "code": "ROOM101-02",
            "program_id": program.id + 1,
            "room": "LAB-10",
        },
    )
    assert wrong_program.status_code == 422
    missing_room = client.post(
        "/api/classes",
        headers=headers,
        json={
            **class_payload,
            "code": "ROOM101-03",
            "program_id": program.id,
            "room": "UNLISTED",
            "day_of_week": 4,
        },
    )
    assert missing_room.status_code == 422


def test_program_curriculum_persists_course_order_and_prerequisite(client, test_db):
    admin_token = get_admin(client, test_db)
    headers = {"Authorization": "Bearer " + admin_token}
    first = Course(code="CUR101", name="Foundations", department="IT", credits=3)
    second = Course(code="CUR201", name="Advanced", department="IT", credits=3)
    program = StudyProgram(
        code="CUR-PROG",
        name="Curriculum Program",
        total_credits=120,
        courses=[first, second],
    )
    test_db.add(program)
    test_db.commit()

    response = client.put(
        f"/api/study-programs/{program.id}/courses",
        headers=headers,
        json={
            "courses": [
                {"course_id": first.id},
                {"course_id": second.id, "prerequisite_course_id": first.id},
            ]
        },
    )
    assert response.status_code == 200
    result = client.get("/api/study-programs", headers=headers)
    program_payload = next(item for item in result.json() if item["id"] == program.id)
    assert [item["id"] for item in program_payload["courses"]] == [first.id, second.id]
    assert program_payload["courses"][1]["prerequisite_course_id"] == first.id

    curriculum = client.get(
        f"/api/study-programs/{program.id}/subjects",
        headers=headers,
    )
    assert curriculum.status_code == 200
    assert [item["subject_id"] for item in curriculum.json()] == [first.id, second.id]
    assert curriculum.json()[1]["order_index"] == 2
    assert curriculum.json()[1]["prerequisite_subject_id"] == first.id

    third = Course(code="CUR301", name="Capstone", department="IT", credits=3)
    test_db.add(third)
    test_db.commit()
    added = client.post(
        f"/api/study-programs/{program.id}/subjects",
        headers=headers,
        json={"subject_id": third.id},
    )
    assert added.status_code == 201
    assert added.json()["order_index"] == 3
    duplicate = client.post(
        f"/api/study-programs/{program.id}/subjects",
        headers=headers,
        json={"subject_id": third.id},
    )
    assert duplicate.status_code == 409

    updated = client.put(
        f"/api/study-programs/{program.id}/subjects",
        headers=headers,
        json={
            "subjects": [
                {"subject_id": first.id},
                {"subject_id": second.id, "prerequisite_subject_id": first.id},
                {"subject_id": third.id, "prerequisite_subject_id": second.id},
            ]
        },
    )
    assert updated.status_code == 200
    removed = client.delete(
        f"/api/study-programs/{program.id}/subjects/{second.id}",
        headers=headers,
    )
    assert removed.status_code == 204
    after_remove = client.get(
        f"/api/study-programs/{program.id}/subjects",
        headers=headers,
    )
    assert [item["subject_id"] for item in after_remove.json()] == [first.id, third.id]
    assert [item["order_index"] for item in after_remove.json()] == [1, 2]
    assert after_remove.json()[1]["prerequisite_subject_id"] is None

    invalid_order = client.put(
        f"/api/study-programs/{program.id}/courses",
        headers=headers,
        json={
            "courses": [
                {"course_id": second.id, "prerequisite_course_id": first.id},
                {"course_id": first.id},
            ]
        },
    )
    assert invalid_order.status_code == 422


def test_bulk_enrollment_checks_physical_room_capacity(client, test_db):
    admin_token = get_admin(client, test_db)
    headers = {"Authorization": f"Bearer {admin_token}"}
    teacher = create_user(
        test_db, username="capacity.teacher", email="capacity.teacher@example.edu.vn",
        full_name="Capacity Teacher", role="teacher",
    )
    students = [
        create_user(
            test_db,
            username=f"capacity.student.{index}",
            email=f"capacity.student.{index}@example.edu.vn",
            full_name=f"Capacity Student {index}",
            role="student",
        )
        for index in range(2)
    ]
    course = Course(code="CAP101", name="Capacity", department="IT", credits=3)
    test_db.add(course)
    test_db.commit()
    room = client.post(
        "/api/rooms",
        headers=headers,
        json={"code": "CAP-ROOM", "capacity": 5},
    )
    assert room.status_code == 201
    created = client.post(
        "/api/classes",
        headers=headers,
        json={
            "code": "CAP101-01",
            "name": "Capacity Test",
            "course_id": course.id,
            "teacher_id": teacher.id,
            "room": "CAP-ROOM",
            "term": "2026-1",
            "day_of_week": 1,
            "start_time": "09:00",
            "end_time": "11:00",
            "capacity": 5,
        },
    )
    assert created.status_code == 201
    test_db.query(Classroom).filter_by(code="CAP-ROOM").one().capacity = 1
    test_db.commit()

    response = client.post(
        f"/api/classes/{created.json()['id']}/students",
        headers=headers,
        json={"student_ids": [student.id for student in students]},
    )
    assert response.status_code == 409
    assert "phòng học" in response.json()["detail"]


def test_class_schedule_changes_notify_students_and_roster_status_is_exportable(
    client, test_db
):
    admin_token = get_admin(client, test_db)
    teacher = create_user(
        test_db,
        username="teacher01",
        email="teacher@example.edu.vn",
        full_name="Teacher",
        role="teacher",
    )
    student = create_user(
        test_db,
        username="student01",
        email="student@example.edu.vn",
        full_name="Student",
        role="student",
        student_code="SV0001",
    )
    course = Course(
        code="CS201", name="Database", department="Computer Science", credits=3
    )
    test_db.add(course)
    test_db.commit()
    created = client.post(
        "/api/classes",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "code": "CS201-01",
            "name": "Database 01",
            "course_id": course.id,
            "teacher_id": teacher.id,
            "room": "B201",
            "term": "2026-1",
            "day_of_week": 1,
            "start_time": "09:00",
            "end_time": "11:00",
            "capacity": 30,
        },
    )
    assert created.status_code == 201
    class_id = created.json()["id"]
    enrolled = client.post(
        f"/api/classes/{class_id}/students",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"student_ids": [student.id]},
    )
    assert enrolled.status_code == 201

    teacher_token = client.post(
        "/api/auth/login",
        json={"email": teacher.email, "password": "CorrectPassword123!"},
    ).json()["access_token"]
    status_response = client.patch(
        f"/api/classes/{class_id}/students/{student.id}/status",
        headers={"Authorization": f"Bearer {teacher_token}"},
        json={"attendance_status": "present"},
    )
    assert status_response.status_code == 200
    assert status_response.json()["attendance_status"] == "present"

    response = client.patch(
        f"/api/classes/{class_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"room": "B205", "start_time": "10:00", "end_time": "12:00"},
    )
    assert response.status_code == 200
    assert response.json()["room"] == "B205"

    student_token = client.post(
        "/api/auth/login",
        json={"email": student.email, "password": "CorrectPassword123!"},
    ).json()["access_token"]
    notifications = client.get(
        "/api/notifications",
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert notifications.status_code == 200
    assert notifications.json()["unread_count"] == 1
    notification_id = notifications.json()["items"][0]["id"]
    assert client.post(
        f"/api/notifications/{notification_id}/read",
        headers={"Authorization": f"Bearer {student_token}"},
    ).json()["is_read"] is True

    roster = client.get(
        f"/api/classes/{class_id}/students",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert roster.status_code == 200
    assert roster.json()[0]["tuition_status"] == "unpaid"
    assert roster.json()[0]["attendance_status"] == "present"
    exported = client.get(
        f"/api/classes/{class_id}/students/export",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


def test_all_roles_ta_attendance_and_class_assignments(client, test_db):
    admin_token = get_admin(client, test_db)
    teacher = create_user(
        test_db,
        username="teacher01",
        email="teacher@example.edu.vn",
        full_name="Instructor",
        role="teacher",
    )
    assistant = create_user(
        test_db,
        username="ta01",
        email="ta@example.edu.vn",
        full_name="Teaching Assistant",
        role="ta",
    )
    second_assistant = create_user(
        test_db,
        username="ta02",
        email="ta02@example.edu.vn",
        full_name="Second Teaching Assistant",
        role="ta",
    )
    student = create_user(
        test_db,
        username="student01",
        email="student@example.edu.vn",
        full_name="Student",
        role="student",
    )
    course = Course(
        code="CS301", name="Web Development", department="Computer Science", credits=3
    )
    test_db.add(course)
    test_db.commit()

    created = client.post(
        "/api/classes",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "code": "CS301-01",
            "name": "Web Development 01",
            "course_id": course.id,
            "teacher_id": teacher.id,
            "assistant_ids": [assistant.id, second_assistant.id],
            "room": "C301",
            "term": "2026-1",
            "day_of_week": 2,
            "start_time": "09:00",
            "end_time": "11:00",
            "capacity": 30,
        },
    )
    assert created.status_code == 201
    class_id = created.json()["id"]
    assert created.json()["assistant_name"] == assistant.full_name
    assert {member["id"] for member in created.json()["assistants"]} == {
        assistant.id,
        second_assistant.id,
    }
    activated = client.patch(
        f"/api/classes/{class_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"status": "active"},
    )
    assert activated.status_code == 200
    assert client.post(
        f"/api/classes/{class_id}/students",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"student_ids": [student.id]},
    ).status_code == 201

    ta_token = client.post(
        "/api/auth/login",
        json={"email": assistant.email, "password": "CorrectPassword123!"},
    ).json()["access_token"]
    ta_headers = {"Authorization": f"Bearer {ta_token}"}
    assert [item["id"] for item in client.get("/api/classes", headers=ta_headers).json()] == [
        class_id
    ]
    second_ta_token = client.post(
        "/api/auth/login",
        json={"email": second_assistant.email, "password": "CorrectPassword123!"},
    ).json()["access_token"]
    assert [item["id"] for item in client.get(
        "/api/classes", headers={"Authorization": f"Bearer {second_ta_token}"}
    ).json()] == [class_id]
    attendance = client.patch(
        f"/api/classes/{class_id}/students/{student.id}/status",
        headers=ta_headers,
        json={"attendance_status": "present"},
    )
    assert attendance.status_code == 200
    assert attendance.json()["attendance_status"] == "present"

    assignment = client.post(
        f"/api/classes/{class_id}/assignments",
        headers=ta_headers,
        json={
            "title": "Build a small website",
            "description": "Submit the source code.",
            "due_at": "2026-12-01T23:59:00",
        },
    )
    assert assignment.status_code == 201
    assert assignment.json()["class_id"] == class_id

    student_token = client.post(
        "/api/auth/login",
        json={"email": student.email, "password": "CorrectPassword123!"},
    ).json()["access_token"]
    student_headers = {"Authorization": f"Bearer {student_token}"}
    visible_assignments = client.get("/api/assignments", headers=student_headers)
    assert visible_assignments.status_code == 200
    assert visible_assignments.json()[0]["title"] == "Build a small website"
    assert client.post(
        f"/api/classes/{class_id}/assignments",
        headers=student_headers,
        json={"title": "Unauthorized"},
    ).status_code == 403

    roles = client.get("/api/roles", headers={"Authorization": f"Bearer {admin_token}"})
    assert roles.status_code == 200
    assert {role["code"] for role in roles.json()} == {
        "admin",
        "manager",
        "admissions",
        "accountant",
        "teacher",
        "ta",
        "student",
        "guest",
    }
    completed = client.patch(
        f"/api/classes/{class_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"status": "completed"},
    )
    assert completed.status_code == 200
    invalid_transition = client.patch(
        f"/api/classes/{class_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"status": "active"},
    )
    assert invalid_transition.status_code == 409
