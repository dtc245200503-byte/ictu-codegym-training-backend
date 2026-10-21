from math import ceil
from sqlalchemy import func
from app.crud import attendance as clock
from app.models.assignment import Assignment, AssignmentRecipient
from app.models.assignment_progress import AssignmentProgress
from app.models.subject import ClassSubject, Subject
from app.models.training_program import TrainingClass

STATES = ("not_submitted", "submitted", "graded", "needs_revision")
ACTION_REQUIRED = {"not_submitted", "needs_revision"}
URGENT_HOURS = 24


def listing(db, user, status="not_submitted", sort="due_asc", page=1, page_size=20):
    state = func.coalesce(AssignmentProgress.status, "not_submitted")
    query = db.query(Assignment, state.label("progress_status"), TrainingClass.class_name, Subject.subject_name, TrainingClass.class_id).join(
        AssignmentRecipient, AssignmentRecipient.assignment_id == Assignment.assignment_id).outerjoin(
        AssignmentProgress, AssignmentProgress.recipient_id == AssignmentRecipient.recipient_id).join(
        ClassSubject, ClassSubject.id == Assignment.class_subject_id).join(TrainingClass, TrainingClass.class_id == ClassSubject.class_id).join(
        Subject, Subject.id == ClassSubject.subject_id).filter(
        AssignmentRecipient.student_id == user.user_id, Assignment.status == "published")
    counts = dict.fromkeys(STATES, 0)
    for key, count in query.with_entities(state, func.count(Assignment.assignment_id)).group_by(state):
        counts[key] = count
    total = counts[status]
    page = min(page, max(1, (total + page_size - 1) // page_size))
    rows = query.filter(state == status).order_by(
        Assignment.due_at.asc() if sort == "due_asc" else Assignment.due_at.desc(), Assignment.assignment_id.asc()
    ).offset((page - 1) * page_size).limit(page_size).all()
    now = clock.utcnow()
    items = []
    for assignment, progress_status, class_name, subject_name, class_id in rows:
        remaining = ceil((assignment.due_at - now).total_seconds())
        urgent = "normal"
        if progress_status in ACTION_REQUIRED:
            urgent = "overdue" if assignment.due_at <= now else "due_soon" if remaining <= URGENT_HOURS * 3600 else "normal"
        items.append({"assignment_id": assignment.assignment_id, "title": assignment.title,
            "class_id": class_id, "class_name": class_name, "subject_name": subject_name,
            "due_at": clock.iso(assignment.due_at), "max_points": str(assignment.max_points),
            "progress_status": progress_status, "remaining_seconds": remaining, "urgency": urgent})
    return {"items": items, "counts": counts, "total": total, "page": page, "page_size": page_size,
            "status": status, "sort": sort, "now": clock.iso(now), "urgent_window_hours": URGENT_HOURS}
