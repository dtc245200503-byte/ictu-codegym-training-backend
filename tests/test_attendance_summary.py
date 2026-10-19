from app.main import app as application  # noqa: F401
from datetime import date, time
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.database import Base, get_db
from app.core.security import create_access_token
from app.models.user import User
from app.models.role import Role
from app.models.user_role import UserRole
from app.models.permission import Permission
from app.models.role_permission import RolePermission
from app.models.training_program import TrainingClass
from app.models.subject import Subject, ClassSubject
from app.models.attendance import ClassSession, SessionAttendance, StudentClass
from app.models.student_enrollment import StudentEnrollment
from app.routers.attendance_summary import router
from app.crud import attendance_summary as crud
from app.attendance_summary_setup import ensure_attendance_summary_schema


@pytest.fixture
def ctx(monkeypatch):
    monkeypatch.setattr(crud, "today", lambda: date(2026, 10, 18))
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        for id, name in [(1, "TRAINING_MANAGER"), (2, "INSTRUCTOR"), (3, "STUDENT"), (4, "ASSISTANT"), (5, "ADMIN")]:
            db.add(Role(role_id=id, role_name=name))
        db.add(Permission(permission_id=1, permission_name="ATTENDANCE_VIEW"))
        db.flush()
        db.add_all([RolePermission(role_id=id, permission_id=1) for id in range(1, 6)])
        for id, role in [(1, 1), (2, 2), (3, 2), (4, 3), (5, 3), (6, 3), (7, 4), (8, 5), (9, 2)]:
            db.add(User(user_id=id, username=f"HV{id}", full_name=f"Người {id}", email=f"u{id}@example.com", password="unused", role_id=role))
            db.add(UserRole(user_id=id, role_id=role))
        db.add_all([Subject(id=id, code=f"MH{id}", subject_name=f"Môn {id}") for id in [1, 2]])
        db.add_all([TrainingClass(class_id=id, class_name=f"Lớp {id}") for id in [1, 2, 3]])
        db.flush()
        db.add_all([ClassSubject(id=1, class_id=1, subject_id=1, instructor_id=2),
                    ClassSubject(id=2, class_id=2, subject_id=1, instructor_id=3),
                    ClassSubject(id=3, class_id=1, subject_id=2, instructor_id=3)])
        db.flush()
        for id, day in [(1, 12), (2, 13), (3, 14), (4, 15), (5, 16), (6, 19), (7, 18)]:
            db.add(ClassSession(session_id=id, class_subject_id=1, session_date=date(2026, 10, day),
                start_time=time(8), topic=f"Buổi {id}", status="cancelled" if id == 5 else "scheduled"))
        db.add_all([StudentClass(student_id=id, class_id=1, enrolled_date=date(2026, 10, 1)) for id in [4, 5, 6]])
        db.flush()
        for id, state in [(1, "present"), (2, "late"), (3, "excused_absence"), (4, "unexcused_absence"), (5, "present"), (6, "present")]:
            db.add(SessionAttendance(session_id=id, student_id=4, student_name="Tên cũ", student_code="OLD", status=state))
        db.commit()
    app = FastAPI()
    app.include_router(router)
    def dependency():
        with sessions() as db:
            yield db
    app.dependency_overrides[get_db] = dependency
    with TestClient(app) as client:
        def request(path="/classes/1", actor=2, **params):
            headers = {} if actor is None else {"Authorization": "Bearer " + create_access_token(actor, "ADMIN")}
            return client.get("/api/attendance-summary" + path, headers=headers, params=params)
        yield request, sessions, engine
    engine.dispose()


def student(response, id=4):
    return next(row for row in response.json()["students"] if row["student_id"] == id)


def test_matrix_statuses_counts_denominator_and_chronological_order(ctx):
    response = ctx[0]()
    assert response.status_code == 200, response.text
    assert [s["session_id"] for s in response.json()["sessions"]] == [1, 2, 3, 4, 5, 7, 6]
    row = student(response)
    assert row["cells"] == ["present", "late", "excused_absence", "unexcused_absence", "cancelled", "unmarked", "upcoming"]
    assert (row["attended_sessions"], row["recorded_sessions"], row["absent_sessions"], row["attendance_rate"]) == (2, 4, 2, 50)
    assert (row["excused_absences"], row["unexcused_absences"]) == (1, 1)
    assert row["student_name"] == "Người 4"
    assert student(response, 5)["attendance_rate"] is None
    assert student(response, 5)["absent_sessions"] == 0


def test_sort_global_before_pagination_and_unknown_rates_last(ctx):
    with ctx[1]() as db:
        db.add_all([SessionAttendance(session_id=1, student_id=6, student_name="N6", student_code="HV6", status="unexcused_absence")])
        db.commit()
    assert [s["student_id"] for s in ctx[0](sort="rate_asc").json()["students"]] == [6, 4, 5]
    page2 = ctx[0](sort="rate_asc", page=2, page_size=1).json()
    assert page2["total"] == 3 and page2["students"][0]["student_id"] == 4
    assert ctx[0](sort="rate_asc", page=99, page_size=1).json()["page"] == 3


@pytest.mark.parametrize("params", [{"sort": "bad"}, {"page": 0}, {"page_size": 101}, {"page_size": -1}])
def test_invalid_filters(ctx, params):
    assert ctx[0](**params).status_code == 422


@pytest.mark.parametrize("actor,code", [(None, 401), (4, 403), (7, 403), (9, 404)])
def test_scope_rejects_unauthorized_users_even_with_forged_role_claim(ctx, actor, code):
    assert ctx[0](actor=actor).status_code == code
    options = ctx[0]("/classes", actor=actor)
    assert options.status_code == (200 if actor == 9 else code)
    if actor == 9:
        assert options.json()["items"] == []


def test_teacher_sees_whole_assigned_class_but_not_other_class(ctx):
    with ctx[1]() as db:
        db.add(ClassSession(session_id=8, class_subject_id=3, session_date=date(2026, 10, 18), start_time=time(10), topic="Môn khác"))
        db.commit()
    assert len(ctx[0]().json()["sessions"]) == 8
    assert ctx[0]("/classes/2").status_code == 404
    assert [row["class_id"] for row in ctx[0]("/classes").json()["items"]] == [1]
    assert len(ctx[0]("/classes", actor=1).json()["items"]) == 3
    assert ctx[0]("/classes/3", actor=8).status_code == 200
    assert ctx[0]("/classes/999", actor=1).status_code == 404


def test_view_permission_required_and_revocation_takes_effect(ctx):
    with ctx[1]() as db:
        db.query(RolePermission).filter_by(role_id=2).delete()
        db.commit()
    assert ctx[0]().status_code == 403


def test_substitute_access_and_assignment_revocation(ctx):
    with ctx[1]() as db:
        db.get(ClassSession, 1).substitute_instructor_id = 9
        db.commit()
    assert ctx[0](actor=9).status_code == 200
    with ctx[1]() as db:
        db.get(ClassSession, 1).status = "cancelled"
        db.get(ClassSubject, 1).instructor_id = 3
        db.commit()
    assert ctx[0](actor=9).status_code == 404
    assert ctx[0](actor=2).status_code == 404


@pytest.mark.parametrize("end_status", ["deferred", "transferred", "withdrawn"])
def test_effective_membership_excludes_old_records_after_end(ctx, end_status):
    with ctx[1]() as db:
        db.add(StudentEnrollment(student_id=4, class_id=1, started_on=date(2026, 10, 13), ended_on=date(2026, 10, 15), end_status=end_status))
        db.commit()
    row = student(ctx[0]())
    assert row["cells"][:4] == ["not_enrolled", "late", "excused_absence", "not_enrolled"]
    assert row["recorded_sessions"] == 2 and row["absent_sessions"] == 1 and row["attendance_rate"] == 50
    assert row["membership"] == "former"


def test_incoming_transfer_and_legacy_join_are_not_absent_before_start(ctx):
    with ctx[1]() as db:
        db.add(StudentEnrollment(student_id=5, class_id=1, started_on=date(2026, 10, 18), origin="transfer"))
        db.get(StudentClass, (6, 1)).enrolled_date = date(2026, 10, 19)
        db.commit()
    response = ctx[0]()
    assert student(response, 5)["cells"][:5] == ["not_enrolled"] * 5
    assert student(response, 5)["cells"][5] == "unmarked"
    assert student(response, 6)["membership"] == "upcoming"
    assert student(response, 6)["cells"][-1] == "upcoming"


def test_deleted_account_history_is_preserved_and_duplicate_intervals_do_not_double_count(ctx):
    with ctx[1]() as db:
        db.add(SessionAttendance(session_id=1, student_id=99, student_name="Học viên cũ", student_code="HV99", status="present"))
        db.add_all([StudentEnrollment(student_id=4, class_id=1, started_on=date(2026, 10, 1)) for _ in range(2)])
        db.commit()
    response = ctx[0]()
    assert student(response)["recorded_sessions"] == 4
    assert student(response, 99)["student_name"] == "Học viên cũ"
    assert student(response, 99)["attendance_rate"] == 100
    assert student(response, 99)["membership"] == "historical"


def test_prefilled_approved_absence_counts_only_reached_dates(ctx):
    with ctx[1]() as db:
        for id in [7, 6]:
            db.get(ClassSession, id).absence_prefilled = True
            db.add(SessionAttendance(session_id=id, student_id=5, student_name="N5", student_code="HV5", status="excused_absence"))
        db.commit()
    row = student(ctx[0](), 5)
    assert row["recorded_sessions"] == row["absent_sessions"] == 1
    assert row["attendance_rate"] == 0


def test_empty_class_and_no_sessions(ctx):
    data = ctx[0]("/classes/3", actor=1).json()
    assert data["students"] == data["sessions"] == [] and data["total"] == 0
    with ctx[1]() as db:
        db.add(StudentClass(student_id=4, class_id=3))
        db.commit()
    row = student(ctx[0]("/classes/3", actor=1))
    assert row["cells"] == [] and row["attendance_rate"] is None


def test_sixty_sessions_do_not_cause_per_session_database_queries(ctx):
    with ctx[1]() as db:
        for id in range(10, 70):
            db.add(ClassSession(session_id=id, class_subject_id=1, session_date=date(2026, 10, 18), start_time=time(id // 60, id % 60), topic=f"Buổi {id}"))
        db.commit()
        queries = []
        def count(*args): queries.append(args[2])
        person = db.get(User, 2)
        event.listen(ctx[2], "before_cursor_execute", count)
        try:
            result = crud.summary(db, person, 1, "rate_asc")
        finally:
            event.remove(ctx[2], "before_cursor_execute", count)
        assert len(result["sessions"]) == 67 and len(result["students"][0]["cells"]) == 67
        assert len(queries) <= 12


def test_minimal_schema_migration_is_repeatable():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE class_sessions (session_id INTEGER PRIMARY KEY, topic VARCHAR(255))"))
        connection.execute(text("INSERT INTO class_sessions VALUES (1, 'Existing')"))
    ensure_attendance_summary_schema(engine)
    ensure_attendance_summary_schema(engine)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT topic, status, substitute_instructor_id FROM class_sessions")).all() == [("Existing", "scheduled", None)]
    engine.dispose()
