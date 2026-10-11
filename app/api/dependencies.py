import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jwt import PyJWTError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def get_current_user(
    request: Request,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Phiên đăng nhập không hợp lệ hoặc đã hết hạn.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except PyJWTError as error:
        raise credentials_error from error

    if payload.get("type") != "access" or not payload.get("sub"):
        raise credentials_error

    try:
        user_id = int(payload["sub"])
    except (TypeError, ValueError) as error:
        raise credentials_error from error

    user = db.get(User, user_id)
    if user is None or not user.is_active or user.is_locked:
        raise credentials_error
    allowed_during_password_change = (
        request.url.path == "/api/auth/change-password"
        or (
            request.url.path == "/api/me"
            and request.method == "GET"
        )
    )
    if user.must_change_password and not allowed_during_password_change:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "password_change_required",
                "message": "Bạn cần đổi mật khẩu trước khi tiếp tục.",
            },
        )
    return user


def require_roles(*allowed_roles: str):
    def role_guard(
        current_user: User = Depends(get_current_user),
    ) -> User:
        user_roles = {role.code for role in current_user.roles}
        if not user_roles.intersection(allowed_roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Bạn không có quyền thực hiện thao tác này.",
            )
        return current_user

    return role_guard
