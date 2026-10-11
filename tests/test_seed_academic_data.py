from sqlalchemy import func, select

from app.core.security import hash_password
from app.models.attendance import AttendanceRecord, AttendanceSession
from app.models.course import Course
from app.models.role import Role
from app.models.study_program import StudyProgram
from app.models.training_class import ClassEnrollment, TrainingClass
from app.models.user import User
from app.seed_academic_data import seed_academic_data


def test_academic_seed_is_repeatable_and_exposes_program_and_attendance_data(
    client, test_db
):
    test_db.add_all(
        [
            Role(code="admin", name="Admin"),
            Role(code="teacher", name="Instructor"),
            Role(code="ta", name="TA"),
            Role(code="student", name="Student"),
        ]
    )
    test_db.commit()

    first_run = seed_academic_data(test_db)
    second_run = seed_academic_data(test_db)
    assert first_run["programs"] == 2
    assert first_run["courses"] == 10
    assert first_run["classes"] == 10
    assert first_run["students"] == 6
    assert first_run["attendance_sessions"] == 40
    assert first_run["new_attendance_records"] == 240
    assert first_run["new_users"] == 8
    assert second_run["new_users"] == 0
    assert second_run["new_attendance_records"] == 0
    assert test_db.scalar(select(func.count(StudyProgram.id))) == 2
    assert test_db.scalar(select(func.count(Course.id))) == 10
    assert test_db.scalar(select(func.min(Course.credits))) == 2
    assert test_db.scalar(select(func.max(Course.credits))) == 4
    assert test_db.scalar(select(func.count(TrainingClass.id))) == 10
    assert test_db.scalar(select(func.count(ClassEnrollment.id))) == 60
    assert test_db.scalar(select(func.count(AttendanceSession.id))) == 40
    assert test_db.scalar(select(func.count(AttendanceRecord.id))) == 240

    programs = test_db.scalars(select(StudyProgram).order_by(StudyProgram.code)).all()
    assert [program.total_credits for program in programs] == [126, 126]
    assert all(120 <= program.total_credits <= 130 for program in programs)
    assert all(program.degree_level == "Đại học" for program in programs)

    admin_role = test_db.scalar(select(Role).where(Role.code == "admin"))
    admin = User(
        username="seed.test.admin",
        email="seed.test.admin@example.edu.vn",
        full_name="Seed Test Admin",
        hashed_password=hash_password("CorrectPassword123!"),
        roles=[admin_role],
    )
    test_db.add(admin)
    test_db.commit()
    login = client.post(
        "/api/auth/login",
        json={"email": admin.email, "password": "CorrectPassword123!"},
    )
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    program_response = client.get("/api/study-programs", headers=headers)
    assert program_response.status_code == 200
    assert len(program_response.json()) == 2
    assert {item["name"] for item in program_response.json()} == {
        "Công nghệ thông tin",
        "Kỹ thuật phần mềm",
    }

    training_class = test_db.scalar(
        select(TrainingClass).where(TrainingClass.code == "INT101_01")
    )
    roster = client.get(
        f"/api/classes/{training_class.id}/students", headers=headers
    )
    assert roster.status_code == 200
    assert len(roster.json()) == 6
    assert all(len(student["attendance_history"]) == 4 for student in roster.json())
