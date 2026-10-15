from datetime import date, datetime, timezone
from typing import Annotated, Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, or_
from sqlalchemy.orm import Session
from app.database import get_db
from app.core.auth import get_current_user
from app.core.permissions import user_has_permission
from app.crud.leads import user_roles
from app.models.training_program import TrainingClass, TrainingProgram
from app.models.subject import ClassSubject, ClassSubjectAssistant, Subject
from app.models.user import User
from app.models.role import Role
from app.models.user_role import UserRole

MANAGER_ROLES = {"ADMIN", "TRAINING_MANAGER", "MANAGER", "ACADEMIC_AFFAIRS"}
TEACHING_ROLES = {"INSTRUCTOR", "TEACHER", "ASSISTANT", "TA"}


def require_class_access(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not (user_has_permission(user.user_id, "CLASS_MANAGE", db) or user_roles(db, user) & TEACHING_ROLES):
        raise HTTPException(status_code=403, detail="Bạn không có quyền xem lớp học.")
    return user


def require_assignment_manager(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not (user_roles(db, user) & MANAGER_ROLES and user_has_permission(user.user_id, "CLASS_MANAGE", db)):
        raise HTTPException(status_code=403, detail="Chỉ người quản lý có quyền quản lý lớp học mới được phân công giảng dạy.")
    return user


router = APIRouter(prefix="/api/classes", tags=["Lớp học"], dependencies=[Depends(require_class_access)])


class AssistantResponse(BaseModel):
    user_id: int
    full_name: str
    email: str
    username: str | None = None


class CandidateUser(BaseModel):
    user_id: int
    full_name: str
    email: str
    username: str


class ClassCandidatesResponse(BaseModel):
    instructors: list[CandidateUser]
    assistants: list[CandidateUser]


class ClassSubjectResponse(BaseModel):
    subject_id: int
    code: str
    name: str
    session_count: int
    instructor_id: int | None = None
    instructor_name: str | None = None
    instructor_email: str | None = None
    assistants: list[AssistantResponse] = []


class ClassResponse(BaseModel):
    class_id: int
    class_name: str
    course_id: int | None
    program_code: str | None
    program_name: str | None
    instructor_id: int | None
    instructor_name: str | None
    start_date: date | None
    status: str
    subjects: list[ClassSubjectResponse]


class ClassListResponse(BaseModel):
    items: list[ClassResponse]
    total: int
    page: int
    page_size: int


PositiveId = Annotated[int, Field(strict=True, gt=0)]


class SubjectAssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instructor_id: PositiveId
    assistant_ids: list[PositiveId] = Field(default_factory=list, max_length=100)


class BatchSubjectAssignmentItem(SubjectAssignmentRequest):
    subject_id: PositiveId


class BatchAssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assignments: list[BatchSubjectAssignmentItem] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_subjects(self):
        if len({item.subject_id for item in self.assignments}) != len(self.assignments):
            raise ValueError("Mỗi môn học chỉ được xuất hiện một lần trong danh sách phân công.")
        return self


class ClassAssignmentDetailResponse(BaseModel):
    class_id: int
    class_name: str
    course_id: int | None
    program_code: str | None
    program_name: str | None
    subjects: list[ClassSubjectResponse]
    available_instructors: list[CandidateUser]
    available_assistants: list[CandidateUser]


def get_role_candidates(db: Session, role_names: list[str]) -> list[CandidateUser]:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    users = (
        db.query(User)
        .join(UserRole, UserRole.user_id == User.user_id)
        .join(Role, Role.role_id == UserRole.role_id)
        .filter(
            Role.role_name.in_(role_names),
            User.is_locked.is_(False),
            (User.locked_until.is_(None)) | (User.locked_until <= now),
        )
        .order_by(User.full_name, User.user_id)
        .distinct()
        .all()
    )
    return [
        CandidateUser(
            user_id=u.user_id,
            full_name=u.full_name or u.username,
            email=u.email,
            username=u.username,
        )
        for u in users
    ]


def validate_instructor(db: Session, instructor_id: int | None) -> User | None:
    if instructor_id is None:
        return None
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    user = db.query(User).filter(User.user_id == instructor_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Không tìm thấy giảng viên được chọn.")
    if user.is_locked or (user.locked_until and user.locked_until > now):
        raise HTTPException(status_code=422, detail="Tài khoản giảng viên đang bị khóa.")
    roles = {
        r[0]
        for r in db.query(Role.role_name)
        .join(UserRole, UserRole.role_id == Role.role_id)
        .filter(UserRole.user_id == instructor_id)
        .all()
    }
    if not roles.intersection({"INSTRUCTOR", "TEACHER"}):
        raise HTTPException(
            status_code=422,
            detail="Chỉ tài khoản có vai trò Giảng viên mới được phân công làm giảng viên chính.",
        )
    return user


def validate_assistants(db: Session, assistant_ids: list[int], instructor_id: int | None = None) -> list[User]:
    if not assistant_ids:
        return []
    unique_ids = list(dict.fromkeys(assistant_ids))
    if instructor_id is not None and instructor_id in unique_ids:
        raise HTTPException(
            status_code=422,
            detail="Giảng viên chính không thể đồng thời là trợ giảng cho cùng một môn.",
        )
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    users = db.query(User).filter(User.user_id.in_(unique_ids)).all()
    if len(users) != len(unique_ids):
        raise HTTPException(status_code=404, detail="Một hoặc nhiều trợ giảng được chọn không tồn tại.")
    for u in users:
        if u.is_locked or (u.locked_until and u.locked_until > now):
            raise HTTPException(status_code=422, detail=f"Tài khoản trợ giảng {u.full_name} đang bị khóa.")
        roles = {
            r[0]
            for r in db.query(Role.role_name)
            .join(UserRole, UserRole.role_id == Role.role_id)
            .filter(UserRole.user_id == u.user_id)
            .all()
        }
        if not roles.intersection({"ASSISTANT", "TA"}):
            raise HTTPException(
                status_code=422,
                detail=f"Chỉ tài khoản có vai trò Trợ giảng mới được phân công làm trợ giảng ({u.full_name}).",
            )
    return users


def load_class_subjects_dict(db: Session, class_ids: list[int]) -> dict[int, list[ClassSubjectResponse]]:
    if not class_ids:
        return {}
    cs_rows = (
        db.query(ClassSubject, Subject, User)
        .join(Subject, Subject.id == ClassSubject.subject_id)
        .outerjoin(User, User.user_id == ClassSubject.instructor_id)
        .filter(ClassSubject.class_id.in_(class_ids))
        .order_by(Subject.id)
        .all()
    )
    cs_ids = [cs.id for cs, _, _ in cs_rows]
    ta_map: dict[int, list[AssistantResponse]] = {}
    if cs_ids:
        ta_rows = (
            db.query(ClassSubjectAssistant.class_subject_id, User)
            .join(User, User.user_id == ClassSubjectAssistant.user_id)
            .filter(ClassSubjectAssistant.class_subject_id.in_(cs_ids))
            .order_by(User.full_name, User.user_id)
            .all()
        )
        for cs_id, ta in ta_rows:
            ta_map.setdefault(cs_id, []).append(
                AssistantResponse(
                    user_id=ta.user_id,
                    full_name=ta.full_name or ta.username,
                    email=ta.email,
                    username=ta.username,
                )
            )

    result: dict[int, list[ClassSubjectResponse]] = {}
    for cs, subj, inst in cs_rows:
        result.setdefault(cs.class_id, []).append(
            ClassSubjectResponse(
                subject_id=subj.id,
                code=subj.code,
                name=subj.subject_name,
                session_count=subj.session_count,
                instructor_id=cs.instructor_id,
                instructor_name=inst.full_name if inst else None,
                instructor_email=inst.email if inst else None,
                assistants=ta_map.get(cs.id, []),
            )
        )
    return result


@router.get("/candidates", response_model=ClassCandidatesResponse, dependencies=[Depends(require_assignment_manager)])
def get_assignment_candidates(db: Session = Depends(get_db)):
    """Fetch eligible instructors and teaching assistants for assignment selection."""
    instructors = get_role_candidates(db, ["INSTRUCTOR", "TEACHER"])
    assistants = get_role_candidates(db, ["ASSISTANT", "TA"])
    return {"instructors": instructors, "assistants": assistants}


@router.get("", response_model=ClassListResponse)
def list_classes(search: str = Query("", max_length=100),
                 status: Literal["planned", "in_progress", "completed", "cancelled"] | None = None,
                 course_id: int | None = Query(None, gt=0), page: int = Query(1, ge=1),
                 page_size: int = Query(20, ge=1, le=100),
                 user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    query = db.query(TrainingClass, TrainingProgram, User).outerjoin(
        TrainingProgram, TrainingProgram.course_id == TrainingClass.course_id).outerjoin(
        User, User.user_id == TrainingClass.instructor_id)

    # Subject assignments are authoritative: replacing a teacher revokes access
    # even when the legacy class-level instructor field still contains their ID.
    if not (user_roles(db, user) & MANAGER_ROLES and user_has_permission(user.user_id, "CLASS_MANAGE", db)):
        assigned_subj_class_ids = (
            db.query(ClassSubject.class_id)
            .filter(ClassSubject.instructor_id == user.user_id)
        )
        assigned_ta_class_ids = (
            db.query(ClassSubject.class_id)
            .join(ClassSubjectAssistant, ClassSubjectAssistant.class_subject_id == ClassSubject.id)
            .filter(ClassSubjectAssistant.user_id == user.user_id)
        )
        query = query.filter(
            or_(
                TrainingClass.class_id.in_(assigned_subj_class_ids),
                TrainingClass.class_id.in_(assigned_ta_class_ids),
            )
        )

    if search.strip():
        term = search.strip().lower()
        subject_teachers = db.query(ClassSubject.class_id).join(User, User.user_id == ClassSubject.instructor_id).filter(
            func.lower(User.full_name).contains(term, autoescape=True))
        query = query.filter(or_(TrainingClass.class_id.in_(subject_teachers),
            *(func.lower(column).contains(term, autoescape=True) for column in [
                TrainingClass.class_name, TrainingProgram.code, TrainingProgram.course_name, User.full_name])))
    if status:
        query = query.filter(TrainingClass.status == status)
    if course_id is not None:
        query = query.filter(TrainingClass.course_id == course_id)
    total = query.count()
    rows = query.order_by(TrainingClass.class_id.desc()).offset((page-1)*page_size).limit(page_size).all()
    ids = [row[0].class_id for row in rows]
    subjects_map = load_class_subjects_dict(db, ids)

    return {"total": total, "page": page, "page_size": page_size, "items": [
        {"class_id": item.class_id, "class_name": item.class_name, "course_id": item.course_id,
         "program_code": program.code if program else None, "program_name": program.course_name if program else None,
         "instructor_id": item.instructor_id, "instructor_name": instructor.full_name if instructor else None,
         "start_date": item.start_date, "status": item.status, "subjects": subjects_map.get(item.class_id, [])}
        for item, program, instructor in rows]}


@router.get("/{class_id}/assignments", response_model=ClassAssignmentDetailResponse, dependencies=[Depends(require_assignment_manager)])
def get_class_assignments(class_id: int, db: Session = Depends(get_db)):
    """Get subject teacher and assistant assignments for a specific class."""
    training_class = db.query(TrainingClass).filter(TrainingClass.class_id == class_id).first()
    if not training_class:
        raise HTTPException(status_code=404, detail="Không tìm thấy lớp học.")

    program = db.query(TrainingProgram).filter(TrainingProgram.course_id == training_class.course_id).first()
    subjects_map = load_class_subjects_dict(db, [class_id])
    instructors = get_role_candidates(db, ["INSTRUCTOR", "TEACHER"])
    assistants = get_role_candidates(db, ["ASSISTANT", "TA"])

    return {
        "class_id": training_class.class_id,
        "class_name": training_class.class_name,
        "course_id": training_class.course_id,
        "program_code": program.code if program else None,
        "program_name": program.course_name if program else None,
        "subjects": subjects_map.get(class_id, []),
        "available_instructors": instructors,
        "available_assistants": assistants,
    }


@router.put("/{class_id}/subjects/{subject_id}/assignment", response_model=ClassSubjectResponse, dependencies=[Depends(require_assignment_manager)])
def assign_subject_teachers(
    class_id: int,
    subject_id: int,
    payload: SubjectAssignmentRequest,
    db: Session = Depends(get_db),
):
    """Assign main lecturer (1) and teaching assistants (multiple) to a specific subject in a class."""
    training_class = db.query(TrainingClass).filter(TrainingClass.class_id == class_id).with_for_update().first()
    if not training_class:
        raise HTTPException(status_code=404, detail="Không tìm thấy lớp học.")

    cs = db.query(ClassSubject).filter(
        ClassSubject.class_id == class_id,
        ClassSubject.subject_id == subject_id,
    ).first()
    if not cs:
        raise HTTPException(status_code=404, detail="Môn học không thuộc lớp này.")

    # Validate lecturer (must have INSTRUCTOR role)
    validate_instructor(db, payload.instructor_id)

    # Validate assistants (must have ASSISTANT/TA role, cannot overlap with main lecturer)
    validate_assistants(db, payload.assistant_ids, payload.instructor_id)

    # Apply instructor
    cs.instructor_id = payload.instructor_id

    # Apply assistants: clear old, insert new
    db.query(ClassSubjectAssistant).filter(ClassSubjectAssistant.class_subject_id == cs.id).delete()
    unique_assistant_ids = list(dict.fromkeys(payload.assistant_ids))
    for ta_id in unique_assistant_ids:
        db.add(ClassSubjectAssistant(class_subject_id=cs.id, user_id=ta_id))

    db.commit()

    subjects_map = load_class_subjects_dict(db, [class_id])
    class_subjects = subjects_map.get(class_id, [])
    updated = next((s for s in class_subjects if s.subject_id == subject_id), None)
    if not updated:
        raise HTTPException(status_code=500, detail="Không thể tải lại thông tin phân công.")
    return updated


@router.put("/{class_id}/assignments", response_model=ClassAssignmentDetailResponse, dependencies=[Depends(require_assignment_manager)])
def save_batch_class_assignments(
    class_id: int,
    payload: BatchAssignmentRequest,
    db: Session = Depends(get_db),
):
    """Batch assign main lecturer and teaching assistants for all subjects in a class."""
    training_class = db.query(TrainingClass).filter(TrainingClass.class_id == class_id).with_for_update().first()
    if not training_class:
        raise HTTPException(status_code=404, detail="Không tìm thấy lớp học.")

    class_subjects = db.query(ClassSubject).filter(ClassSubject.class_id == class_id).all()
    cs_dict = {cs.subject_id: cs for cs in class_subjects}

    for item in payload.assignments:
        if item.subject_id not in cs_dict:
            raise HTTPException(status_code=400, detail=f"Môn học ID {item.subject_id} không thuộc lớp này.")
        validate_instructor(db, item.instructor_id)
        validate_assistants(db, item.assistant_ids, item.instructor_id)

    # Perform updates
    for item in payload.assignments:
        cs = cs_dict[item.subject_id]
        cs.instructor_id = item.instructor_id
        db.query(ClassSubjectAssistant).filter(ClassSubjectAssistant.class_subject_id == cs.id).delete()
        for ta_id in list(dict.fromkeys(item.assistant_ids)):
            db.add(ClassSubjectAssistant(class_subject_id=cs.id, user_id=ta_id))

    db.commit()
    return get_class_assignments(class_id=class_id, db=db)
