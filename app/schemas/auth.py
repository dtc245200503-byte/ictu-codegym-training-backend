from pydantic import AliasChoices, BaseModel, EmailStr, Field, field_validator


class LoginRequest(BaseModel):
    identifier: str = Field(
        min_length=1,
        max_length=255,
        validation_alias=AliasChoices("identifier", "email", "username"),
    )
    password: str = Field(min_length=1, max_length=128)


class LoginUser(BaseModel):
    id: int
    username: str
    email: EmailStr
    full_name: str
    roles: list[str]
    must_change_password: bool


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: LoginUser


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class RefreshResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class LogoutRequest(BaseModel):
    refresh_token: str = Field(min_length=1)

class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ForgotPasswordResponse(BaseModel):
    message: str


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def validate_password_strength(cls, value: str) -> str:
        if not any(char.isalpha() for char in value):
            raise ValueError("Mật khẩu phải có ít nhất một chữ cái.")
        if not any(char.isdigit() for char in value):
            raise ValueError("Mật khẩu phải có ít nhất một chữ số.")
        if not any(not char.isalnum() for char in value):
            raise ValueError("Mật khẩu phải có ít nhất một ký tự đặc biệt.")
        return value
