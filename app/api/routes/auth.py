from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import create_access_token, verify_password
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import LoginRequest, LoginResponse, LoginUser


router = APIRouter(prefix="/auth", tags=["Authentication"])

MAX_FAILED_ATTEMPTS = 5
LOCK_DURATION_MINUTES = 15


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_datetime(value: datetime) -> datetime:
    """MySQL may return DATETIME without timezone information."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


@router.post("/login", response_model=LoginResponse)
def login(
    credentials: LoginRequest,
    db: Session = Depends(get_db),
):
    user = db.scalar(
        select(User)
        .where(User.email == str(credentials.email))
        .with_for_update()
    )

    invalid_credentials = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Email hoặc mật khẩu không đúng",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if user is None:
        raise invalid_credentials

    now = utc_now()

    if user.locked_until is not None:
        locked_until = normalize_datetime(user.locked_until)

        if now < locked_until:
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail="Tài khoản tạm thời bị khóa. Vui lòng thử lại sau 15 phút.",
            )

        # The temporary lock has expired.
        user.locked_until = None
        user.failed_login_attempts = 0
        db.commit()
        db.refresh(user)

    if not verify_password(credentials.password, user.hashed_password):
        user.failed_login_attempts += 1

        if user.failed_login_attempts >= MAX_FAILED_ATTEMPTS:
            user.failed_login_attempts = MAX_FAILED_ATTEMPTS
            user.locked_until = now + timedelta(
                minutes=LOCK_DURATION_MINUTES
            )

        db.commit()

        if user.locked_until is not None:
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail="Tài khoản tạm thời bị khóa. Vui lòng thử lại sau 15 phút.",
            )

        raise invalid_credentials

    if not user.is_active or user.is_locked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tài khoản đã bị khóa hoặc vô hiệu hóa",
        )

    # A successful login resets consecutive failures.
    user.failed_login_attempts = 0
    user.locked_until = None
    db.commit()

    access_token = create_access_token(subject=str(user.id))

    return LoginResponse(
        access_token=access_token,
        token_type="bearer",
        user=LoginUser(
            id=user.id,
            username=user.username,
            email=user.email,
            full_name=user.full_name,
            roles=[role.code for role in user.roles],
        ),
    )
