from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.permissions import require_permission
from app.crud import attendance_summary as crud
from app.crud.leads import user_roles
from app.database import get_db
from app.models.training_program import TrainingClass
from app.models.user import User


def require_staff(user: User = Depends(require_permission("ATTENDANCE_VIEW")), db: Session = Depends(get_db)):
    if not user_roles(db, user) & crud.STAFF_ROLES:
        raise HTTPException(403, "Bảng chuyên cần dành cho giảng viên và quản lý đào tạo.")
    return user


router = APIRouter(prefix="/api/attendance-summary", tags=["Chuyên cần"])


@router.get("/classes")
def classes(user: User = Depends(require_staff), db: Session = Depends(get_db)):
    return {"items": [{"class_id": row.class_id, "class_name": row.class_name}
                      for row in crud.class_scope(db, user).order_by(TrainingClass.class_name, TrainingClass.class_id)]}


@router.get("/classes/{class_id}")
def summary(class_id: int, sort: Literal["name", "rate_asc"] = "name", page: int = Query(1, ge=1),
            page_size: int = Query(50, ge=1, le=100), user: User = Depends(require_staff), db: Session = Depends(get_db)):
    return crud.summary(db, user, class_id, sort, page, page_size)
