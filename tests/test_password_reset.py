from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_password_reset_token, hash_password, verify_password
from app.database import SessionLocal
from app.main import app
from app.models.password_reset import PasswordResetToken
from app.models.session import UserSession
from app.models.user import User


@pytest.fixture
def reset_context():
    prefix = 'reset_' + uuid.uuid4().hex[:12]
    user_ids = []
    with SessionLocal() as db:
        for suffix in ('a', 'b'):
            user = User(username=prefix + suffix, email=prefix + suffix + '@example.com',
                        full_name='Người dùng kiểm thử', password=hash_password('OldPassword123'), role_id=3)
            db.add(user); db.flush(); user_ids.append(user.user_id)
        db.commit()
    with TestClient(app) as client:
        yield {'client': client, 'ids': user_ids, 'email': prefix + 'a@example.com'}
    with SessionLocal() as db:
        db.query(PasswordResetToken).filter(PasswordResetToken.user_id.in_(user_ids)).delete()
        db.query(UserSession).filter(UserSession.user_id.in_(user_ids)).delete()
        db.query(User).filter(User.user_id.in_(user_ids)).delete()
        db.commit()


def token_for(context, index=0, used=False, expired=False):
    token = create_password_reset_token()
    with SessionLocal() as db:
        db.add(PasswordResetToken(user_id=context['ids'][index], token=token, used=used,
                                 expires_at=datetime.now() + timedelta(minutes=-1 if expired else 30)))
        db.commit()
    return token


def reset(context, token, password='NewPassword456'):
    return context['client'].post('/api/auth/reset-password', json={'token': token, 'new_password': password})


def test_forgot_email_link_resets_password_and_allows_login(reset_context, monkeypatch):
    sent = []
    async def capture_email(**kwargs):
        sent.append(kwargs)
    monkeypatch.setattr('app.routers.auth.send_reset_password_email', capture_email)
    client = reset_context['client']
    response = client.post('/api/auth/forgot-password', json={'email': ' ' + reset_context['email'].upper() + ' '})
    assert response.status_code == 200 and len(sent) == 1
    assert sent[0]['email'] == reset_context['email']
    token = parse_qs(urlparse(sent[0]['reset_link']).query)['token'][0]
    assert reset(reset_context, token).status_code == 200
    with SessionLocal() as db:
        user = db.get(User, reset_context['ids'][0])
        assert user.password != 'NewPassword456'
        assert verify_password('NewPassword456', user.password)
        assert not verify_password('OldPassword123', user.password)
    assert client.post('/api/auth/login', json={'email': reset_context['email'], 'password': 'NewPassword456'}).status_code == 200
    assert client.post('/api/auth/login', json={'email': reset_context['email'], 'password': 'OldPassword123'}).status_code == 401
    assert reset(reset_context, token, 'AnotherPassword789').status_code == 400


@pytest.mark.parametrize('kind', ['unknown', 'used', 'expired', 'wrong_case', 'empty', 'unicode'])
def test_invalid_links_do_not_change_password_or_sessions(reset_context, kind):
    token = token_for(reset_context, used=kind == 'used', expired=kind == 'expired')
    if kind == 'unknown': token = create_password_reset_token()
    if kind == 'empty': token = ''
    if kind == 'unicode': token = 'Liên_kết_không_hợp_lệ'
    if kind == 'wrong_case': token = token.swapcase()
    with SessionLocal() as db:
        original = db.get(User, reset_context['ids'][0]).password
        db.add(UserSession(user_id=reset_context['ids'][0], refresh_token='invalid_' + uuid.uuid4().hex,
                           expires_at=datetime.now() + timedelta(days=1), revoked=False)); db.commit()
    response = reset(reset_context, token)
    assert response.status_code == 400 and 'liên kết mới' in response.json()['detail']
    with SessionLocal() as db:
        assert db.get(User, reset_context['ids'][0]).password == original
        assert not db.query(UserSession).filter_by(user_id=reset_context['ids'][0]).one().revoked


@pytest.mark.parametrize('password', ['', 'Ab12', 'abcdefgh', '12345678', '        '])
def test_backend_rejects_weak_password_without_consuming_link(reset_context, password):
    token = token_for(reset_context)
    assert reset(reset_context, token, password).status_code == 400
    with SessionLocal() as db:
        assert not db.query(PasswordResetToken).filter_by(token=token).one().used
        assert verify_password('OldPassword123', db.get(User, reset_context['ids'][0]).password)
    assert reset(reset_context, token).status_code == 200


def test_success_consumes_all_own_links_and_revokes_only_own_sessions(reset_context):
    first = token_for(reset_context)
    second = token_for(reset_context)
    other = token_for(reset_context, index=1)
    client = reset_context['client']
    sessions = []
    with SessionLocal() as db:
        for index in (0, 0, 1):
            session_token = 'session_' + uuid.uuid4().hex
            sessions.append(session_token)
            db.add(UserSession(user_id=reset_context['ids'][index], refresh_token=session_token,
                               expires_at=datetime.now() + timedelta(days=1), revoked=False))
        user = db.get(User, reset_context['ids'][0])
        user.failed_login_attempts = 5
        user.locked_until = datetime.now() + timedelta(minutes=15)
        db.commit()
    assert reset(reset_context, first).status_code == 200
    assert reset(reset_context, second, 'AnotherPassword789').status_code == 400
    with SessionLocal() as db:
        assert db.query(PasswordResetToken).filter_by(token=first).one().used
        assert db.query(PasswordResetToken).filter_by(token=second).one().used
        assert not db.query(PasswordResetToken).filter_by(token=other).one().used
        assert all(db.query(UserSession).filter_by(refresh_token=item).one().revoked for item in sessions[:2])
        assert not db.query(UserSession).filter_by(refresh_token=sessions[2]).one().revoked
        assert verify_password('OldPassword123', db.get(User, reset_context['ids'][1]).password)
        user = db.get(User, reset_context['ids'][0])
        assert user.failed_login_attempts == 0 and user.locked_until is None
    assert client.post('/api/auth/refresh', json={'refresh_token': sessions[0]}).status_code == 401


def test_reset_does_not_unlock_admin_locked_account(reset_context):
    token = token_for(reset_context)
    with SessionLocal() as db:
        user = db.get(User, reset_context['ids'][0]); user.is_locked = True; user.lock_reason = 'Khóa bởi quản trị viên'; db.commit()
    assert reset(reset_context, token).status_code == 200
    with SessionLocal() as db:
        user = db.get(User, reset_context['ids'][0])
        assert user.is_locked and user.lock_reason == 'Khóa bởi quản trị viên'
    assert reset_context['client'].post('/api/auth/login', json={'email': reset_context['email'], 'password': 'NewPassword456'}).status_code == 423


def test_unknown_email_does_not_send_email_or_create_token(reset_context, monkeypatch):
    async def unexpected_email(**kwargs):
        raise AssertionError('Unknown email must not be sent a reset link')
    monkeypatch.setattr('app.routers.auth.send_reset_password_email', unexpected_email)
    with SessionLocal() as db:
        before = db.query(PasswordResetToken).count()
    response = reset_context['client'].post('/api/auth/forgot-password', json={'email': 'missing_' + uuid.uuid4().hex + '@example.com'})
    assert response.status_code == 404 and 'Email không tồn tại' in response.json()['detail']
    with SessionLocal() as db:
        assert db.query(PasswordResetToken).count() == before


def test_change_password_keeps_current_session_and_rejects_wrong_confirmation(reset_context):
    client = reset_context['client']
    login = client.post('/api/auth/login', json={
        'email': reset_context['email'], 'password': 'OldPassword123'
    }).json()
    headers = {'Authorization': 'Bearer ' + login['access_token']}
    payload = {'current_password': 'OldPassword123', 'new_password': 'NewPassword456!',
               'confirm_password': 'WrongPassword789!', 'refresh_token': login['refresh_token']}
    assert client.post('/api/auth/change-password', headers=headers, json=payload).status_code == 422
    payload['confirm_password'] = payload['new_password']
    response = client.post('/api/auth/change-password', headers=headers, json=payload)
    assert response.status_code == 200
    assert client.post('/api/auth/refresh', json={'refresh_token': login['refresh_token']}).status_code == 200
    assert client.post('/api/auth/login', json={
        'email': reset_context['email'], 'password': payload['new_password']
    }).status_code == 200
