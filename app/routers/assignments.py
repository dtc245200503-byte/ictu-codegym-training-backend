import re
from urllib.parse import quote
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session
from app.core.permissions import require_permission
from app.crud import assignments as crud
from app.crud import attendance as clock
from app.crud.leads import user_roles
from app.database import get_db
from app.models.assignment import Assignment, AssignmentAttachment
from app.models.attendance import ClassSession
from app.models.subject import ClassSubject, Subject
from app.models.training_program import TrainingClass
from app.models.user import User
from app.schemas.assignment import AssignmentInput, AssignmentUpdate, PublishAssignment


def require_view(user: User = Depends(require_permission("ASSIGNMENT_VIEW")), db: Session = Depends(get_db)):
    if not user_roles(db, user) & (crud.TEACHERS | {"STUDENT"}):
        raise HTTPException(403, "Bài tập dành cho giảng viên và học viên.")
    return user


def require_teacher(user: User = Depends(require_view), db: Session = Depends(get_db)):
    if not crud.can_create(db, user):
        raise HTTPException(403, "Bạn không có quyền soạn và giao bài tập.")
    return user


router = APIRouter(prefix="/api/assignments", tags=["Bài tập"])


@router.get("/options")
def options(user: User = Depends(require_view), db: Session = Depends(get_db)):
    rows = []
    if crud.can_create(db, user):
        rows = crud.teaching_sessions(db, user).join(TrainingClass, TrainingClass.class_id == ClassSubject.class_id).join(
            Subject, Subject.id == ClassSubject.subject_id).filter(ClassSession.status != "cancelled",
            TrainingClass.status.in_(["planned", "in_progress"])).with_entities(
            ClassSession.session_id, ClassSession.class_subject_id, ClassSession.session_date, ClassSession.start_time,
            ClassSession.topic, TrainingClass.class_id, TrainingClass.class_name, Subject.subject_name).order_by(
            TrainingClass.class_name, Subject.subject_name, ClassSession.session_date, ClassSession.start_time).all()
    return {"can_create": crud.can_create(db, user), "now": clock.iso(clock.utcnow()), "sessions": [{
        "session_id": row[0], "class_subject_id": row[1], "session_date": row[2], "start_time": row[3].strftime("%H:%M"),
        "topic": row[4], "class_id": row[5], "class_name": row[6], "subject_name": row[7]} for row in rows]}


@router.get("")
def listing(page: int = Query(1, ge=1), user: User = Depends(require_view), db: Session = Depends(get_db)):
    query = crud.scoped(db, user)
    return {"total": query.count(), "items": [crud.detail(db, row, user) for row in query.order_by(
        Assignment.created_at.desc(), Assignment.assignment_id.desc()).offset((page - 1) * 20).limit(20)]}


@router.post("", status_code=201)
def create(data: AssignmentInput, user: User = Depends(require_teacher), db: Session = Depends(get_db)):
    crud.target(db, user, data.class_subject_id, data.session_id, lock=True)
    crud.require_future(data.due_at)
    now = clock.utcnow()
    row = Assignment(**data.model_dump(), author_id=user.user_id, created_at=now, updated_at=now)
    db.add(row)
    db.commit()
    return crud.detail(db, row, user)


@router.get("/{id}")
def detail(id: int, user: User = Depends(require_view), db: Session = Depends(get_db)):
    return crud.detail(db, crud.find(db, user, id), user)


@router.put("/{id}")
def update(id: int, data: AssignmentUpdate, user: User = Depends(require_teacher), db: Session = Depends(get_db)):
    crud.require_future(data.due_at)
    row = crud.find(db, user, id)
    # Keep the class/session fixed for a saved draft, so its files never move to
    # another class through stale editing tabs.
    if (row.class_subject_id, row.session_id) != (data.class_subject_id, data.session_id):
        raise HTTPException(422, "Môn và buổi học của bản nháp đã lưu không thể thay đổi.")
    row = crud.claim_draft(db, user, id, data.expected_version)
    crud.require_future(data.due_at)
    for key, value in data.model_dump(exclude={"expected_version"}).items():
        setattr(row, key, value)
    db.commit()
    return crud.detail(db, row, user)


@router.post("/{id}/publish")
def publish(id: int, data: PublishAssignment, user: User = Depends(require_teacher), db: Session = Depends(get_db)):
    try:
        row = crud.publish(db, user, id, data.expected_version)
        return crud.detail(db, row, user)
    except Exception:
        db.rollback()
        raise


@router.post("/{id}/attachments", status_code=201)
def upload(id: int, expected_version: int = Query(..., gt=0), file: UploadFile = File(...),
           user: User = Depends(require_teacher), db: Session = Depends(get_db)):
    try:
        content = file.file.read(crud.MAX_FILE_BYTES + 1)
        if not content or len(content) > crud.MAX_FILE_BYTES:
            raise HTTPException(422, "Mỗi tệp phải có nội dung và không quá 10 MB.")
        filename = re.sub(r'[\x00-\x1f\x7f<>:"/\\|?*]', "_", file.filename or "tep-dinh-kem").strip(" .")[:200] or "tep-dinh-kem"
        row = crud.claim_draft(db, user, id, expected_version)
        existing = db.query(AssignmentAttachment).filter_by(assignment_id=id).all()
        if len(existing) >= crud.MAX_FILES or sum(item.size for item in existing) + len(content) > crud.MAX_TOTAL_BYTES:
            raise HTTPException(422, "Tối đa 5 tệp, tổng dung lượng không quá 25 MB.")
        db.add(AssignmentAttachment(assignment_id=id, filename=filename, size=len(content), content=content))
        db.commit()
        return crud.detail(db, row, user)
    except Exception:
        db.rollback()
        raise
    finally:
        file.file.close()


@router.delete("/{id}/attachments/{attachment_id}")
def remove_attachment(id: int, attachment_id: int, expected_version: int = Query(..., gt=0),
                      user: User = Depends(require_teacher), db: Session = Depends(get_db)):
    row = crud.claim_draft(db, user, id, expected_version)
    file = db.query(AssignmentAttachment).filter_by(assignment_id=id, attachment_id=attachment_id).first()
    if file is None:
        db.rollback()
        raise HTTPException(404, "Không tìm thấy tệp.")
    db.delete(file)
    db.commit()
    return crud.detail(db, row, user)


@router.get("/{id}/attachments/{attachment_id}")
def download(id: int, attachment_id: int, user: User = Depends(require_view), db: Session = Depends(get_db)):
    crud.find(db, user, id)
    file = db.query(AssignmentAttachment).filter_by(assignment_id=id, attachment_id=attachment_id).first()
    if file is None:
        raise HTTPException(404, "Không tìm thấy tệp.")
    return Response(file.content, media_type="application/octet-stream", headers={
        "Content-Disposition": f"attachment; filename=\"attachment-{attachment_id}\"; filename*=UTF-8''{quote(file.filename, safe='')}",
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox",
    })
