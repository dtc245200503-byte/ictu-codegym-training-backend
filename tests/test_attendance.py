from datetime import date, datetime, time, timedelta
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from app.main import app as application  # noqa: F401; initialize the shared test schema
from app.database import Base, get_db
from app.core.security import create_access_token
from app.models.user import User
from app.models.role import Role
from app.models.permission import Permission
from app.models.role_permission import RolePermission
from app.models.user_role import UserRole
from app.models.subject import Subject, ClassSubject
from app.models.training_program import TrainingClass
from app.models.attendance import StudentClass, ClassSession, SessionAttendance, AttendanceHistory
from app.routers.attendance import router
from app.crud import attendance


@pytest.fixture
def ctx(monkeypatch):
    clock = [datetime(2026, 10, 16, 3)]
    monkeypatch.setattr(attendance, "utcnow", lambda: clock[0])
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    @event.listens_for(engine, "connect")
    def foreign_keys(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        for id, name in enumerate(["TRAINING_MANAGER", "INSTRUCTOR", "STUDENT", "ADMIN", "ASSISTANT"], 1):
            db.add(Role(role_id=id, role_name=name))
        db.add_all([Permission(permission_id=1, permission_name="ATTENDANCE_VIEW"),
                    Permission(permission_id=2, permission_name="ATTENDANCE_EDIT")])
        db.flush()
        for role in [1, 2, 3, 4, 5]:
            for permission in [1, 2]:
                db.add(RolePermission(role_id=role, permission_id=permission))
        for id, role in [(1, 1), (2, 2), (3, 2), (4, 3), (5, 3), (6, 3), (7, 3), (8, 3), (9, 4), (10, 5)]:
            db.add(User(user_id=id, username=f"HV{id}", full_name=f"Người {id}", email=f"u{id}@example.com", password="unused", role_id=role))
        db.flush()
        for id, role in [(1, 1), (2, 2), (3, 2), (4, 3), (5, 3), (6, 3), (7, 3), (8, 3), (9, 4), (10, 5)]:
            db.add(UserRole(user_id=id, role_id=role))
        db.add_all([Subject(id=id, code=f"S{id}", subject_name=f"Môn {id}") for id in [1, 2]])
        db.add_all([TrainingClass(class_id=id, class_name=f"Lớp {id}", instructor_id=2, start_date=date(2026, 10, 1)) for id in [1, 2]])
        db.flush()
        db.add_all([ClassSubject(id=1, class_id=1, subject_id=1, instructor_id=2), ClassSubject(id=2, class_id=2, subject_id=2, instructor_id=3)])
        db.flush()
        for id, day, subject, hour in [(1, 16, 1, 8), (2, 15, 1, 8), (3, 17, 1, 8), (4, 16, 2, 9), (5, 16, 1, 10)]:
            db.add(ClassSession(session_id=id, class_subject_id=subject, session_date=date(2026, 10, day), start_time=time(hour), topic=f"Buổi {id}"))
        for student in [4, 5, 6, 7]:
            db.add(StudentClass(student_id=student, class_id=1, enrolled_date=date(2026, 10, 1)))
        db.add(StudentClass(student_id=8, class_id=2, enrolled_date=date(2026, 10, 1)))
        db.commit()
    app = FastAPI()
    app.include_router(router)
    from app.routers.timetable import router as timetable_router
    app.include_router(timetable_router)
    from app.routers.rooms import router as rooms_router
    app.include_router(rooms_router)
    from app.routers.notifications import router as notifications_router
    app.include_router(notifications_router)
    from app.routers.student_enrollments import router as student_router
    app.include_router(student_router)
    def database():
        with sessions() as db:
            yield db
    app.dependency_overrides[get_db] = database
    with TestClient(app) as client:
        def request(method, path="/sessions/1", actor=2, **kwargs):
            headers = {} if actor is None else {"Authorization": "Bearer " + create_access_token(actor, "TRAINING_MANAGER")}
            return client.request(method, path if path.startswith("/api/") else "/api/attendance" + path, headers=headers, **kwargs)
        yield request, sessions, clock, engine
    engine.dispose()


def payload(version=0, statuses=None):
    statuses = statuses or ["present"] * 4
    return {"expected_version": version, "items": [{"student_id": id, "status": status} for id, status in zip([4, 5, 6, 7], statuses)]}


def test_all_four_statuses_saved_and_edits_audited_with_actor_and_time(ctx):
    req, dbs, _, _ = ctx
    detail = req("GET").json()
    assert detail["can_edit"] and detail["version"] == 0
    assert len(detail["students"]) == 4 and all(item["status"] is None for item in detail["students"])
    body = payload(statuses=["present", "late", "excused_absence", "unexcused_absence"])
    first = req("PUT", json=body)
    assert first.status_code == 200, first.text
    assert first.json()["version"] == 1 and first.json()["changed"] == 4
    second = req("PUT", json=payload(1))
    assert second.status_code == 200 and second.json()["changed"] == 3
    logs = req("GET", "/sessions/1/history").json()
    assert logs["total"] == 7
    assert all(row["actor_id"] == 2 and row["actor_name"] == "Người 2" and row["created_at"] == "2026-10-16T03:00:00+00:00" for row in logs["items"])
    assert logs["items"][0]["from_status"] == "unexcused_absence" and logs["items"][0]["to_status"] == "present"
    assert req("PUT", json=payload(2)).json()["changed"] == 0
    assert req("GET").json()["version"] == 2
    assert req("GET", "/sessions/1/history").json()["total"] == 7
    with dbs() as db:
        assert db.query(SessionAttendance).count() == 4


@pytest.mark.parametrize("session,actor,code", [(2, 2, 403), (2, 1, 200), (2, 9, 403), (3, 2, 403), (3, 1, 403), (3, 9, 403)])
def test_past_needs_training_manager_and_future_is_never_editable(ctx, session, actor, code):
    req, _, _, _ = ctx
    before = req("GET", f"/sessions/{session}", actor=actor).json()
    assert before["can_edit"] == (code == 200)
    assert req("PUT", f"/sessions/{session}", actor=actor, json=payload()).status_code == code
    if code != 200:
        assert req("GET", f"/sessions/{session}/history", actor=actor).json()["total"] == 0


@pytest.mark.parametrize("actor,code", [(None, 401), (3, 404), (4, 403), (10, 403)])
def test_scope_applies_to_detail_write_and_history_even_with_forged_role_claim(ctx, actor, code):
    req, _, _, _ = ctx
    assert req("GET", actor=actor).status_code == code
    assert req("PUT", actor=actor, json=payload()).status_code == code
    assert req("GET", "/sessions/1/history", actor=actor).status_code == code


def test_list_today_and_class_filter_are_scoped_and_paginated(ctx):
    req, _, _, _ = ctx
    items = req("GET", "/sessions").json()
    assert [item["session_id"] for item in items["items"]] == [1, 5]
    assert items["today"] == "2026-10-16"
    assert req("GET", "/sessions", params={"class_id": 2}).json()["total"] == 0
    assert req("GET", "/sessions", actor=1).json()["total"] == 3
    assert req("GET", "/sessions", params={"page_size": 1, "page": 2}).json()["items"][0]["session_id"] == 5
    assert req("GET", "/sessions", params={"on_date": "2026-10-15"}).json()["items"][0]["session_id"] == 2
    assert req("GET", "/sessions", params={"on_date": "invalid"}).status_code == 422
    assert req("GET", "/sessions", params={"page_size": 101}).status_code == 422


def test_vietnam_midnight_rechecked_when_saving(ctx):
    req, _, clock, _ = ctx
    clock[0] = datetime(2026, 10, 15, 17)  # Oct 16 00:00 Vietnam
    assert req("GET").json()["can_edit"]
    clock[0] = datetime(2026, 10, 16, 16, 59, 59)
    assert req("PUT", json=payload()).status_code == 200
    clock[0] = datetime(2026, 10, 16, 17)
    assert not req("GET").json()["can_edit"]
    assert req("PUT", json=payload(1, ["late"] * 4)).status_code == 403
    assert req("GET", "/sessions").json()["items"][0]["session_id"] == 3


@pytest.mark.parametrize("body,code", [
    ({"expected_version": 0, "items": []}, 422),
    ({"expected_version": 0, "items": [{"student_id": 4, "status": "wrong"}]}, 422),
    ({"expected_version": 0, "items": [{"student_id": 4, "status": "present"}] * 2}, 422),
    ({"expected_version": 0, "items": [{"student_id": 4, "status": "present"}]}, 409),
    ({"expected_version": 0, "items": [{"student_id": 8, "status": "present"}]}, 409),
    ({**payload(), "expected_version": True}, 422),
    ({**payload(), "actor_id": 1}, 422),
])
def test_invalid_or_tampered_batch_is_atomic(ctx, body, code):
    req, dbs, _, _ = ctx
    assert req("PUT", json=body).status_code == code
    with dbs() as db:
        assert db.query(SessionAttendance).count() == 0 and db.query(AttendanceHistory).count() == 0
        assert db.get(ClassSession, 1).attendance_version == 0


def test_stale_revision_cannot_overwrite_or_append_audit(ctx):
    req, _, _, _ = ctx
    assert req("PUT", json=payload()).status_code == 200
    assert req("PUT", json=payload(0, ["late"] * 4)).status_code == 409
    assert all(student["status"] == "present" for student in req("GET").json()["students"])
    assert req("GET", "/sessions/1/history").json()["total"] == 4


def test_audit_failure_rolls_back_entire_batch_and_version(ctx):
    req, dbs, _, _ = ctx
    def fail(db, *_):
        if any(isinstance(item, AttendanceHistory) for item in db.new):
            raise RuntimeError("Audit storage unavailable")
    event.listen(Session, "before_flush", fail)
    try:
        with pytest.raises(RuntimeError, match="Audit storage"):
            req("PUT", json=payload())
    finally:
        event.remove(Session, "before_flush", fail)
    with dbs() as db:
        assert db.query(SessionAttendance).count() == 0
        assert db.query(AttendanceHistory).count() == 0
        assert db.get(ClassSession, 1).attendance_version == 0


def test_permission_revocation_reassignment_and_cancelled_class(ctx):
    req, dbs, _, _ = ctx
    with dbs() as db:
        db.query(RolePermission).filter_by(role_id=2, permission_id=2).delete()
        db.commit()
    assert not req("GET").json()["can_edit"]
    assert req("PUT", json=payload()).status_code == 403
    with dbs() as db:
        db.add(RolePermission(role_id=2, permission_id=2))
        db.get(ClassSubject, 1).instructor_id = 3
        db.commit()
    assert req("GET").status_code == 404  # Legacy class instructor still 2.
    assert req("PUT", json=payload()).status_code == 404
    assert req("PUT", actor=3, json=payload()).status_code == 200
    with dbs() as db:
        db.get(TrainingClass, 1).status = "cancelled"
        db.commit()
    assert req("PUT", actor=1, json=payload(1, ["late"] * 4)).status_code == 403


def test_prepare_session_and_enrollment_workflow_and_duplicates(ctx):
    req, dbs, _, _ = ctx
    with dbs() as db:
        db.get(ClassSession, 1).end_time = time(9)
        db.get(ClassSession, 5).end_time = time(11)
        db.commit()
    body = {"class_subject_id": 1, "session_date": "2026-10-16", "start_time": "14:00", "topic": "  Ôn tập  "}
    assert req("POST", "/sessions", json=body).status_code == 403
    new = req("POST", "/sessions", actor=1, json=body)
    assert new.status_code == 201 and new.json()["topic"] == "Ôn tập"
    assert req("POST", "/sessions", actor=1, json=body).status_code == 409
    assert req("POST", "/sessions", actor=1, json={**body, "session_date": "2026-09-30"}).status_code == 400
    assert req("POST", "/sessions", actor=1, json={**body, "start_time": "14:00+07:00"}).status_code == 422
    assert req("POST", "/classes/1/students", actor=1, json={"student_ids": [8]}).json()["added"] == 1
    assert req("POST", "/classes/1/students", actor=1, json={"student_ids": [8]}).status_code == 409
    assert req("POST", "/classes/1/students", json={"student_ids": [8]}).status_code == 403
    assert req("POST", "/classes/1/students", actor=1, json={"student_ids": [2]}).status_code == 422
    assert len(req("GET").json()["students"]) == 5
    assert len(req("GET", "/sessions/2", actor=1).json()["students"]) == 4  # Today's enrollment not backdated.
    candidates = req("GET", "/students", actor=1, params={"search": "HV8"}).json()
    assert [row["student_id"] for row in candidates["items"]] == [8]


def test_roster_and_names_remain_historical_after_first_save(ctx):
    req, dbs, _, engine = ctx
    req("PUT", json=payload())
    with dbs() as db:
        db.get(User, 4).full_name = "Đổi tên"
        db.query(StudentClass).filter_by(student_id=5, class_id=1).delete()
        db.add(StudentClass(student_id=8, class_id=1, enrolled_date=date(2026, 10, 16)))
        db.commit()
    detail = req("GET").json()
    assert {row["student_id"] for row in detail["students"]} == {4, 5, 6, 7}
    assert detail["students"][0]["student_name"] == "Người 4"
    Base.metadata.create_all(engine)  # Repeated startup does not alter records.
    assert req("GET", "/sessions/1/history").json()["total"] == 4
    page = req("GET", "/sessions/1/history", params={"page_size": 2, "page": 2}).json()
    assert len(page["items"]) == 2 and page["total"] == 4


def test_reference_permissions_seed_is_idempotent_and_keeps_revocations(ctx, monkeypatch):
    from app import database
    from app.migrations import seed_attendance_permissions
    _, sessions, _, _ = ctx
    monkeypatch.setattr(database, "SessionLocal", sessions)
    with sessions() as db:
        db.query(RolePermission).filter_by(role_id=2, permission_id=2).delete()
        db.commit()
    seed_attendance_permissions()
    seed_attendance_permissions()
    with sessions() as db:
        assert db.query(RolePermission).filter_by(role_id=2, permission_id=2).count() == 0
        assert db.query(Permission).count() == 2
        db.query(RolePermission).delete()
        db.query(Permission).delete()
        db.commit()
    seed_attendance_permissions()
    seed_attendance_permissions()
    with sessions() as db:
        assert db.query(Permission).count() == 2
        edit = db.query(Permission).filter_by(permission_name="ATTENDANCE_EDIT").one()
        granted = {row[0] for row in db.query(RolePermission.role_id).filter_by(permission_id=edit.permission_id)}
        assert granted == {1, 2, 4}
