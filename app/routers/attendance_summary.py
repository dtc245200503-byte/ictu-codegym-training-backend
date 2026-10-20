from app.crud import attendance_risk
from typing import Literal
from urllib.parse import quote
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session
from app.attendance_export import MIME_TYPE, filename, workbook_bytes

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
    attendance_risk.refresh(db)
    result = crud.summary(db, user, class_id, sort, page, page_size)
    flags = {row['student_id']: row for row in attendance_risk.active_flags(db, class_ids=[class_id])}
    for row in result['students']:
        row['attendance_risk'] = flags.get(row['student_id'])
    result["can_export"] = bool(user_roles(db, user) & {"TRAINING_MANAGER", "MANAGER"})
    return result


@router.get("/classes/{class_id}/export")
def export(class_id: int, sort: Literal["name", "rate_asc"] = "name",
           user: User = Depends(require_staff), db: Session = Depends(get_db)):
    if not user_roles(db, user) & {"TRAINING_MANAGER", "MANAGER"}:
        raise HTTPException(403, "Chỉ Quản lý đào tạo được xuất bảng chuyên cần.")
    result = crud.summary(db, user, class_id, sort, page_size=None)
    name = filename(result)
    fallback = f"Chuyen-can_LOP-{class_id}_{result['today'].isoformat()}.xlsx"
    return Response(workbook_bytes(result), media_type=MIME_TYPE, headers={
        "Content-Disposition": f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}",
        "Cache-Control": "no-store",
    })
