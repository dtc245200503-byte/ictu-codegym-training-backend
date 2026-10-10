
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    create_password_reset_token,
    hash_password_reset_token,
    hash_refresh_token,
    hash_password,
    verify_password,
)
from app.db.session import get_db
from app.models.auth_session import AuthSession
from app.models.password_reset_token import PasswordResetToken
from app.models.user import User
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    LoginUser,
    RefreshRequest,
    RefreshResponse,
    LogoutRequest,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    ResetPasswordRequest,
)
from app.services.email_service import send_password_reset_email

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

    user.failed_login_attempts = 0
    user.locked_until = None

    refresh_token = create_refresh_token()
    session = AuthSession(
        user_id=user.id,
        refresh_token_hash=hash_refresh_token(refresh_token),
        expires_at=now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    )

    db.add(session)
    db.commit()

    access_token = create_access_token(subject=str(user.id))

    return LoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        user=LoginUser(
            id=user.id,
            username=user.username,
            email=user.email,
            full_name=user.full_name,
            roles=[role.code for role in user.roles],
        ),
    )

@router.post("/refresh", response_model=RefreshResponse)
def refresh_tokens(
    request: RefreshRequest,
    db: Session = Depends(get_db),
):
    now = utc_now()
    token_hash = hash_refresh_token(request.refresh_token)

    session = db.scalar(
        select(AuthSession)
        .where(AuthSession.refresh_token_hash == token_hash)
        .with_for_update()
    )

    invalid_token = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Refresh token không hợp lệ hoặc đã hết hạn",
    )

    if session is None or session.revoked_at is not None:
        raise invalid_token

    if normalize_datetime(session.expires_at) <= now:
        session.revoked_at = now
        db.commit()
        raise invalid_token

    user = db.get(User, session.user_id)

    if user is None or not user.is_active or user.is_locked:
        session.revoked_at = now
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tài khoản đã bị khóa hoặc vô hiệu hóa",
        )

    new_refresh_token = create_refresh_token()

    # Revoke the old refresh token before issuing the replacement.
    session.revoked_at = now

    new_session = AuthSession(
        user_id=user.id,
        refresh_token_hash=hash_refresh_token(new_refresh_token),
        expires_at=now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    )

    db.add(new_session)
    db.commit()

    new_access_token = create_access_token(subject=str(user.id))

    return RefreshResponse(
        access_token=new_access_token,
        refresh_token=new_refresh_token,
        token_type="bearer",
    )
@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: LogoutRequest,
    db: Session = Depends(get_db),
):
    token_hash = hash_refresh_token(request.refresh_token)

    session = db.scalar(
        select(AuthSession).where(
            AuthSession.refresh_token_hash == token_hash
        )
    )

    if session is not None and session.revoked_at is None:
        session.revoked_at = utc_now()
        db.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)

@router.post("/forgot-password", response_model=ForgotPasswordResponse)
async def forgot_password(
    request: ForgotPasswordRequest,
    db: Session = Depends(get_db),
):
    generic_message = (
        "Nếu email tồn tại trong hệ thống, "
        "hướng dẫn đặt lại mật khẩu sẽ được gửi đến email đó."
    )

    user = db.scalar(
        select(User).where(User.email == str(request.email))
    )

    # Always return the same response for an unknown email.
    if user is None:
        return ForgotPasswordResponse(message=generic_message)

    now = utc_now()
    raw_token = create_password_reset_token()

    reset_token = PasswordResetToken(
        user_id=user.id,
        token_hash=hash_password_reset_token(raw_token),
        expires_at=now + timedelta(
            minutes=settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES
        ),
    )

    try:
        # Invalidate older unused reset tokens for this user.
        db.query(PasswordResetToken).filter(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used_at.is_(None),
        ).update(
            {PasswordResetToken.used_at: now},
            synchronize_session=False,
        )

        db.add(reset_token)
        db.commit()

        reset_link = (
            f"{settings.FRONTEND_RESET_PASSWORD_URL}"
            f"?token={raw_token}"
        )

        await send_password_reset_email(user.email, reset_link)
    except Exception:
        db.rollback()

        # Invalidate the token if email delivery fails.
        try:
            failed_token = db.scalar(
                select(PasswordResetToken).where(
                    PasswordResetToken.token_hash
                    == hash_password_reset_token(raw_token)
                )
            )

            if (
                failed_token is not None
                and failed_token.used_at is None
            ):
                failed_token.used_at = utc_now()
                db.commit()

        except Exception:
            db.rollback()

        # Keep the response identical for known and unknown emails.
        return ForgotPasswordResponse(message=generic_message)

    return ForgotPasswordResponse(message=generic_message)


@router.post("/reset-password")
def reset_password(
    request: ResetPasswordRequest,
    db: Session = Depends(get_db),
):
    now = utc_now()
    token_hash = hash_password_reset_token(request.token)

    reset_token = db.scalar(
        select(PasswordResetToken)
        .where(PasswordResetToken.token_hash == token_hash)
        .with_for_update()
    )

    invalid_token = HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Token không hợp lệ, đã hết hạn hoặc đã được sử dụng.",
    )

    if reset_token is None or reset_token.used_at is not None:
        raise invalid_token

    if normalize_datetime(reset_token.expires_at) <= now:
        raise invalid_token

    user = db.get(User, reset_token.user_id)

    if user is None:
        raise invalid_token

    try:
        user.hashed_password = hash_password(request.new_password)
        user.failed_login_attempts = 0
        user.locked_until = None

        reset_token.used_at = now

        # Revoke all refresh sessions for this user.
        db.query(AuthSession).filter(
            AuthSession.user_id == user.id,
            AuthSession.revoked_at.is_(None),
        ).update(
            {AuthSession.revoked_at: now},
            synchronize_session=False,
        )

        # Invalidate other outstanding reset tokens.
        db.query(PasswordResetToken).filter(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.id != reset_token.id,
            PasswordResetToken.used_at.is_(None),
        ).update(
            {PasswordResetToken.used_at: now},
            synchronize_session=False,
        )

        db.commit()

    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Không thể đặt lại mật khẩu lúc này.",
        )

    return {"message": "Đặt lại mật khẩu thành công."}
