from datetime import timedelta
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from app.database import get_db
from app.core.security import create_access_token
from app.models.assignment import Assignment, AssignmentRecipient
from app.models.assignment_progress import AssignmentProgress
from app.models.role_permission import RolePermission
from app.models.user_role import UserRole
from app.routers.student_assignments import router as mine_router
from app.routers.assignments import router as assignments_router
from app.main import app as application
from test_assignments import ctx as assignment_ctx, attendance_ctx


@pytest.fixture
def ctx(assignment_ctx):
    _, sessions, clock, _ = assignment_ctx
    app = FastAPI()
    app.include_router(mine_router)
    app.include_router(assignments_router)
    def database():
        with sessions() as db:
            yield db
    app.dependency_overrides[get_db] = database
    with TestClient(app) as client:
        def req(path='/mine', actor=4, **params):
            headers = {} if actor is None else {'Authorization': 'Bearer ' + create_access_token(actor, 'STUDENT')}
            return client.get('/api/assignments' + path, headers=headers, params=params)
        yield req, sessions, clock


def give(ctx, hours=48, status=None, student=4, published=True, title='Bài tập'):
    now = ctx[2][0]
    with ctx[1]() as db:
        row = Assignment(class_subject_id=1, session_id=1, author_id=2, title=title, description='Đề bài', due_at=now + timedelta(hours=hours),
            max_points=10, status='published' if published else 'draft', created_at=now, updated_at=now, published_at=now if published else None)
        db.add(row); db.flush()
        recipient = AssignmentRecipient(assignment_id=row.assignment_id, student_id=student, title=title, message='Bài mới', created_at=now)
        db.add(recipient); db.flush()
        if status:
            db.add(AssignmentProgress(recipient_id=recipient.recipient_id, status=status, submitted_at=now - timedelta(hours=1),
                reviewed_at=now if status != 'submitted' else None, updated_at=now))
        id = row.assignment_id
        db.commit()
    return id


def test_default_group_and_four_counts_use_only_own_published_assignments(ctx):
    id = give(ctx)
    for state in ['submitted', 'graded', 'needs_revision']:
        give(ctx, status=state)
    give(ctx, student=5); give(ctx, published=False)
    response = ctx[0]()
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['counts'] == dict.fromkeys(['not_submitted','submitted','graded','needs_revision'], 1)
    assert body['status'] == 'not_submitted' and body['sort'] == 'due_asc'
    assert [row['assignment_id'] for row in body['items']] == [id]
    assert body['total'] == 1 and body['items'][0]['progress_status'] == 'not_submitted'
    assert body['now'] == '2026-10-16T03:00:00+00:00'
    assert body['urgent_window_hours'] == 24


def test_default_sort_is_global_before_pagination_and_deterministic_on_ties(ctx):
    ids = [give(ctx, hours=hours) for hours in [20, 4, -1, 4, 50]]
    first = ctx[0](page_size=2).json()
    second = ctx[0](page=2, page_size=2).json()
    assert [r['assignment_id'] for r in first['items']] == [ids[2], ids[1]]
    assert [r['assignment_id'] for r in second['items']] == [ids[3], ids[0]]
    assert first['counts']['not_submitted'] == 5
    assert [r['assignment_id'] for r in ctx[0](sort='due_desc').json()['items']] == [ids[4], ids[0], ids[1], ids[3], ids[2]]
    assert ctx[0](page=99, page_size=2).json()['page'] == 3


@pytest.mark.parametrize('hours,urgency', [(-1, 'overdue'), (0, 'overdue'), (0.001, 'due_soon'), (24, 'due_soon'), (24.001, 'normal')])
def test_deadline_boundaries_and_remaining_seconds(ctx, hours, urgency):
    give(ctx, hours=hours)
    item = ctx[0]().json()['items'][0]
    assert item['urgency'] == urgency
    from math import ceil
    assert item['remaining_seconds'] == ceil(hours * 3600)


@pytest.mark.parametrize('state', ['submitted', 'graded'])
def test_completed_actions_are_never_marked_urgent_even_after_deadline(ctx, state):
    give(ctx, hours=-2, status=state)
    body = ctx[0](status=state).json()
    assert body['total'] == 1 and body['items'][0]['urgency'] == 'normal'
    assert body['items'][0]['remaining_seconds'] == -7200


def test_needs_revision_is_actionable_and_resubmission_moves_to_submitted(ctx):
    id = give(ctx, hours=1, status='needs_revision')
    assert ctx[0](status='needs_revision').json()['items'][0]['urgency'] == 'due_soon'
    with ctx[1]() as db:
        progress = db.query(AssignmentProgress).one()
        progress.status = 'submitted'; progress.reviewed_at = None; progress.submitted_at = ctx[2][0]
        db.commit()
    body = ctx[0](status='submitted').json()
    assert body['counts']['needs_revision'] == 0
    assert body['items'][0]['assignment_id'] == id
    assert body['items'][0]['urgency'] == 'normal'


def test_opening_or_reading_does_not_mark_work_submitted(ctx):
    id = give(ctx)
    for _ in range(2):
        assert ctx[0]().json()['counts']['not_submitted'] == 1
        assert ctx[0](f'/mine/{id}').status_code == 200
    with ctx[1]() as db:
        assert db.query(AssignmentProgress).count() == 0


def test_no_cross_student_detail_or_count_leak_even_with_dual_role_and_forged_params(ctx):
    mine = give(ctx)
    other = give(ctx, student=5)
    draft = give(ctx, published=False)
    with ctx[1]() as db:
        db.add(UserRole(user_id=2, role_id=3)); db.commit()
    assert ctx[0](student_id=5).json()['total'] == 1
    assert ctx[0](f'/mine/{mine}').json()['can_edit'] is False
    for id in [other, draft, 999]:
        assert ctx[0](f'/mine/{id}').status_code == 404
    assert ctx[0](actor=2).json()['total'] == 0  # Author but not a recipient.
    assert ctx[0](f'/mine/{mine}', actor=2).status_code == 404


@pytest.mark.parametrize('actor,code', [(None, 401), (1, 403), (2, 403), (9, 403), (10, 403)])
def test_student_role_required_even_with_view_permission(ctx, actor, code):
    assert ctx[0](actor=actor).status_code == code


def test_revocation_applies_without_trusting_legacy_role(ctx):
    give(ctx)
    with ctx[1]() as db:
        db.query(UserRole).filter_by(user_id=4).delete(); db.commit()
    assert ctx[0]().status_code == 403
    with ctx[1]() as db:
        db.add(UserRole(user_id=4, role_id=3))
        db.query(RolePermission).filter_by(role_id=3, permission_id=10).delete(); db.commit()
    assert ctx[0]().status_code == 403


@pytest.mark.parametrize('params', [{'status':'fake'}, {'sort':'name'}, {'page':0}, {'page_size':101}])
def test_invalid_filters_rejected(ctx, params):
    assert ctx[0](**params).status_code == 422


def test_status_and_clock_refresh_empty_state_and_route_order(ctx):
    empty = ctx[0]().json()
    assert empty['items'] == [] and all(n == 0 for n in empty['counts'].values()) and empty['page'] == 1
    give(ctx, hours=1)
    ctx[2][0] += timedelta(hours=2)
    assert ctx[0]().json()['items'][0]['urgency'] == 'overdue'
    def database():
        with ctx[1]() as db:
            yield db
    application.dependency_overrides[get_db] = database
    try:
        client = TestClient(application)
        response = client.get('/api/assignments/mine', headers={
            'Authorization': 'Bearer ' + create_access_token(4, 'STUDENT')})
        assert response.status_code == 200, response.text  # /mine must not match /{id}.
        assert response.json()['items'][0]['urgency'] == 'overdue'
        client.close()
    finally:
        application.dependency_overrides.pop(get_db, None)


def test_progress_integrity_rejects_unreviewed_graded_state(ctx):
    give(ctx)
    with ctx[1]() as db:
        recipient = db.query(AssignmentRecipient).one()
        db.add(AssignmentProgress(recipient_id=recipient.recipient_id, status='graded', submitted_at=ctx[2][0], updated_at=ctx[2][0]))
        with pytest.raises(IntegrityError): db.commit()
        db.rollback()
