from datetime import date, datetime
from urllib.parse import unquote
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session
from app.database import get_db
from app.core.security import create_access_token
from app.crud import assignments as crud
from app.models.assignment import Assignment, AssignmentRecipient, AssignmentAttachment
from app.models.attendance import ClassSession, StudentClass
from app.models.permission import Permission
from app.models.role_permission import RolePermission
from app.models.student_enrollment import StudentEnrollment
from app.models.subject import ClassSubject
from app.models.training_program import TrainingClass
from app.models.user_role import UserRole
from app.routers.assignments import router
from app.routers.notifications import router as notifications_router
from test_attendance import ctx as attendance_ctx


@pytest.fixture
def ctx(attendance_ctx):
    _, sessions, clock, engine = attendance_ctx
    with sessions() as db:
        db.add_all([Permission(permission_id=10, permission_name='ASSIGNMENT_VIEW'), Permission(permission_id=11, permission_name='ASSIGNMENT_MANAGE')])
        db.flush()
        # Deliberately grant every role: role checks must still prevent misuse.
        for role in range(1, 6):
            for permission in [10, 11]:
                db.add(RolePermission(role_id=role, permission_id=permission))
        db.commit()
    app = FastAPI()
    app.include_router(router)
    app.include_router(notifications_router)
    def dependency():
        with sessions() as db:
            yield db
    app.dependency_overrides[get_db] = dependency
    with TestClient(app) as client:
        def req(method='GET', path='', actor=2, **kwargs):
            headers = {} if actor is None else {'Authorization': 'Bearer ' + create_access_token(actor, 'INSTRUCTOR')}
            return client.request(method, path if path.startswith('/api/') else '/api/assignments' + path, headers=headers, **kwargs)
        yield req, sessions, clock, engine


def data(**changes):
    return {'class_subject_id': 1, 'session_id': 1, 'title': 'Bài Python', 'description': '**Yêu cầu**\n- Viết hàm\n[Tham khảo](https://example.com)',
            'due_at': '2026-10-20T20:00:00+07:00', 'max_points': '10.50', **changes}


def draft(ctx, **changes):
    result = ctx[0]('POST', json=data(**changes))
    assert result.status_code == 201, result.text
    return result.json()


def publish(ctx, row, **kwargs):
    return ctx[0]('POST', f"/{row['assignment_id']}/publish", json={'expected_version': row['version']}, **kwargs)


def test_draft_is_private_and_publish_delivers_once_to_entire_class(ctx):
    req, sessions, _, _ = ctx
    row = draft(ctx)
    assert row['status'] == 'draft' and row['recipient_count'] == 0
    assert row['due_at'] == '2026-10-20T13:00:00+00:00'
    assert row['max_points'] == '10.50'
    for actor in [3, 4, 8]:
        assert req(path=f"/{row['assignment_id']}", actor=actor).status_code == 404
        assert req(actor=actor).json()['total'] == 0
    with sessions() as db:
        assert db.query(AssignmentRecipient).count() == 0
    result = publish(ctx, row)
    assert result.status_code == 200, result.text
    assert result.json()['status'] == 'published' and not result.json()['can_edit']
    assert result.json()['recipient_count'] == 4
    assert publish(ctx, row).status_code == 200
    for actor in [4, 5, 6, 7]:
        detail = req(path=f"/{row['assignment_id']}", actor=actor)
        assert detail.status_code == 200 and detail.json()['description'] == data()['description']
        inbox = req(path='/api/notifications', actor=actor).json()['items']
        assert len(inbox) == 1 and inbox[0]['notification_id'].startswith('assignment:')
        assert inbox[0]['target_url'] == f"/assignments?assignment_id={row['assignment_id']}"
        assert '20:00 20/10/2026' in inbox[0]['message']
    with sessions() as db:
        assert db.query(AssignmentRecipient).count() == 4
    assert req(path=f"/{row['assignment_id']}", actor=8).status_code == 404
    assert req(path='/api/notifications', actor=8).json()['total'] == 0


def test_notification_read_is_owner_only(ctx):
    row = draft(ctx); publish(ctx, row)
    item = ctx[0](path='/api/notifications', actor=4).json()['items'][0]
    assert ctx[0]('POST', '/api/notifications/' + item['notification_id'] + '/read', actor=5).status_code == 404
    assert ctx[0]('POST', '/api/notifications/' + item['notification_id'] + '/read', actor=4).json()['unread'] == 0


@pytest.mark.parametrize('change', [
    {'title': '   '}, {'description': ''}, {'max_points': 0}, {'max_points': -1}, {'max_points': '10.123'},
    {'max_points': 'NaN'}, {'max_points': 10001}, {'due_at': '2026-10-16T09:59:00+07:00'},
    {'due_at': '2026-10-16T10:00:00+07:00'}, {'due_at': '2026-10-20T20:00:00'}, {'session_id': True},
])
def test_invalid_fields_and_nonfuture_deadline(ctx, change):
    assert ctx[0]('POST', json=data(**change)).status_code == 422
    with ctx[1]() as db:
        assert db.query(Assignment).count() == 0


@pytest.mark.parametrize('actor,code', [(None, 401), (1, 403), (4, 403), (9, 403), (10, 403), (3, 404)])
def test_creation_requires_teaching_role_permission_and_assignment(ctx, actor, code):
    assert ctx[0]('POST', actor=actor, json=data()).status_code == code


def test_session_must_belong_to_subject_and_substitute_is_authoritative(ctx):
    assert ctx[0]('POST', json=data(session_id=4)).status_code == 404
    with ctx[1]() as db:
        db.get(ClassSession, 1).substitute_instructor_id = 3
        db.commit()
    assert ctx[0]('POST', json=data()).status_code == 404
    assert ctx[0]('POST', actor=3, json=data()).status_code == 201
    ids = [row['session_id'] for row in ctx[0](path='/options').json()['sessions']]
    assert 1 not in ids and 2 in ids


def test_draft_update_conflicts_and_publication_is_immutable(ctx):
    row = draft(ctx); id = row['assignment_id']
    update = {**data(title='Đã sửa'), 'expected_version': 1}
    result = ctx[0]('PUT', f'/{id}', json=update)
    assert result.status_code == 200 and result.json()['version'] == 2
    assert ctx[0]('PUT', f'/{id}', json=update).status_code == 409
    assert publish(ctx, row).status_code == 409
    assert publish(ctx, result.json()).status_code == 200
    assert ctx[0]('PUT', f'/{id}', json={**update, 'expected_version': 3}).status_code == 409
    assert ctx[0]('POST', f'/{id}/attachments?expected_version=3', files={'file': ('x.txt', b'abc')}).status_code == 409


def test_publish_rechecks_deadline_without_sending_any_notifications(ctx):
    row = draft(ctx)
    ctx[2][0] = datetime(2026, 10, 20, 13)
    assert publish(ctx, row).status_code == 422
    current = ctx[0](path=f"/{row['assignment_id']}").json()
    assert current['status'] == 'draft' and current['version'] == 1 and current['recipient_count'] == 0


@pytest.mark.parametrize('change', ['role', 'permission', 'assignment', 'cancelled_session', 'completed_class'])
def test_authorization_and_lifecycle_rechecked_when_publishing(ctx, change):
    row = draft(ctx)
    with ctx[1]() as db:
        if change == 'role': db.query(UserRole).filter_by(user_id=2).delete()
        if change == 'permission': db.query(RolePermission).filter_by(role_id=2, permission_id=11).delete()
        if change == 'assignment': db.get(ClassSubject, 1).instructor_id = 3
        if change == 'cancelled_session': db.get(ClassSession, 1).status = 'cancelled'
        if change == 'completed_class': db.get(TrainingClass, 1).status = 'completed'
        db.commit()
    assert publish(ctx, row).status_code in [403, 404, 409]
    with ctx[1]() as db:
        assert db.query(AssignmentRecipient).count() == 0
        assert db.get(Assignment, row['assignment_id']).status == 'draft'


def test_effective_membership_at_publish_excludes_deferred_and_future_students(ctx):
    row = draft(ctx)
    with ctx[1]() as db:
        db.add(StudentEnrollment(student_id=4, class_id=1, started_on=date(2026,10,1), ended_on=date(2026,10,16), end_status='deferred'))
        db.add(StudentEnrollment(student_id=5, class_id=1, started_on=date(2026,10,17)))
        db.add(StudentEnrollment(student_id=8, class_id=1, started_on=date(2026,10,16), origin='transfer'))
        db.add(UserRole(user_id=6, role_id=2))
        db.commit()
    result = publish(ctx, row)
    assert result.status_code == 200 and result.json()['recipient_count'] == 3
    with ctx[1]() as db:
        assert {r.student_id for r in db.query(AssignmentRecipient)} == {6, 7, 8}
        db.query(StudentClass).filter_by(student_id=7).delete()
        db.commit()
    assert ctx[0](path=f"/{row['assignment_id']}", actor=7).status_code == 200
    assert ctx[0](path=f"/{row['assignment_id']}", actor=4).status_code == 404


def test_empty_class_does_not_publish(ctx):
    row = draft(ctx)
    with ctx[1]() as db:
        db.query(StudentClass).filter_by(class_id=1).delete(); db.commit()
    assert publish(ctx, row).status_code == 409
    with ctx[1]() as db:
        assert db.get(Assignment, row['assignment_id']).version == 1


def test_file_upload_download_ownership_and_delete(ctx):
    row = draft(ctx); id = row['assignment_id']
    response = ctx[0]('POST', f'/{id}/attachments?expected_version=1', files={'file': ('bài tập.txt', b'example', 'text/plain')})
    assert response.status_code == 201, response.text
    row = response.json(); file = row['attachments'][0]; url = f"/{id}/attachments/{file['attachment_id']}"
    download = ctx[0](path=url)
    assert download.content == b'example' and download.headers['content-type'] == 'application/octet-stream'
    assert 'bài tập.txt' in unquote(download.headers['content-disposition'])
    assert download.headers['x-content-type-options'] == 'nosniff'
    assert ctx[0](path=url, actor=4).status_code == 404
    assert ctx[0](path=url, actor=3).status_code == 404
    assert ctx[0]('DELETE', url + '?expected_version=1').status_code == 409
    assert publish(ctx, row).status_code == 200
    assert ctx[0](path=url, actor=4).content == b'example'
    assert ctx[0](path=url, actor=8).status_code == 404
    assert ctx[0]('DELETE', url + '?expected_version=3').status_code == 409
    other = draft(ctx)
    assert ctx[0](path=f"/{other['assignment_id']}/attachments/{file['attachment_id']}").status_code == 404


def test_attachment_limits_rollback_and_removal(ctx, monkeypatch):
    monkeypatch.setattr(crud, 'MAX_FILE_BYTES', 8)
    monkeypatch.setattr(crud, 'MAX_TOTAL_BYTES', 10)
    monkeypatch.setattr(crud, 'MAX_FILES', 2)
    row = draft(ctx); id = row['assignment_id']
    def upload(content, version):
        return ctx[0]('POST', f'/{id}/attachments?expected_version={version}', files={'file': ('test.txt', content)})
    assert upload(b'', 1).status_code == 422
    assert upload(b'123456789', 1).status_code == 422
    first = upload(b'123456', 1).json()
    assert upload(b'123456', 2).status_code == 422
    second = upload(b'1234', 2)
    assert second.status_code == 201
    assert upload(b'x', 3).status_code == 422
    file_id = first['attachments'][0]['attachment_id']
    result = ctx[0]('DELETE', f'/{id}/attachments/{file_id}?expected_version=3')
    assert result.status_code == 200 and len(result.json()['attachments']) == 1


def test_publication_rolls_back_if_notification_storage_fails(ctx):
    row = draft(ctx)
    def reject(session, flush_context, instances):
        if any(isinstance(item, AssignmentRecipient) for item in session.new):
            raise RuntimeError('storage unavailable')
    event.listen(Session, 'before_flush', reject)
    try:
        with pytest.raises(RuntimeError, match='storage unavailable'):
            publish(ctx, row)
    finally:
        event.remove(Session, 'before_flush', reject)
    with ctx[1]() as db:
        assert db.get(Assignment, row['assignment_id']).status == 'draft'
        assert db.get(Assignment, row['assignment_id']).version == 1
        assert db.query(AssignmentRecipient).count() == 0
    assert publish(ctx, row).json()['recipient_count'] == 4


def test_concurrent_publication_sends_one_delivery_per_student(ctx, tmp_path):
    import sqlite3
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from fastapi import HTTPException
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models.user import User
    row = draft(ctx)
    path = tmp_path / 'publish.sqlite'
    raw = ctx[3].raw_connection()
    destination = sqlite3.connect(path)
    try:
        raw.driver_connection.backup(destination)
    finally:
        destination.close(); raw.close()
    engine = create_engine('sqlite:///' + path.as_posix(), connect_args={'check_same_thread': False, 'timeout': 15})
    sessions = sessionmaker(bind=engine)
    barrier = Barrier(2)
    def worker():
        with sessions() as db:
            user = db.get(User, 2)
            barrier.wait(timeout=10)
            try:
                crud.publish(db, user, row['assignment_id'], 1)
                return 200
            except HTTPException as failure:
                db.rollback()
                return failure.status_code
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: worker(), range(2)))
        assert 200 in results and set(results) <= {200, 409}
        with sessions() as db:
            assert db.query(AssignmentRecipient).count() == 4
            item = db.get(Assignment, row['assignment_id'])
            assert item.status == 'published' and item.version == 2
    finally:
        engine.dispose()
