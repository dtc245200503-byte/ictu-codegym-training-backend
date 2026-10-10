from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class LoginUser(BaseModel):
    id: int
    username: str
    email: EmailStr
    full_name: str
    roles: list[str]


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