from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class CreateUserRequest(BaseModel):
    username: str
    full_name: str
    email: EmailStr
    phone: Optional[str] = None
    role_id: int


class UpdateUserRequest(BaseModel):
    full_name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None


class LockUserRequest(BaseModel):
    lock_reason: str = Field(
        min_length=1,
        max_length=255
    )


class ImportUserRow(BaseModel):
    row_index: int
    full_name: str
    email: str
    phone: Optional[str] = None
    role_input: str
    role_name: str
    role_id: Optional[int] = None
    is_valid: bool
    errors: list[str] = []


class ImportPreviewResponse(BaseModel):
    total_rows: int
    valid_count: int
    invalid_count: int
    rows: list[ImportUserRow]


class ImportConfirmUserItem(BaseModel):
    row_index: int
    full_name: str
    email: str
    phone: Optional[str] = None
    role_id: int


class ConfirmImportRequest(BaseModel):
    users: list[ImportConfirmUserItem] = Field(min_length=1, max_length=100)


class ImportSummaryResponse(BaseModel):
    message: str
    total_rows: int
    success_count: int
    failed_count: int
    errors: list[str] = []
    summary_text: str
