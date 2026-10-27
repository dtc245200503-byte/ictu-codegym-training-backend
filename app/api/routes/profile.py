import base64
import binascii
import warnings
from io import BytesIO

from fastapi import APIRouter, Depends, HTTPException, Response, status
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.api.routes.management import _user_payload
from app.db.session import get_db
from app.models.user import User
from app.models.user_avatar import UserAvatar
from app.schemas.management import AvatarUpload, SelfProfileUpdate

router = APIRouter(tags=["User profile"])
MAX_AVATAR_BYTES = 2 * 1024 * 1024
MAX_AVATAR_PIXELS = 40_000_000
AVATAR_SIZE = 512
AVATAR_THUMBNAIL_SIZE = 128


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
    variant: str = "original",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    if variant not in {"original", "thumbnail"}:
        raise HTTPException(status_code=422, detail="variant must be original or thumbnail.")
    avatar = db.get(UserAvatar, current_user.id)
    if avatar is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    image_data = avatar.thumbnail_data if variant == "thumbnail" else avatar.image_data
    if not image_data:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    return Response(
        content=image_data,
        media_type=avatar.content_type,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
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

    expected_format = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}[detected_type]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(image_data)) as source:
                if source.format != expected_format or getattr(source, "n_frames", 1) != 1:
                    raise HTTPException(status_code=422, detail="Avatar must be a valid single-frame image matching its content type.")
                if source.width * source.height > MAX_AVATAR_PIXELS:
                    raise HTTPException(status_code=413, detail="Avatar image dimensions are too large.")
                source.load()
                oriented = ImageOps.exif_transpose(source)
                width, height = oriented.size
                if request.crop_size is None:
                    size = min(width, height)
                    left, top = (width - size) // 2, (height - size) // 2
                else:
                    size = request.crop_size
                    left, top = request.crop_x or 0, request.crop_y or 0
                    if left + size > width or top + size > height:
                        raise HTTPException(status_code=422, detail="Avatar crop coordinates exceed image bounds.")
                cropped = oriented.crop((left, top, left + size, top + size))
                cropped = cropped.convert("RGB" if detected_type == "image/jpeg" or "A" not in cropped.getbands() else "RGBA")
                resampling = getattr(Image, "Resampling", Image).LANCZOS
                cropped = cropped.resize((AVATAR_SIZE, AVATAR_SIZE), resampling)
                thumbnail = cropped.resize((AVATAR_THUMBNAIL_SIZE, AVATAR_THUMBNAIL_SIZE), resampling)
                options = {"format": expected_format}
                if expected_format == "JPEG":
                    options.update(quality=88, optimize=True)
                elif expected_format == "PNG":
                    options.update(optimize=True)
                else:
                    options.update(quality=88, method=4)
                normalized_buffer, thumbnail_buffer = BytesIO(), BytesIO()
                cropped.save(normalized_buffer, **options)
                thumbnail.save(thumbnail_buffer, **options)
                image_data = normalized_buffer.getvalue()
                thumbnail_data = thumbnail_buffer.getvalue()
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning, ValueError) as error:
        raise HTTPException(status_code=422, detail="Image content is invalid or unsafe.") from error
    if len(image_data) > MAX_AVATAR_BYTES:
        raise HTTPException(status_code=413, detail="Processed avatar exceeds the 2 MiB storage limit.")

    avatar = db.get(UserAvatar, current_user.id)
    if avatar is None:
        avatar = UserAvatar(
            user_id=current_user.id,
            image_data=image_data,
            thumbnail_data=thumbnail_data,
            content_type=detected_type,
        )
        db.add(avatar)
    else:
        avatar.image_data = image_data
        avatar.thumbnail_data = thumbnail_data
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
