from pydantic import EmailStr, Field, field_validator
from app.schemas.profile import UpdateProfileRequest
from app.crud import attendance as clock


class UpdateStudentProfile(UpdateProfileRequest):
    full_name: str = Field(min_length=1, max_length=100)
    email: EmailStr = Field(max_length=100)
    entry_level: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=5000)
    expected_token: str = Field(min_length=64, max_length=64)

    @field_validator('email', 'entry_level', 'notes', mode='before')
    @classmethod
    def trim_text(cls, value):
        return (value.strip() or None) if isinstance(value, str) else value

    @field_validator('date_of_birth')
    @classmethod
    def validate_birthday(cls, value):
        if value is not None and (value.year < 1000 or value > clock.today()):
            raise ValueError('Ngày sinh phải từ năm 1000 và không được ở tương lai.')
        return value
