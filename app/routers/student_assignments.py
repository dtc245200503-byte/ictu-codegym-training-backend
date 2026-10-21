from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from app.core.permissions import require_permission
from app.crud.leads import user_roles
from app.crud.student_assignments import listing
from app.crud import assignments as assignments_crud
from app.models.assignment import Assignment, AssignmentRecipient
from app.database import get_db
from app.models.user import User

router = APIRouter(prefix="/api/assignments", tags=["Bài tập của tôi"])


def require_student(user: User = Depends(require_permission("ASSIGNMENT_VIEW")), db: Session = Depends(get_db)):
    if "STUDENT" not in user_roles(db, user):
        raise HTTPException(403, "Danh sách này dành cho học viên.")
    return user


@router.get("/mine")
def mine(status: Literal["not_submitted", "submitted", "graded", "needs_revision"] = "not_submitted",
         sort: Literal["due_asc", "due_desc"] = "due_asc", page: int = Query(1, ge=1),
         page_size: int = Query(20, ge=1, le=100),
         user: User = Depends(require_student), db: Session = Depends(get_db)):
    return listing(db, user, status, sort, page, page_size)


@router.get("/mine/{id}")
def detail(id: int, user: User = Depends(require_student), db: Session = Depends(get_db)):
    row = db.query(Assignment).join(AssignmentRecipient, AssignmentRecipient.assignment_id == Assignment.assignment_id).filter(
        Assignment.assignment_id == id, Assignment.status == "published", AssignmentRecipient.student_id == user.user_id).first()
    if row is None:
        raise HTTPException(404, "Không tìm thấy bài tập được giao cho bạn.")
    result = assignments_crud.detail(db, row, user)
    result['can_edit'] = False
    return result
