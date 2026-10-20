from typing import Literal
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, TypeAdapter, model_validator


class RoomInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    kind: Literal["physical", "online"]
    capacity: int | None = Field(default=None, ge=1, le=100000, strict=True)
    meeting_url: str | None = Field(default=None, max_length=2000)
    is_active: bool = Field(default=True, strict=True)

    @model_validator(mode="after")
    def validate_room(self):
        self.name = " ".join(self.name.split())
        if not self.name:
            raise ValueError("Nhập tên phòng.")
        self.meeting_url = self.meeting_url or None
        if self.kind == "physical":
            if self.capacity is None or self.meeting_url is not None:
                raise ValueError("Phòng vật lý cần sức chứa, không nhập liên kết trực tuyến.")
        else:
            if not self.meeting_url or self.capacity is not None:
                raise ValueError("Phòng trực tuyến cần liên kết, không nhập sức chứa vật lý.")
            validated = TypeAdapter(HttpUrl).validate_python(self.meeting_url)
            if validated.username is not None or validated.password is not None or any(ord(c) < 32 or c.isspace() for c in self.meeting_url) or "\\" in self.meeting_url:
                raise ValueError("Liên kết HTTP/HTTPS không được chứa tài khoản đăng nhập, khoảng trắng hoặc ký tự điều khiển.")
            if not urlsplit(self.meeting_url).netloc:
                raise ValueError("Nhập liên kết đầy đủ bắt đầu bằng http:// hoặc https://.")
        return self


class RoomUpdate(RoomInput):
    expected_version: int = Field(ge=0, strict=True)
