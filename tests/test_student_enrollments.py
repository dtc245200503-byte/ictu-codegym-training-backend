from datetime import date, datetime, time
import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session
from test_attendance import ctx as attendance_ctx, payload  # noqa: F401
from app.models.permission import Permission
from app.models.role_permission import RolePermission
from app.models.training_program import TrainingClass
from app.models.attendance import StudentClass, ClassSession, SessionAttendance, AttendanceHistory
from app.models.student_enrollment import StudentEnrollment, StudentEnrollmentHistory


@pytest.fixture
def ctx(attendance_ctx):
    req, sessions, clock, engine = attendance_ctx
    with sessions() as db:
        db.add(Permission(permission_id=3, permission_name="STUDENT_MANAGE"))
        db.flush()
        db.add(RolePermission(role_id=1, permission_id=3))
        db.commit()
    return req, sessions, clock, engine


def change(ctx, operation="deferred", day="2026-10-16", student=4, **extra):
    req = ctx[0]
    return req("POST", f"/api/students/{student}/changes", actor=1, json={
        "class_id": 1, "enrollment_id": None, "operation": operation, "effective_date": day,
        "reason": "  Lý do học tập  ", **extra})


def profile(ctx, student=4):
    return ctx[0]("GET", f"/api/students/{student}", actor=1).json()


def ids(ctx, session=1, actor=2):
    return {item["student_id"] for item in ctx[0]("GET", f"/sessions/{session}", actor=actor).json()["students"]}


@pytest.mark.parametrize("operation", ["deferred", "withdrawn"])
def test_effective_boundary_removes_student_but_keeps_previous_days(ctx, operation):
    req, sessions, _, _ = ctx
    result = change(ctx, operation)
    assert result.status_code == 201, result.text
    assert ids(ctx) == {5, 6, 7}
    assert ids(ctx, 2) == {4, 5, 6, 7}
    assert ids(ctx, 3) == {5, 6, 7}
    record = result.json()
    assert record["reason"] == "Lý do học tập" and record["effective_date"] == "2026-10-16"
    assert record["actor_id"] == 1 and record["actor_name"] == "Người 1"
    assert record["created_at"] == "2026-10-16T03:00:00+00:00"
    assert profile(ctx)["enrollments"][0]["status"] == operation
    with sessions() as db:
        assert db.get(StudentClass, (4, 1)) is not None


def test_future_decision_applies_by_date_without_background_job(ctx):
    _, _, clock, _ = ctx
    assert change(ctx, day="2026-10-17").status_code == 201
    assert 4 in ids(ctx, 1) and 4 not in ids(ctx, 3)
    assert profile(ctx)["enrollments"][0]["status"] == "active"
    assert not profile(ctx)["enrollments"][0]["can_change"]
    clock[0] = datetime(2026, 10, 16, 17)  # 00:00 Oct 17 in Vietnam.
    assert profile(ctx)["enrollments"][0]["status"] == "deferred"
    assert 4 in ids(ctx, 1)  # Historical session remains historical.


def test_saved_roster_changes_without_deleting_attendance_and_stale_save_rejected(ctx):
    req, sessions, _, _ = ctx
    assert req("PUT", json=payload()).status_code == 200
    assert change(ctx).status_code == 201
    result = req("GET").json()
    assert {row["student_id"] for row in result["students"]} == {5, 6, 7}
    assert result["archived_students"][0]["student_id"] == 4
    assert result["archived_students"][0]["status"] == "present"
    assert req("PUT", json=payload(1)).status_code == 409
    body = payload(1, ["late"] * 4)
    body["items"] = [row for row in body["items"] if row["student_id"] != 4]
    assert req("PUT", json=body).status_code == 200
    with sessions() as db:
        assert db.get(SessionAttendance, (1, 4)).status == "present"
        assert db.query(AttendanceHistory).filter_by(student_id=4).count() == 1
    old = req("GET", "/api/students/4/attendance", actor=1).json()
    assert old["total"] == 1 and old["items"][0]["class_id"] == 1


def test_transfer_preserves_grades_source_membership_and_saved_attendance(ctx):
    req, sessions, _, engine = ctx
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE legacy_grades (student_id INTEGER, class_id INTEGER, score INTEGER, FOREIGN KEY(student_id, class_id) REFERENCES student_classes(student_id, class_id))"))
        conn.execute(text("INSERT INTO legacy_grades VALUES (4, 1, 9)"))
    req("PUT", json=payload())
    req("PUT", "/sessions/2", actor=1, json=payload())
    assert req("PUT", "/sessions/4", actor=3, json={"expected_version": 0, "items": [{"student_id": 8, "status": "late"}]}).status_code == 200
    result = change(ctx, "transferred", to_class_id=2)
    assert result.status_code == 201, result.text
    assert 4 not in ids(ctx) and 4 in ids(ctx, 2)
    assert ids(ctx, 4, 3) == {4, 8}
    destination = req("GET", "/sessions/4", actor=3).json()
    assert next(row for row in destination["students"] if row["student_id"] == 4)["status"] is None
    assert req("PUT", "/sessions/4", actor=3, json={"expected_version": 1, "items": [{"student_id": 8, "status": "late"}]}).status_code == 409
    assert req("PUT", "/sessions/4", actor=3, json={"expected_version": 1, "items": [{"student_id": 8, "status": "late"}, {"student_id": 4, "status": "present"}]}).status_code == 200
    with sessions() as db:
        assert db.get(StudentClass, (4, 1)) and db.get(StudentClass, (4, 2))
        assert db.get(StudentClass, (4, 2)).enrolled_date == date(2026, 10, 16)
        assert db.get(SessionAttendance, (1, 4)).status == "present"
        assert db.query(AttendanceHistory).filter_by(session_id=1, student_id=4).count() == 1
        assert db.execute(text("SELECT student_id, class_id, score FROM legacy_grades")).one() == (4, 1, 9)
    old = req("GET", "/api/students/4/attendance", actor=1).json()
    assert old["total"] == 3


def test_transfer_back_creates_separate_interval_instead_of_rewriting_original(ctx):
    req, sessions, _, _ = ctx
    assert change(ctx, "transferred", to_class_id=2).status_code == 201
    destination = next(row for row in profile(ctx)["enrollments"] if row["class_id"] == 2)
    response = change(ctx, "transferred", day="2026-10-18", class_id=2, enrollment_id=destination["enrollment_id"], to_class_id=1)
    assert response.status_code == 201, response.text
    with sessions() as db:
        db.add(ClassSession(session_id=6, class_subject_id=1, session_date=date(2026, 10, 18), start_time=time(8), topic="Quay lại"))
        db.commit()
        assert db.query(StudentEnrollment).filter_by(student_id=4, class_id=1).count() == 2
        assert db.get(StudentClass, (4, 1)).enrolled_date == date(2026, 10, 1)
    assert 4 in ids(ctx, 2) and 4 not in ids(ctx, 1) and 4 in ids(ctx, 6)
    assert req("GET", "/api/students/4/history", actor=1).json()["total"] == 2


@pytest.mark.parametrize("extra,code", [
    ({"reason": ""}, 422), ({"reason": "  "}, 422), ({"reason": "x"*1001}, 422),
    ({"effective_date": "wrong"}, 422), ({"effective_date": "0001-01-01"}, 422),
    ({"effective_date": "2026-09-30"}, 422), ({"operation": "unknown"}, 422),
    ({"operation": "transferred"}, 422), ({"to_class_id": 2}, 422),
    ({"operation": "transferred", "to_class_id": 1}, 422),
    ({"operation": "transferred", "to_class_id": 9999}, 404),
    ({"enrollment_id": 999}, 409), ({"enrollment_id": True}, 422), ({"actor_id": 2}, 422),
])
def test_invalid_operations_leave_membership_and_history_unchanged(ctx, extra, code):
    req, sessions, _, _ = ctx
    result = change(ctx, **extra)
    assert result.status_code == code, result.text
    with sessions() as db:
        assert db.query(StudentEnrollment).count() == 0
        assert db.query(StudentEnrollmentHistory).count() == 0
        assert db.query(StudentClass).count() == 5


def test_duplicate_conflicting_actions_and_preparation_cannot_undo_decision(ctx):
    req, sessions, _, _ = ctx
    assert change(ctx, day="2026-10-17").status_code == 201
    assert change(ctx, "withdrawn").status_code == 409
    assert change(ctx, "transferred", to_class_id=2).status_code == 409
    assert req("POST", "/classes/1/students", actor=1, json={"student_ids": [4, 8]}).status_code == 409
    with sessions() as db:
        assert db.query(StudentEnrollmentHistory).count() == 1
        assert db.get(StudentClass, (8, 1)) is None


@pytest.mark.parametrize("target_status", ["cancelled", "completed"])
def test_invalid_target_class(ctx, target_status):
    _, sessions, _, _ = ctx
    with sessions() as db:
        db.get(TrainingClass, 2).status = target_status
        db.commit()
    assert change(ctx, "transferred", to_class_id=2).status_code == 422


def test_existing_destination_or_before_target_start_cannot_transfer(ctx):
    _, sessions, _, _ = ctx
    with sessions() as db:
        db.get(TrainingClass, 2).start_date = date(2026, 11, 1)
        db.commit()
    assert change(ctx, "transferred", to_class_id=2).status_code == 422
    with sessions() as db:
        db.get(TrainingClass, 2).start_date = date(2026, 10, 1)
        db.add(StudentClass(student_id=4, class_id=2, enrolled_date=date(2026, 10, 1)))
        db.commit()
    assert change(ctx, "transferred", to_class_id=2).status_code == 409


@pytest.mark.parametrize("actor,code", [(None, 401), (2, 403), (4, 403), (9, 403)])
def test_manager_role_and_permission_required_for_all_profile_routes(ctx, actor, code):
    req, sessions, _, _ = ctx
    if actor is not None:
        with sessions() as db:
            role = {2: 2, 4: 3, 9: 4}[actor]
            db.add(RolePermission(role_id=role, permission_id=3))
            db.commit()
    for url in ["/api/students", "/api/students/classes", "/api/students/4", "/api/students/4/history", "/api/students/4/attendance"]:
        assert req("GET", url, actor=actor).status_code == code
    assert req("POST", "/api/students/4/changes", actor=actor, json={
        "class_id": 1, "enrollment_id": None, "operation": "withdrawn",
        "effective_date": "2026-10-16", "reason": "test"}).status_code == code


def test_audit_failure_rolls_back_source_and_destination(ctx):
    req, sessions, _, _ = ctx
    def fail(session, *_):
        if any(isinstance(row, StudentEnrollmentHistory) for row in session.new):
            raise RuntimeError("Profile audit unavailable")
    event.listen(Session, "before_flush", fail)
    try:
        with pytest.raises(RuntimeError, match="Profile audit"):
            change(ctx, "transferred", to_class_id=2)
    finally:
        event.remove(Session, "before_flush", fail)
    with sessions() as db:
        assert db.query(StudentEnrollment).count() == 0
        assert db.query(StudentEnrollmentHistory).count() == 0
        assert db.get(StudentClass, (4, 2)) is None
    assert 4 in ids(ctx)


def test_history_retains_snapshot_names_and_can_paginate(ctx):
    req, sessions, _, _ = ctx
    change(ctx, "transferred", to_class_id=2)
    destination = next(row for row in profile(ctx)["enrollments"] if row["class_id"] == 2)
    change(ctx, "withdrawn", day="2026-10-17", class_id=2, enrollment_id=destination["enrollment_id"])
    with sessions() as db:
        db.get(TrainingClass, 1).class_name = "Tên lớp mới"
        db.commit()
    logs = req("GET", "/api/students/4/history", actor=1, params={"page": 2, "page_size": 1}).json()
    assert logs["total"] == 2 and logs["items"][0]["from_class_name"] == "Lớp 1"
    assert req("GET", "/api/students", actor=1, params={"search": "HV4", "class_id": 1}).json()["total"] == 1
    assert req("GET", "/api/students", actor=1, params={"search": "HV4", "class_id": 2}).json()["total"] == 1
    with sessions() as db:
        db.query(RolePermission).filter_by(role_id=1, permission_id=3).delete()
        db.commit()
    assert req("GET", "/api/students/4", actor=1).status_code == 403
