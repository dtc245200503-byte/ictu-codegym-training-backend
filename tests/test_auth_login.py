import jwt

from app.core.config import settings
from app.core.security import hash_password
from app.models.user import User


def create_test_user(db, **overrides):
    data = {
        "username": "student01",
        "email": "student@example.com",
        "full_name": "Test Student",
        "hashed_password": hash_password("CorrectPassword123!"),
        "is_active": True,
        "is_locked": False,
    }
    data.update(overrides)

    user = User(**data)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_login_endpoint_is_registered(client):
    response = client.post(
        "/api/auth/login",
        json={
            "email": "student@example.com",
            "password": "CorrectPassword123!",
        },
    )

    assert response.status_code != 404


def test_login_rejects_missing_credentials(client):
    response = client.post("/api/auth/login", json={})

    assert response.status_code == 422


def test_login_success_returns_access_token(client, test_db):
    user = create_test_user(test_db)

    response = client.post(
        "/api/auth/login",
        json={
            "email": user.email,
            "password": "CorrectPassword123!",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["email"] == user.email
    assert body["user"]["id"] == user.id

    payload = jwt.decode(
        body["access_token"],
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
    )
    assert payload["sub"] == str(user.id)
    assert payload["type"] == "access"


def test_login_rejects_wrong_password(client, test_db):
    create_test_user(test_db)

    response = client.post(
        "/api/auth/login",
        json={
            "email": "student@example.com",
            "password": "WrongPassword123!",
        },
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Email hoặc mật khẩu không đúng"


def test_login_rejects_unknown_email(client):
    response = client.post(
        "/api/auth/login",
        json={
            "email": "unknown@example.com",
            "password": "CorrectPassword123!",
        },
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Email hoặc mật khẩu không đúng"


def test_login_rejects_inactive_user(client, test_db):
    create_test_user(test_db, is_active=False)

    response = client.post(
        "/api/auth/login",
        json={
            "email": "student@example.com",
            "password": "CorrectPassword123!",
        },
    )

    assert response.status_code == 403


def test_login_rejects_locked_user(client, test_db):
    create_test_user(test_db, is_locked=True)

    response = client.post(
        "/api/auth/login",
        json={
            "email": "student@example.com",
            "password": "CorrectPassword123!",
        },
    )

    assert response.status_code == 403
from datetime import datetime, timedelta, timezone


def test_five_failed_attempts_temporarily_lock_account(client, test_db):
    user = create_test_user(test_db)

    for attempt in range(5):
        response = client.post(
            "/api/auth/login",
            json={
                "email": user.email,
                "password": "WrongPassword123!",
            },
        )

        if attempt < 4:
            assert response.status_code == 401
        else:
            assert response.status_code == 423

    test_db.refresh(user)
    assert user.failed_login_attempts == 5
    assert user.locked_until is not None

    # Even the correct password must be rejected during the lock.
    response = client.post(
        "/api/auth/login",
        json={
            "email": user.email,
            "password": "CorrectPassword123!",
        },
    )
    assert response.status_code == 423


def test_expired_temporary_lock_allows_login(client, test_db):
    user = create_test_user(
        test_db,
        failed_login_attempts=5,
        locked_until=datetime.now(timezone.utc) - timedelta(minutes=1),
    )

    response = client.post(
        "/api/auth/login",
        json={
            "email": user.email,
            "password": "CorrectPassword123!",
        },
    )

    assert response.status_code == 200
    test_db.refresh(user)
    assert user.failed_login_attempts == 0
    assert user.locked_until is None


def test_successful_login_resets_failed_attempts(client, test_db):
    user = create_test_user(
        test_db,
        failed_login_attempts=3,
    )

    response = client.post(
        "/api/auth/login",
        json={
            "email": user.email,
            "password": "CorrectPassword123!",
        },
    )

    assert response.status_code == 200
    test_db.refresh(user)
    assert user.failed_login_attempts == 0
    assert user.locked_until is None
