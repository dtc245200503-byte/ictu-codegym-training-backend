from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import create_access_token
from app.database import Base, get_db
from app.models.permission import Permission
from app.models.role import Role
from app.models.role_permission import RolePermission
from app.models.user import User
from app.models.user_role import UserRole
from app.models.subject import ClassSubject, ClassSubjectAssistant, Subject
from app.models.training_program import TrainingClass
from app.routers.classes import router
from app.migrations import ensure_class_assignment_schema, seed_class_permissions_and_roles


@pytest.fixture
def assignments():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    @event.listens_for(engine, 'connect')
    def enable_foreign_keys(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)
    names = ['manager', 'teacher', 'other_teacher', 'assistant', 'other_assistant', 'student', 'admin', 'dual', 'locked', 'temporary_locked']
    role_ids = [1, 2, 2, 3, 3, 4, 5, 2, 2, 3]
    with session() as db:
        db.add_all([Role(role_id=i + 1, role_name=name) for i, name in enumerate(['TRAINING_MANAGER', 'INSTRUCTOR', 'ASSISTANT', 'STUDENT', 'ADMIN'])])
        db.add(Permission(permission_id=1, permission_name='CLASS_MANAGE'))
        db.flush()
        db.add_all([RolePermission(role_id=role, permission_id=1) for role in [1, 2, 5]])
        for i, (name, role_id) in enumerate(zip(names, role_ids), 1):
            db.add(User(user_id=i, username=name, full_name=name, email=name+'@example.com', password='unused', role_id=role_id,
                        is_locked=name == 'locked', locked_until=datetime.now()+timedelta(hours=1) if name == 'temporary_locked' else None))
        db.flush()
        db.add_all([UserRole(user_id=i, role_id=role) for i, role in enumerate(role_ids, 1)])
        db.add(UserRole(user_id=8, role_id=3))
        db.add_all([Subject(id=i, code=f'S{i}', subject_name=f'Môn {i}', session_count=3) for i in [1, 2, 3]])
        db.add_all([TrainingClass(class_id=i, class_name=f'Lớp {i}', instructor_id=2 if i == 1 else None) for i in [1, 2]])
        db.flush()
        db.add_all([ClassSubject(id=1, class_id=1, subject_id=1), ClassSubject(id=2, class_id=1, subject_id=2), ClassSubject(id=3, class_id=2, subject_id=3)])
        db.commit()
    app = FastAPI()
    app.include_router(router)
    def get_test_db():
        with session() as db:
            yield db
    app.dependency_overrides[get_db] = get_test_db
    with TestClient(app) as client:
        def request(method, path='/api/classes', actor='manager', **kwargs):
            headers = {} if actor is None else {'Authorization': 'Bearer '+create_access_token(names.index(actor) + 1, 'ADMIN')}
            return client.request(method, path, headers=headers, **kwargs)
        yield request, session
    engine.dispose()


def put(request, instructor=2, assistants=None, subject=1):
    return request('PUT', f'/api/classes/1/subjects/{subject}/assignment', json={'instructor_id': instructor, 'assistant_ids': assistants or []})


def test_one_instructor_multiple_assistants_and_atomic_batch(assignments):
    request, session = assignments
    response = request('PUT', '/api/classes/1/assignments', json={'assignments': [
        {'subject_id': 1, 'instructor_id': 2, 'assistant_ids': [4, 5]},
        {'subject_id': 2, 'instructor_id': 3, 'assistant_ids': [4]},
    ]})
    assert response.status_code == 200, response.text
    subjects = response.json()['subjects']
    assert [s['instructor_id'] for s in subjects] == [2, 3]
    assert {u['user_id'] for u in subjects[0]['assistants']} == {4, 5}
    invalid = request('PUT', '/api/classes/1/assignments', json={'assignments': [
        {'subject_id': 1, 'instructor_id': 3, 'assistant_ids': []},
        {'subject_id': 2, 'instructor_id': 6, 'assistant_ids': []},
    ]})
    assert invalid.status_code == 422
    with session() as db:
        assert db.get(ClassSubject, 1).instructor_id == 2
        assert db.query(ClassSubjectAssistant).count() == 3


def test_assignment_changes_visibility_and_replacement_revokes_legacy_access(assignments):
    request, _ = assignments
    assert request('GET', actor='teacher').json()['total'] == 0  # Legacy class teacher is not a subject assignment.
    assert put(request, assistants=[4, 5]).status_code == 200
    for actor in ['teacher', 'assistant', 'other_assistant']:
        assert request('GET', actor=actor).json()['total'] == 1
    assert request('GET', actor='other_teacher').json()['total'] == 0
    assert put(request, instructor=3, assistants=[5]).status_code == 200
    for actor in ['teacher', 'assistant']:
        assert request('GET', actor=actor).json()['total'] == 0
    assert request('GET', actor='other_teacher', params={'search': 'other_teacher'}).json()['total'] == 1
    assert request('GET', actor='other_teacher', params={'search': 'Lớp 2'}).json()['total'] == 0


def test_candidates_exclude_wrong_role_locked_and_temporary_locked(assignments):
    request, _ = assignments
    result = request('GET', '/api/classes/candidates').json()
    assert {u['user_id'] for u in result['instructors']} == {2, 3, 8}
    assert {u['user_id'] for u in result['assistants']} == {4, 5, 8}


@pytest.mark.parametrize('actor', ['teacher', 'assistant', 'student'])
def test_only_managers_can_fetch_candidates_or_write_even_with_forged_token_role(assignments, actor):
    request, _ = assignments
    for method, url, body in [
        ('GET', '/api/classes/candidates', None), ('GET', '/api/classes/1/assignments', None),
        ('PUT', '/api/classes/1/subjects/1/assignment', {'instructor_id': 2}),
        ('PUT', '/api/classes/1/assignments', {'assignments': [{'subject_id': 1, 'instructor_id': 2}]}),
    ]:
        assert request(method, url, actor=actor, **({'json': body} if body else {})).status_code == 403


def test_manager_requires_permission_and_assistants_need_no_management_grant(assignments):
    request, session = assignments
    put(request, assistants=[4])
    assert request('GET', actor='assistant').status_code == 200
    with session() as db:
        db.query(RolePermission).filter_by(role_id=1).delete(); db.commit()
    assert request('GET').status_code == 403
    assert put(request).status_code == 403
    assert request('GET', '/api/classes/1/assignments', actor='admin').status_code == 200
    assert request('GET', actor=None).status_code in (401, 403)


@pytest.mark.parametrize('body,status', [
    ({}, 422), ({'instructor_id': None}, 422), ({'instructor_id': 0}, 422),
    ({'instructor_id': True}, 422), ({'instructor_id': '2'}, 422),
    ({'instructor_id': 6}, 422), ({'instructor_id': 9999}, 404), ({'instructor_id': 9}, 422),
    ({'instructor_id': 2, 'assistant_ids': [6]}, 422),
    ({'instructor_id': 2, 'assistant_ids': [10]}, 422),
    ({'instructor_id': 2, 'assistant_ids': [9999]}, 404),
    ({'instructor_id': 8, 'assistant_ids': [8]}, 422),
    ({'instructor_id': 2, 'assistant_ids': [-1]}, 422),
    ({'instructor_id': 2, 'class_id': 2}, 422),
])
def test_invalid_assignments_leave_data_unchanged(assignments, body, status):
    request, session = assignments
    assert request('PUT', '/api/classes/1/subjects/1/assignment', json=body).status_code == status
    with session() as db:
        assert db.get(ClassSubject, 1).instructor_id is None
        assert db.query(ClassSubjectAssistant).count() == 0


def test_multiple_roles_are_read_from_memberships_and_repeated_saves_are_idempotent(assignments):
    request, session = assignments
    assert put(request, instructor=8, assistants=[4, 4, 5]).status_code == 200
    assert put(request, instructor=8, assistants=[4, 4, 5]).status_code == 200
    with session() as db:
        assert db.query(ClassSubjectAssistant).count() == 2
        db.query(UserRole).filter_by(user_id=8, role_id=2).delete(); db.commit()
    assert put(request, instructor=8).status_code == 422  # Primary role_id still says instructor.
    assert 8 not in {u['user_id'] for u in request('GET', '/api/classes/candidates').json()['instructors']}


def test_subject_membership_missing_classes_and_duplicate_batch_items(assignments):
    request, _ = assignments
    assert put(request, subject=3).status_code == 404
    assert request('PUT', '/api/classes/999/subjects/1/assignment', json={'instructor_id': 2}).status_code == 404
    assert request('PUT', '/api/classes/1/assignments', json={'assignments': []}).status_code == 422
    item = {'subject_id': 1, 'instructor_id': 2}
    assert request('PUT', '/api/classes/1/assignments', json={'assignments': [item, item]}).status_code == 422


def test_migration_backfills_legacy_teachers_once_and_preserves_reassignments():
    engine = create_engine('sqlite://')
    with engine.begin() as connection:
        for sql in [
            'CREATE TABLE users (user_id INTEGER PRIMARY KEY)',
            'CREATE TABLE roles (role_id INTEGER PRIMARY KEY, role_name TEXT)',
            'CREATE TABLE user_roles (user_id INTEGER, role_id INTEGER)',
            'CREATE TABLE classes (class_id INTEGER PRIMARY KEY, instructor_id INTEGER)',
            'CREATE TABLE class_subjects (id INTEGER PRIMARY KEY, class_id INTEGER, subject_id INTEGER)',
            'INSERT INTO users VALUES (1), (2)', "INSERT INTO roles VALUES (1, 'INSTRUCTOR')",
            'INSERT INTO user_roles VALUES (1, 1), (2, 1)', 'INSERT INTO classes VALUES (1, 1)',
            'INSERT INTO class_subjects VALUES (1, 1, 1)',
        ]:
            connection.execute(text(sql))
    ensure_class_assignment_schema(engine)
    with engine.begin() as connection:
        assert connection.execute(text('SELECT instructor_id FROM class_subjects')).scalar() == 1
        connection.execute(text('UPDATE class_subjects SET instructor_id = 2'))
    ensure_class_assignment_schema(engine)
    assert 'class_subject_assistants' in inspect(engine).get_table_names()
    with engine.connect() as connection:
        assert connection.execute(text('SELECT instructor_id FROM class_subjects')).scalar() == 2
    engine.dispose()


def test_reference_seed_never_creates_demo_accounts_or_restores_revoked_grants(assignments, monkeypatch):
    _, session = assignments
    monkeypatch.setattr('app.database.SessionLocal', session)
    with session() as db:
        count = db.query(User).count()
        db.query(RolePermission).filter_by(role_id=1).delete(); db.commit()
    seed_class_permissions_and_roles()
    seed_class_permissions_and_roles()
    with session() as db:
        assert db.query(User).count() == count
        assert db.query(RolePermission).filter_by(role_id=1).count() == 0


def test_assistant_import_alias_uses_database_role_id(assignments):
    from app.routers.users import resolve_role_info
    _, session = assignments
    with session() as db:
        for label in ['ASSISTANT', 'Trợ giảng', 'TA']:
            assert resolve_role_info(label, db) == (3, 'Trợ giảng', 'ASSISTANT')
