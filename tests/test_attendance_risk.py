from datetime import date, time
from decimal import Decimal
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.database import get_db
from app.core.security import create_access_token
from app.crud import attendance_risk as crud
from app.models.attendance import ClassSession, SessionAttendance
from app.models.attendance_risk import AttendanceRisk, AttendanceRiskNotification, ProgramAbsencePolicy
from app.models.training_program import TrainingProgram, TrainingClass
from app.models.permission import Permission
from app.models.role_permission import RolePermission
from app.models.student_enrollment import StudentEnrollment
from app.models.user_role import UserRole
from app.models.subject import ClassSubject
from app.routers.attendance_risk import router as policy_router
from app.routers.classes import router as classes_router
from app.routers.student_enrollments import router as students_router
from app.routers.notifications import router as notifications_router
from test_attendance import ctx as base_ctx, payload


@pytest.fixture
def ctx(base_ctx):
    original, sessions, clock, engine = base_ctx
    with sessions() as db:
        for id, name in [(3, "CURRICULUM_MANAGE"), (4, "CLASS_MANAGE"), (5, "STUDENT_MANAGE")]:
            db.add(Permission(permission_id=id, permission_name=name))
            db.add(RolePermission(role_id=1, permission_id=id))
        db.add(RolePermission(role_id=4, permission_id=3))
        db.add(TrainingProgram(course_id=10, code="CT10", course_name="Chương trình 10"))
        db.flush()
        db.get(TrainingClass, 1).course_id = 10
        db.add(ClassSession(session_id=6, class_subject_id=1, session_date=date(2026, 10, 20), start_time=time(8), topic="Buổi thêm"))
        db.commit()
    app = FastAPI()
    for router in [policy_router, classes_router, students_router, notifications_router]:
        app.include_router(router)
    def dependency():
        with sessions() as db:
            yield db
    app.dependency_overrides[get_db] = dependency
    with TestClient(app) as client:
        def request(method, path="/api/attendance-risks/programs/10/policy", actor=1, **kwargs):
            headers = {} if actor is None else {"Authorization": "Bearer " + create_access_token(actor, "TRAINING_MANAGER")}
            return client.request(method, path, headers=headers, **kwargs)
        yield request, original, sessions, clock, engine


def mark(ctx, session=1, version=0, status="unexcused_absence"):
    return ctx[1]("PUT", f"/sessions/{session}", actor=1, json=payload(version, [status, "present", "present", "present"]))


def flagged(ctx):
    with ctx[2]() as db:
        return db.query(AttendanceRisk).filter_by(active=True).count()


def notices(ctx):
    with ctx[2]() as db:
        return db.query(AttendanceRiskNotification).count()


def test_default_strict_threshold_and_combined_absences_notify_once(ctx):
    assert Decimal(ctx[0]("GET").json()["threshold_percent"]) == 20
    assert mark(ctx).status_code == 200
    assert flagged(ctx) == 0 and notices(ctx) == 0  # 1/5 == 20%, not above
    assert mark(ctx, session=2, status="excused_absence").status_code == 200
    assert flagged(ctx) == 1 and notices(ctx) == 2
    with ctx[2]() as db:
        row = db.get(AttendanceRisk, (1, 4))
        assert (row.absent_sessions, row.scheduled_sessions, row.activation) == (2, 5, 1)
        assert {item.recipient_id for item in db.query(AttendanceRiskNotification)} == {1, 2}
    for _ in range(3):
        assert ctx[0]("GET", "/api/classes").status_code == 200
        assert ctx[0]("GET", "/api/notifications/summary").status_code == 200
    assert notices(ctx) == 2


def test_flags_on_class_list_student_list_and_profile(ctx):
    mark(ctx); mark(ctx, session=2)
    flags = next(row for row in ctx[0]("GET", "/api/classes").json()["items"] if row["class_id"] == 1)["attendance_risks"]
    assert flags[0]["student_id"] == 4 and flags[0]["absence_rate"] == 40
    assert ctx[0]("GET", "/api/students/4").json()["attendance_risks"][0]["threshold_percent"] == 20
    assert next(row for row in ctx[0]("GET", "/api/students").json()["items"] if row["student_id"] == 4)["attendance_risks"]
    assert ctx[0]("GET", "/api/students/5").json()["attendance_risks"] == []


def test_edit_clears_flag_and_later_recrossing_sends_one_new_activation(ctx):
    mark(ctx); mark(ctx, session=2)
    assert mark(ctx, version=1, status="present").status_code == 200
    assert flagged(ctx) == 0 and notices(ctx) == 2
    assert mark(ctx, version=2).status_code == 200
    assert flagged(ctx) == 1 and notices(ctx) == 4
    with ctx[2]() as db:
        assert db.get(AttendanceRisk, (1, 4)).activation == 2


def test_policy_change_recomputes_immediately_and_checks_expected_value(ctx):
    mark(ctx)
    assert ctx[0]("PUT", json={"threshold_percent": 19.99, "expected_threshold_percent": 20}).status_code == 200
    assert flagged(ctx) == 1 and notices(ctx) == 2
    assert ctx[0]("PUT", json={"threshold_percent": 30, "expected_threshold_percent": 20}).status_code == 409
    assert flagged(ctx) == 1
    assert ctx[0]("PUT", json={"threshold_percent": 20, "expected_threshold_percent": 19.99}).status_code == 200
    assert flagged(ctx) == 0
    assert ctx[0]("PUT", json={"threshold_percent": 0, "expected_threshold_percent": 20}).status_code == 200
    assert flagged(ctx) == 1 and notices(ctx) == 4


@pytest.mark.parametrize("threshold", [-1, 100.01, 20.123, True, "NaN", "Infinity"])
def test_invalid_threshold_is_rejected(ctx, threshold):
    assert ctx[0]("PUT", json={"threshold_percent": threshold, "expected_threshold_percent": 20}).status_code == 422


@pytest.mark.parametrize("actor", [2, 4, 9, 10])
def test_only_training_manager_with_permission_can_change_policy(ctx, actor):
    assert ctx[0]("PUT", actor=actor, json={"threshold_percent": 10, "expected_threshold_percent": 20}).status_code == 403
    assert ctx[0]("GET", actor=9).json()["can_edit"] is False


def test_permission_revocation_and_missing_program(ctx):
    assert ctx[0]("GET", "/api/attendance-risks/programs/999/policy").status_code == 404
    with ctx[2]() as db:
        db.query(RolePermission).filter_by(role_id=1, permission_id=3).delete()
        db.commit()
    assert ctx[0]("PUT", json={"threshold_percent": 10, "expected_threshold_percent": 20}).status_code == 403


def test_notification_inbox_read_ownership_and_student_privacy(ctx):
    mark(ctx); mark(ctx, session=2)
    items = ctx[0]("GET", "/api/notifications", actor=2).json()["items"]
    assert len(items) == 1 and items[0]["notification_id"].startswith("risk:")
    assert "2/5" in items[0]["message"] and "40.00%" in items[0]["message"]
    id = items[0]["notification_id"]
    assert ctx[0]("POST", f"/api/notifications/{id}/read", actor=4).status_code == 404
    assert ctx[0]("POST", f"/api/notifications/{id}/read", actor=2).json()["unread"] == 0
    assert ctx[0]("GET", "/api/notifications", actor=4).json()["items"] == []
    assert ctx[0]("GET", "/api/notifications", actor=3).json()["items"] == []


def test_future_absences_and_cancelled_sessions_do_not_count(ctx):
    mark(ctx)
    with ctx[2]() as db:
        db.add(SessionAttendance(session_id=3, student_id=4, student_name="N4", student_code="HV4", status="excused_absence"))
        db.get(ClassSession, 1).status = "cancelled"
        db.commit()
        crud.refresh(db)
    assert flagged(ctx) == 0
    # Day rolls over: future approved absence now counts, denominator excludes cancelled.
    from datetime import datetime
    ctx[3][0] = datetime(2026, 10, 17, 3)
    with ctx[2]() as db:
        crud.refresh(db)
    assert flagged(ctx) == 1


def test_schedule_change_can_turn_flag_on_and_off_without_attendance_edits(ctx):
    mark(ctx)
    with ctx[2]() as db:
        db.get(ClassSession, 6).status = "cancelled"
        db.commit()
        crud.refresh(db)
    assert flagged(ctx) == 1  # 1/4 = 25%
    with ctx[2]() as db:
        db.get(ClassSession, 6).status = "scheduled"
        db.commit()
        crud.refresh(db)
    assert flagged(ctx) == 0


@pytest.mark.parametrize("end_status", ["deferred", "transferred", "withdrawn"])
def test_inactive_membership_clears_flag_and_preserves_old_notifications(ctx, end_status):
    mark(ctx); mark(ctx, session=2)
    with ctx[2]() as db:
        db.add(StudentEnrollment(student_id=4, class_id=1, started_on=date(2026, 10, 1), ended_on=date(2026, 10, 16), end_status=end_status))
        db.commit()
        crud.refresh(db)
    assert flagged(ctx) == 0 and notices(ctx) == 2


def test_duplicate_membership_and_dual_staff_roles_do_not_duplicate_counts_or_notices(ctx):
    with ctx[2]() as db:
        db.add_all([StudentEnrollment(student_id=4, class_id=1, started_on=date(2026, 10, 1)) for _ in range(2)])
        db.add(UserRole(user_id=1, role_id=2))
        db.get(ClassSession, 5).substitute_instructor_id = 1
        db.commit()
    mark(ctx); mark(ctx, session=2)
    assert flagged(ctx) == 1 and notices(ctx) == 2


def test_program_policy_isolated_and_closed_classes_clear_flag(ctx):
    mark(ctx); mark(ctx, session=2)
    with ctx[2]() as db:
        db.add(TrainingProgram(course_id=20, code="CT20", course_name="Other"))
        db.add(ProgramAbsencePolicy(course_id=20, threshold_percent=100))
        db.commit()
        crud.refresh(db)
    assert flagged(ctx) == 1
    with ctx[2]() as db:
        db.get(TrainingClass, 1).status = "completed"
        db.commit()
        crud.refresh(db)
    assert flagged(ctx) == 0


def test_failed_refresh_rolls_back_flags_and_notifications_then_retries(ctx, monkeypatch):
    with ctx[2]() as db:
        for id in [1, 2]:
            db.add(SessionAttendance(session_id=id, student_id=4, student_name="N4", student_code="HV4", status="unexcused_absence"))
        db.commit()
        evaluate = crud.evaluate
        def fail_after_evaluation(session):
            evaluate(session)
            session.flush()
            raise RuntimeError("Simulated failure")
        monkeypatch.setattr(crud, "evaluate", fail_after_evaluation)
        with pytest.raises(RuntimeError):
            crud.refresh(db)
        assert db.query(AttendanceRisk).count() == 0
        assert db.query(AttendanceRiskNotification).count() == 0
        monkeypatch.setattr(crud, "evaluate", evaluate)
        crud.refresh(db)
    assert flagged(ctx) == 1 and notices(ctx) == 2


def test_parallel_reconciliation_notifies_once(ctx, tmp_path):
    import sqlite3
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    with ctx[2]() as db:
        for id in [1, 2]:
            db.add(SessionAttendance(session_id=id, student_id=4, student_name="N4", student_code="HV4", status="unexcused_absence"))
        db.commit()
    path = tmp_path / "risk-concurrency.db"
    source = ctx[4].raw_connection()
    with sqlite3.connect(path) as target:
        source.driver_connection.backup(target)
    source.close()
    engine = create_engine(f"sqlite:///{path.as_posix()}", connect_args={"check_same_thread": False, "timeout": 15})
    sessions = sessionmaker(bind=engine)
    barrier = Barrier(2)
    def run(_):
        with sessions() as db:
            barrier.wait(timeout=10)
            crud.refresh(db)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(run, [1, 2]))
    with sessions() as db:
        assert db.query(AttendanceRisk).filter_by(active=True).count() == 1
        assert db.query(AttendanceRiskNotification).count() == 2
    engine.dispose()


def test_revoked_roles_do_not_receive_new_alerts_even_if_legacy_role_remains(ctx):
    with ctx[2]() as db:
        for id in [1, 2]:
            db.add(SessionAttendance(session_id=id, student_id=4, student_name="N4", student_code="HV4", status="unexcused_absence"))
        db.query(UserRole).filter(UserRole.user_id.in_([1, 2])).delete(synchronize_session=False)
        db.commit()
        crud.refresh(db)
    assert flagged(ctx) == 1 and notices(ctx) == 0
