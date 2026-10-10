
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, parse_qs

from app.core.security import (
    hash_password,
    hash_password_reset_token,
    hash_refresh_token,
    verify_password,
)
from app.models.auth_session import AuthSession
from app.models.password_reset_token import PasswordResetToken
from app.models.user import User


def create_reset_test_user(db, **overrides):
    data = {
        "username": "reset_student",
        "email": "reset_student@example.com",
        "full_name": "Reset Test Student",
        "hashed_password": hash_password("OldPassword123!"),
        "is_active": True,
        "is_locked": False,
    }
    data.update(overrides)

    user = User(**data)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_forgot_password_unknown_email_returns_generic_response(client):
    response = client.post(
        "/api/auth/forgot-password",
        json={"email": "unknown@example.com"},
    )

    assert response.status_code == 200
    assert "Nếu email tồn tại" in response.json()["message"]


def test_forgot_password_creates_token_and_sends_email(
    client, test_db, monkeypatch
):
    user = create_reset_test_user(test_db)
    sent_emails = []

    async def fake_send_email(email, reset_link):
        sent_emails.append((email, reset_link))

    monkeypatch.setattr(
        "app.api.routes.auth.send_password_reset_email",
        fake_send_email,
    )

    response = client.post(
        "/api/auth/forgot-password",
        json={"email": user.email},
    )

    assert response.status_code == 200
    assert "Nếu email tồn tại" in response.json()["message"]
    assert len(sent_emails) == 1
    assert sent_emails[0][0] == user.email

    reset_link = sent_emails[0][1]
    token = parse_qs(urlparse(reset_link).query)["token"][0]

    saved_token = test_db.query(PasswordResetToken).filter_by(
        token_hash=hash_password_reset_token(token)
    ).first()

    assert saved_token is not None
    assert saved_token.user_id == user.id
    assert saved_token.used_at is None
    assert saved_token.expires_at > datetime.now(timezone.utc).replace(
        tzinfo=None
    ) if saved_token.expires_at.tzinfo is None else saved_token.expires_at > datetime.now(timezone.utc)


def test_reset_password_success_changes_password_and_revokes_sessions(
    client, test_db
):
    user = create_reset_test_user(test_db)
    raw_token = "valid-reset-token-for-test"

    reset_token = PasswordResetToken(
        user_id=user.id,
        token_hash=hash_password_reset_token(raw_token),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )
    old_refresh_token = "old-refresh-token-for-test"
    session = AuthSession(
        user_id=user.id,
        refresh_token_hash=hash_refresh_token(old_refresh_token),
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    test_db.add_all([reset_token, session])
    test_db.commit()

    response = client.post(
        "/api/auth/reset-password",
        json={
            "token": raw_token,
            "new_password": "NewPassword456!",
        },
    )

    assert response.status_code == 200
    assert verify_password(
        "NewPassword456!", user.hashed_password
    )
    assert not verify_password(
        "OldPassword123!", user.hashed_password
    )

    test_db.refresh(reset_token)
    test_db.refresh(session)
    assert reset_token.used_at is not None
    assert session.revoked_at is not None


def test_reset_password_rejects_invalid_token(client):
    response = client.post(
        "/api/auth/reset-password",
        json={
            "token": "invalid-token",
            "new_password": "NewPassword456!",
        },
    )

    assert response.status_code == 400


def test_reset_password_rejects_expired_token(client, test_db):
    user = create_reset_test_user(test_db)
    expired_token = "expired-reset-token"

    test_db.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=hash_password_reset_token(expired_token),
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
    )
    test_db.commit()

    response = client.post(
        "/api/auth/reset-password",
        json={
            "token": expired_token,
            "new_password": "NewPassword456!",
        },
    )

    assert response.status_code == 400


def test_reset_password_rejects_reused_token(client, test_db):
    user = create_reset_test_user(test_db)
    used_token = "already-used-reset-token"

    test_db.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=hash_password_reset_token(used_token),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
            used_at=datetime.now(timezone.utc),
        )
    )
    test_db.commit()

    response = client.post(
        "/api/auth/reset-password",
        json={
            "token": used_token,
            "new_password": "NewPassword456!",
        },
    )

    assert response.status_code == 400

def test_forgot_password_email_failure_invalidates_token(
    client, test_db, monkeypatch
):
    user = create_reset_test_user(test_db)

    async def fake_send_email(email, reset_link):
        raise RuntimeError("Simulated SMTP failure")

    monkeypatch.setattr(
        "app.api.routes.auth.send_password_reset_email",
        fake_send_email,
    )

    response = client.post(
        "/api/auth/forgot-password",
        json={"email": user.email},
    )

    assert response.status_code == 200
    assert "Nếu email tồn tại" in response.json()["message"]

    tokens = test_db.query(PasswordResetToken).filter_by(
        user_id=user.id
    ).all()

    assert len(tokens) == 1
    assert tokens[0].used_at is not None
