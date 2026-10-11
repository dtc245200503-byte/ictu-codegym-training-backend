
import asyncio
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
from fastapi_mail import MessageType
from app.services import email_service


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
    assert reset_link.startswith("http://localhost:5173/reset-password?token=")
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
    expiry = (
        saved_token.expires_at.replace(tzinfo=timezone.utc)
        if saved_token.expires_at.tzinfo is None
        else saved_token.expires_at
    )
    assert expiry <= datetime.now(timezone.utc) + timedelta(minutes=15)


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
    assert user.must_change_password is False
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


def test_forgot_password_logs_reset_link_when_smtp_is_unconfigured(
    client, test_db, caplog, monkeypatch
):
    user = create_reset_test_user(test_db)
    monkeypatch.setattr("app.core.config.settings.MAIL_USERNAME", "")

    response = client.post(
        "/api/auth/forgot-password",
        json={"email": user.email},
    )

    assert response.status_code == 200
    assert "Nếu email tồn tại" in response.json()["message"]
    assert "Password reset email is not configured" in caplog.text
    assert "?token=" in caplog.text


def test_smtp_unavailable_in_production_does_not_log_or_deliver_reset_link(
    client, test_db, caplog, monkeypatch
):
    user = create_reset_test_user(test_db)
    monkeypatch.setattr("app.core.config.settings.APP_ENV", "production")
    monkeypatch.setattr("app.core.config.settings.MAIL_USERNAME", "")

    response = client.post(
        "/api/auth/forgot-password",
        json={"email": user.email},
    )

    assert response.status_code == 200
    assert "?token=" not in caplog.text
    tokens = test_db.query(PasswordResetToken).filter_by(user_id=user.id).all()
    assert len(tokens) == 1
    assert tokens[0].used_at is not None


def test_password_reset_email_uses_configured_smtp_and_html_template(monkeypatch):
    sent_messages = []
    connection_configs = []

    class FakeFastMail:
        def __init__(self, config):
            connection_configs.append(config)

        async def send_message(self, message):
            sent_messages.append(message)

    monkeypatch.setattr(email_service, "FastMail", FakeFastMail)
    monkeypatch.setattr(email_service.settings, "MAIL_USERNAME", "smtp-user@example.com")
    monkeypatch.setattr(email_service.settings, "MAIL_PASSWORD", "smtp-test-password")
    monkeypatch.setattr(email_service.settings, "MAIL_FROM", "portal@example.com")
    monkeypatch.setattr(email_service.settings, "MAIL_SERVER", "smtp.example.com")
    monkeypatch.setattr(email_service.settings, "MAIL_PORT", 587)

    asyncio.run(
        email_service.send_password_reset_email(
            "student@example.com",
            "https://portal.example.com/reset-password?token=abc123",
        )
    )

    assert len(connection_configs) == 1
    assert len(sent_messages) == 1
    message = sent_messages[0]
    assert message.subject == "[ICTU Academic Portal] Khôi phục mật khẩu tài khoản"
    assert message.recipients[0].email == "student@example.com"
    assert message.subtype == MessageType.html
    assert "https://portal.example.com/reset-password?token=abc123" in message.body
    assert "15 phút" in message.body


def test_first_login_password_change_uses_auth_endpoint_and_clears_flag(
    client, test_db
):
    user = create_reset_test_user(test_db, must_change_password=True)
    login = client.post(
        "/api/auth/login",
        json={"identifier": user.username, "password": "OldPassword123!"},
    )
    assert login.status_code == 200
    assert login.json()["user"]["must_change_password"] is True

    response = client.post(
        "/api/auth/change-password",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        json={
            "current_password": "OldPassword123!",
            "new_password": "NewPassword456!",
        },
    )

    assert response.status_code == 200
    test_db.refresh(user)
    assert user.must_change_password is False
    assert verify_password("NewPassword456!", user.hashed_password)
