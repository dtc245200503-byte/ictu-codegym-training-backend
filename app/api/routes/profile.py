import base64
import binascii

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.api.routes.management import _user_payload
from app.db.session import get_db
from app.models.user import User
from app.models.user_avatar import UserAvatar
from app.schemas.management import AvatarUpload, SelfProfileUpdate

router = APIRouter(tags=["User profile"])
MAX_AVATAR_BYTES = 2 * 1024 * 1024


def _image_content_type(image_data: bytes) -> str | None:
    if (
        len(image_data) >= 33
        and image_data.startswith(b"\x89PNG\r\n\x1a\n")
        and image_data[12:16] == b"IHDR"
    ):
        return "image/png"
    if image_data.startswith(b"\xff\xd8\xff") and image_data.endswith(b"\xff\xd9"):
        return "image/jpeg"
    if (
        len(image_data) >= 30
        and image_data.startswith(b"RIFF")
        and image_data[8:12] == b"WEBP"
        and int.from_bytes(image_data[4:8], "little") + 8 == len(image_data)
    ):
        return "image/webp"
    return None


@router.get("/me")
def read_current_user(
    current_user: User = Depends(get_current_user),
) -> dict:
    return _user_payload(current_user)


@router.patch("/me")
def update_current_user(
    request: SelfProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if current_user.must_change_password:
        raise HTTPException(
            status_code=403,
            detail="Bạn cần đổi mật khẩu trước khi cập nhật hồ sơ.",
        )
    updates = request.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="Không có dữ liệu cần cập nhật.")
    if "full_name" in updates and updates["full_name"] is None:
        raise HTTPException(status_code=422, detail="Họ tên không được để trống.")
    for field, value in updates.items():
        setattr(current_user, field, value)
    db.commit()
    db.refresh(current_user)
    return _user_payload(current_user)


@router.get("/me/avatar")
def read_current_avatar(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    avatar = db.get(UserAvatar, current_user.id)
    if avatar is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return Response(
        content=avatar.image_data,
        media_type=avatar.content_type,
        headers={"Cache-Control": "private, no-store"},
    )


@router.put("/me/avatar")
def update_current_avatar(
    request: AvatarUpload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    try:
        image_data = base64.b64decode(request.data_base64, validate=True)
    except (binascii.Error, ValueError) as error:
        raise HTTPException(status_code=422, detail="Dữ liệu ảnh không hợp lệ.") from error
    if not image_data or len(image_data) > MAX_AVATAR_BYTES:
        raise HTTPException(status_code=413, detail="Ảnh đại diện tối đa 2 MB.")
    detected_type = _image_content_type(image_data)
    if detected_type != request.content_type:
        raise HTTPException(
            status_code=422,
            detail="Chỉ chấp nhận ảnh PNG, JPEG hoặc WebP hợp lệ.",
        )

    avatar = db.get(UserAvatar, current_user.id)
    if avatar is None:
        avatar = UserAvatar(
            user_id=current_user.id,
            image_data=image_data,
            content_type=detected_type,
        )
        db.add(avatar)
    else:
        avatar.image_data = image_data
        avatar.content_type = detected_type
    db.commit()
    return {"message": "Ảnh đại diện đã được cập nhật."}


@router.delete("/me/avatar", status_code=status.HTTP_204_NO_CONTENT)
def delete_current_avatar(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    avatar = db.get(UserAvatar, current_user.id)
    if avatar is not None:
        db.delete(avatar)
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
