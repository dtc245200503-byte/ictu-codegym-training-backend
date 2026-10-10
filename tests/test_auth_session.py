from datetime import datetime, timedelta, timezone

from app.core.security import hash_password, hash_refresh_token
from app.models.auth_session import AuthSession
from app.models.user import User


def create_session_test_user(db):
    user = User(
        username="session_student",
        email="session_student@example.com",
        full_name="Session Test Student",
        hashed_password=hash_password("CorrectPassword123!"),
        is_active=True,
        is_locked=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def login_and_get_refresh_token(client, user):
    response = client.post(
        "/api/auth/login",
        json={
            "email": user.email,
            "password": "CorrectPassword123!",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["refresh_token"]
    return body["refresh_token"]


def test_login_creates_refresh_session(client, test_db):
    user = create_session_test_user(test_db)
    refresh_token = login_and_get_refresh_token(client, user)

    session = test_db.query(AuthSession).filter_by(
        refresh_token_hash=hash_refresh_token(refresh_token)
    ).first()

    assert session is not None
    assert session.user_id == user.id
    assert session.revoked_at is None


def test_refresh_rotates_token_and_rejects_old_token(client, test_db):
    user = create_session_test_user(test_db)
    old_token = login_and_get_refresh_token(client, user)

    response = client.post(
        "/api/auth/refresh",
        json={"refresh_token": old_token},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["refresh_token"] != old_token

    old_session = test_db.query(AuthSession).filter_by(
        refresh_token_hash=hash_refresh_token(old_token)
    ).first()
    assert old_session is not None
    assert old_session.revoked_at is not None

    reused_response = client.post(
        "/api/auth/refresh",
        json={"refresh_token": old_token},
    )
    assert reused_response.status_code == 401


def test_logout_revokes_refresh_token(client, test_db):
    user = create_session_test_user(test_db)
    refresh_token = login_and_get_refresh_token(client, user)

    response = client.post(
        "/api/auth/logout",
        json={"refresh_token": refresh_token},
    )

    assert response.status_code == 204

    session = test_db.query(AuthSession).filter_by(
        refresh_token_hash=hash_refresh_token(refresh_token)
    ).first()
    assert session is not None
    assert session.revoked_at is not None

    refresh_response = client.post(
        "/api/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert refresh_response.status_code == 401


def test_refresh_rejects_unknown_token(client):
    response = client.post(
        "/api/auth/refresh",
        json={"refresh_token": "this-token-does-not-exist"},
    )
    assert response.status_code == 401


def test_refresh_rejects_expired_token(client, test_db):
    user = create_session_test_user(test_db)
    expired_token = "expired-test-refresh-token"

    session = AuthSession(
        user_id=user.id,
        refresh_token_hash=hash_refresh_token(expired_token),
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    test_db.add(session)
    test_db.commit()

    response = client.post(
        "/api/auth/refresh",
        json={"refresh_token": expired_token},
    )

    assert response.status_code == 401
    test_db.refresh(session)
    assert session.revoked_at is not None