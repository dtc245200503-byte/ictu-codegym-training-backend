from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: "LoginUser"


class LoginUser(BaseModel):
    id: int
    username: str
    email: EmailStr
    full_name: str
    roles: list[str]


LoginResponse.model_rebuild()
