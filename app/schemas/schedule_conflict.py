from pydantic import BaseModel, Field


class ConflictConfirmation(BaseModel):
    acknowledge_conflicts: bool = Field(default=False, strict=True)
    conflict_token: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


CONFIRMATION_FIELDS = {"acknowledge_conflicts", "conflict_token"}
