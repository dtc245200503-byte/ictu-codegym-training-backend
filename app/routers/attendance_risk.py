from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from app.core.permissions import require_permission
from app.crud.leads import user_roles
from app.crud import attendance_risk as crud
from app.database import get_db
from app.models.user import User
from app.models.training_program import TrainingProgram
from app.models.attendance_risk import ProgramAbsencePolicy

router = APIRouter(prefix="/api/attendance-risks", tags=["Cảnh báo chuyên cần"])


class PolicyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    threshold_percent: Decimal = Field(ge=0, le=100, max_digits=5, decimal_places=2)
    expected_threshold_percent: Decimal = Field(ge=0, le=100, max_digits=5, decimal_places=2)


def program(db, id):
    row = db.get(TrainingProgram, id)
    if row is None or row.deleted_at is not None:
        raise HTTPException(404, "Không tìm thấy chương trình đào tạo.")
    return row


@router.get("/programs/{course_id}/policy")
def policy(course_id: int, user: User = Depends(require_permission("CURRICULUM_MANAGE")), db: Session = Depends(get_db)):
    item = program(db, course_id)
    config = db.get(ProgramAbsencePolicy, course_id)
    return {"course_id": course_id, "program_name": item.course_name,
            "threshold_percent": config.threshold_percent if config else crud.DEFAULT_THRESHOLD,
            "can_edit": bool(user_roles(db, user) & {"TRAINING_MANAGER", "MANAGER"})}


@router.put("/programs/{course_id}/policy")
def update_policy(course_id: int, data: PolicyInput, user: User = Depends(require_permission("CURRICULUM_MANAGE")), db: Session = Depends(get_db)):
    if not user_roles(db, user) & {"TRAINING_MANAGER", "MANAGER"}:
        raise HTTPException(403, "Chỉ Quản lý đào tạo được cấu hình ngưỡng vắng.")
    try:
        crud.begin_sync(db)
        program(db, course_id)
        row = db.query(ProgramAbsencePolicy).filter_by(course_id=course_id).populate_existing().with_for_update().first()
        current = row.threshold_percent if row else crud.DEFAULT_THRESHOLD
        if current != data.expected_threshold_percent:
            raise HTTPException(409, "Ngưỡng đã thay đổi. Vui lòng tải lại cấu hình trước khi lưu.")
        if row is None:
            row = ProgramAbsencePolicy(course_id=course_id)
            db.add(row)
        row.threshold_percent = data.threshold_percent
        db.flush()
        crud.evaluate(db)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return policy(course_id, user, db)
