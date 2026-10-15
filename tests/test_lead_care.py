from datetime import timedelta
import pytest
from sqlalchemy import create_engine, inspect, text
from app.database import SessionLocal
from app.models.consultation import ConsultationLead
from app.models.lead_care import LeadCareLog
from app.models.user import User
from app.models.user_role import UserRole
from app.core.security import create_access_token
from app.migrations import ensure_lead_care_schema
from test_leads import ctx, create, payload, req  # noqa: F401


def body(**changes):
    return {"interaction_type": "call", "content": "  Hẹn khách gọi lại tuần sau.  ",
            "occurred_at": "2026-10-07T09:30:00+07:00", **changes}


def url(lead):
    return f"/api/leads/{lead['lead_id']}"


@pytest.mark.parametrize("kind", ["call", "message", "meeting"])
def test_log_records_actor_time_and_optional_callback_atomically(ctx, kind):
    lead = create(ctx)
    result = req(ctx, "POST", url(lead) + "/care-logs", json=body(
        interaction_type=kind, callback_action="schedule", next_callback_at="2026-10-14T10:00:00+07:00"))
    assert result.status_code == 201, result.text
    log = result.json()
    assert log['interaction_type'] == kind
    assert log['content'] == "Hẹn khách gọi lại tuần sau."
    assert log['actor_id'] == ctx['users'][0]
    assert log['actor_name'] == ctx['prefix'] + 'admissions'
    assert log['occurred_at'] == "2026-10-07T02:30:00Z"
    assert log['created_at'] == "2026-10-07T03:00:00Z"
    assert log['next_callback_at'] == "2026-10-14T03:00:00Z"
    history = req(ctx, "GET", url(lead) + "/care-logs").json()
    assert history['items'] == [log] and history['total'] == 1
    fresh = req(ctx, "GET", url(lead)).json()
    assert fresh['next_callback_at'] == log['next_callback_at'] and not fresh['callback_overdue']
    assert fresh['status'] == lead['status']


@pytest.mark.parametrize("changes,code", [
    ({"interaction_type": "email"}, 422), ({"content": "  "}, 422), ({"content": "x" * 5001}, 422),
    ({"occurred_at": "2026-10-08T10:00:00+07:00"}, 400), ({"occurred_at": "2026-10-07T09:00:00"}, 422),
    ({"occurred_at": "invalid"}, 422), ({"actor_id": 1}, 422), ({"lead_id": 99}, 422),
    ({"occurred_at": "0001-01-01T00:00:00+07:00"}, 400),
    ({"occurred_at": "0999-01-01T00:00:00Z"}, 400),
    ({"callback_action": "schedule"}, 422), ({"next_callback_at": "2026-10-14T10:00:00+07:00"}, 422),
    ({"callback_action": "schedule", "next_callback_at": "2026-10-07T10:00:00+07:00"}, 400),
    ({"callback_action": "schedule", "next_callback_at": "2026-10-06T10:00:00+07:00"}, 400),
    ({"callback_action": "schedule", "next_callback_at": "2026-10-14T10:00:00"}, 422),
])
def test_invalid_log_does_not_create_history_or_change_reminder(ctx, changes, code):
    lead = create(ctx)
    req(ctx, "PUT", url(lead) + "/callback", json={"next_callback_at": "2026-10-14T03:00:00Z"})
    response = req(ctx, "POST", url(lead) + "/care-logs", json=body(**changes))
    assert response.status_code == code, response.text
    history = req(ctx, "GET", url(lead) + "/care-logs").json()
    assert history['total'] == 0 and history['next_callback_at'] == "2026-10-14T03:00:00Z"


def test_overdue_boundary_filter_reschedule_keep_complete_and_clear(ctx):
    lead, other = create(ctx), create(ctx)
    callback = url(lead) + "/callback"
    next_time = ctx['clock'][0] + timedelta(days=7)
    iso = next_time.isoformat() + 'Z'
    assert req(ctx, 'PUT', callback, json={'next_callback_at': iso}).status_code == 200
    ctx['clock'][0] = next_time
    assert not req(ctx, 'GET', url(lead)).json()['callback_overdue']
    ctx['clock'][0] += timedelta(microseconds=1)
    assert req(ctx, 'GET', url(lead)).json()['callback_overdue']
    filters = {'search': ctx['prefix'], 'callback_status': 'overdue'}
    assert [item['lead_id'] for item in req(ctx, 'GET', params=filters).json()['items']] == [lead['lead_id']]
    assert req(ctx, 'GET', params={**filters, 'callback_status': 'upcoming'}).json()['total'] == 0
    assert [item['lead_id'] for item in req(ctx, 'GET', params={**filters, 'callback_status': 'none'}).json()['items']] == [other['lead_id']]
    assert req(ctx, 'POST', url(lead) + '/care-logs', json=body()).status_code == 201
    assert req(ctx, 'GET', url(lead)).json()['next_callback_at'] == iso
    assert req(ctx, 'PUT', callback, json={'next_callback_at': '2026-10-21T03:00:00Z'}).status_code == 200
    assert req(ctx, 'GET', params=filters).json()['total'] == 0
    assert req(ctx, 'POST', url(lead) + '/care-logs', json=body(callback_action='complete')).status_code == 201
    assert req(ctx, 'GET', url(lead)).json()['next_callback_at'] is None
    assert req(ctx, 'PUT', callback, json={'next_callback_at': '2026-10-21T03:00:00Z'}).status_code == 200
    assert req(ctx, 'PUT', callback, json={'next_callback_at': None}).json()['next_callback_at'] is None
    assert req(ctx, 'GET', url(lead) + '/care-logs').json()['total'] == 2


@pytest.mark.parametrize('data,code', [({}, 422), ({'next_callback_at': '2026-10-01T03:00:00Z'}, 400),
    ({'next_callback_at': '2026-10-14T03:00:00'}, 422), ({'next_callback_at': None, 'assignee_id': 1}, 422)])
def test_invalid_callback(ctx, data, code):
    lead = create(ctx)
    assert req(ctx, 'PUT', url(lead) + '/callback', json=data).status_code == code
    assert req(ctx, 'GET', url(lead)).json()['next_callback_at'] is None


def test_permissions_and_reassignment_preserve_history_and_reminder(ctx):
    lead = create(ctx)
    req(ctx, 'POST', url(lead) + '/care-logs', json=body(callback_action='schedule', next_callback_at='2026-10-14T03:00:00Z'))
    with SessionLocal() as db:
        advisor = User(username=ctx['prefix']+'second', full_name='Tư vấn mới', email=ctx['prefix']+'second@example.com', password='unused', role_id=6)
        db.add(advisor); db.flush(); advisor_id = advisor.user_id
        db.add(UserRole(user_id=advisor_id, role_id=6)); db.commit()
    ctx['users'].append(advisor_id)
    ctx['headers']['second'] = {'Authorization': 'Bearer ' + create_access_token(advisor_id, 'ADMIN')}
    for role, status in [('second', 404), ('student', 403), ('instructor', 403)]:
        assert req(ctx, 'GET', url(lead) + '/care-logs', role=role).status_code == status
        assert req(ctx, 'POST', url(lead) + '/care-logs', role=role, json=body()).status_code == status
        assert req(ctx, 'PUT', url(lead) + '/callback', role=role, json={'next_callback_at': None}).status_code == status
    assert req(ctx, 'POST', '/api/leads/assign', role='manager', json={'lead_ids': [lead['lead_id']], 'assignee_id': advisor_id}).status_code == 200
    assert req(ctx, 'GET', url(lead) + '/care-logs').status_code == 404
    assert req(ctx, 'POST', url(lead) + '/care-logs', json=body()).status_code == 404
    assert req(ctx, 'PUT', url(lead) + '/callback', json={'next_callback_at': None}).status_code == 404
    for role in ['second', 'manager', 'admin']:
        data = req(ctx, 'GET', url(lead) + '/care-logs', role=role).json()
        assert data['total'] == 1 and data['next_callback_at'] == '2026-10-14T03:00:00Z'
        assert data['items'][0]['actor_id'] == ctx['users'][0]
    ctx['clock'][0] += timedelta(days=8)
    assert req(ctx, 'GET', params={'callback_status': 'overdue', 'search': ctx['prefix']}).json()['total'] == 0
    assert req(ctx, 'GET', role='second', params={'callback_status': 'overdue', 'search': ctx['prefix']}).json()['total'] == 1
    assert req(ctx, 'DELETE', url(lead), role='manager').status_code == 200
    assert req(ctx, 'GET', url(lead) + '/care-logs', role='manager').status_code == 404
    assert req(ctx, 'POST', url(lead) + '/care-logs', role='manager', json=body()).status_code == 404
    assert req(ctx, 'PUT', url(lead) + '/callback', role='manager', json={'next_callback_at': None}).status_code == 404


def test_history_order_pagination_and_backdated_logs(ctx):
    lead = create(ctx)
    path = url(lead) + '/care-logs'
    first = req(ctx, 'POST', path, json=body()).json()['log_id']
    second = req(ctx, 'POST', path, json=body()).json()['log_id']
    oldest = req(ctx, 'POST', path, json=body(occurred_at='2026-10-01T01:00:00Z')).json()['log_id']
    page = req(ctx, 'GET', path, params={'page_size': 2}).json()
    assert [item['log_id'] for item in page['items']] == [second, first] and page['total'] == 3
    assert req(ctx, 'GET', path, params={'page_size': 2, 'page': 2}).json()['items'][0]['log_id'] == oldest
    assert req(ctx, 'GET', path, params={'page_size': 101}).status_code == 422
    assert req(ctx, 'GET', path, params={'page': 0}).status_code == 422
    assert req(ctx, 'GET', params={'callback_status': 'invalid'}).status_code == 422
    assert ctx['client'].get(path).status_code == 401


def test_migration_is_idempotent_and_preserves_old_leads():
    engine = create_engine('sqlite://')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE consultation_leads (lead_id INTEGER PRIMARY KEY, full_name TEXT)'))
        conn.execute(text("INSERT INTO consultation_leads VALUES (1, 'Khách cũ')"))
    ensure_lead_care_schema(engine)
    ensure_lead_care_schema(engine)
    with engine.connect() as conn:
        assert conn.execute(text('SELECT full_name, next_callback_at FROM consultation_leads')).one() == ('Khách cũ', None)
    assert 'lead_care_logs' in inspect(engine).get_table_names()
    assert sum(index['column_names'] == ['next_callback_at'] for index in inspect(engine).get_indexes('consultation_leads')) == 1
    engine.dispose()
