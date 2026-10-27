from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session
from fastapi.responses import Response

from app.api.dependencies import get_current_user, require_roles
from app.api.pagination import page_metadata, set_pagination_headers
from app.db.session import get_db
from app.models.academic import CourseFinalGrade, GradePolicy
from app.models.course import Course
from app.models.study_program import StudyProgram, program_subjects
from app.models.training_class import ClassEnrollment, TrainingClass
from app.models.user import User
from app.models.student_profile import StudentProfile
from app.models.academic import GraduationEvent, GraduationRecord
from app.schemas.academic import GraduationConfirmBatch, GraduationThresholdUpdate
from app.schemas.management import GradePolicyCreate, GradeReopenRequest
from app.services.academic_grading import (
    calculate_class_final_grades,
    latest_final_grade,
    next_final_grade_version,
    resolve_grade_policy,
)
from app.services.receipt_pdf import build_receipt_pdf
from app.services.graduation import evaluate_student_graduation

router = APIRouter(tags=["Academic grading"])


@router.put("/study-programs/{program_id}/graduation-policy")
def update_graduation_policy(
    program_id: int, payload: GraduationThresholdUpdate,
    db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "manager")),
):
    program = db.get(StudyProgram, program_id)
    if program is None:
        raise HTTPException(status_code=404, detail="Study program not found.")
    program.graduation_attendance_threshold = payload.attendance_threshold_percent
    db.commit()
    return {"program_id": program.id,
            "attendance_threshold_percent": str(program.graduation_attendance_threshold)}


@router.get("/study-programs/{program_id}/graduation-eligibility")
def graduation_eligibility(
    program_id: int, page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100), db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "manager")),
):
    program = db.get(StudyProgram, program_id)
    if program is None:
        raise HTTPException(status_code=404, detail="Study program not found.")
    course_ids = list(db.scalars(select(program_subjects.c.subject_id).where(
        program_subjects.c.program_id == program.id
    )).all())
    student_query = select(ClassEnrollment.student_id).join(
        TrainingClass, ClassEnrollment.class_id == TrainingClass.id
    ).where(TrainingClass.course_id.in_(course_ids or [-1]),
            ClassEnrollment.enrollment_status.in_(("active", "completed"))).distinct()
    total = db.scalar(select(func.count()).select_from(student_query.subquery())) or 0
    student_ids = db.scalars(student_query.order_by(ClassEnrollment.student_id)
        .offset((page - 1) * page_size).limit(page_size)).all()
    results = [evaluate_student_graduation(db, program=program, student=student)
               for student in db.scalars(select(User).where(User.id.in_(student_ids or [-1]))
                   .order_by(User.id)).all()]
    return {"program_id": program_id,
            "attendance_threshold_configured": program.graduation_attendance_threshold is not None,
            **page_metadata(total=total, page=page, page_size=page_size),
            "eligible": [row for row in results if row["eligible"]],
            "ineligible": [row for row in results if not row["eligible"]]}


@router.post("/study-programs/{program_id}/graduations/confirm")
def confirm_graduations(
    program_id: int, payload: GraduationConfirmBatch,
    db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "manager")),
):
    program = db.get(StudyProgram, program_id)
    if program is None:
        raise HTTPException(status_code=404, detail="Study program not found.")
    if program.graduation_attendance_threshold is None:
        raise HTTPException(status_code=409, detail="Configure the program attendance threshold before graduation review.")
    students = db.scalars(select(User).where(User.id.in_(payload.student_ids)).with_for_update()).all()
    if len(students) != len(payload.student_ids):
        raise HTTPException(status_code=404, detail="One or more students were not found.")
    snapshots = [evaluate_student_graduation(db, program=program, student=student) for student in students]
    ineligible = [item for item in snapshots if not item["eligible"]]
    if ineligible:
        raise HTTPException(status_code=409, detail={"message": "Some students are not eligible.", "students": ineligible})
    already_graduated = db.scalars(select(GraduationRecord.student_id).where(
        GraduationRecord.program_id == program_id,
        GraduationRecord.student_id.in_(payload.student_ids),
        GraduationRecord.status == "graduated",
    ).with_for_update()).all()
    if already_graduated:
        raise HTTPException(status_code=409, detail={
            "message": "Graduation confirmation already exists for one or more students.",
            "student_ids": already_graduated,
        })
    for snapshot in snapshots:
        student_id = snapshot["student_id"]
        record = db.scalar(select(GraduationRecord).where(
            GraduationRecord.student_id == student_id,
            GraduationRecord.program_id == program_id,
        ).with_for_update())
        if record is None:
            record = GraduationRecord(student_id=student_id, program_id=program_id,
                status="graduated", confirmed_by=user.id,
                confirmed_at=datetime.now(timezone.utc).replace(tzinfo=None),
                eligibility_snapshot=snapshot)
            db.add(record)
        elif record.status != "graduated":
            record.status = "graduated"
            record.confirmed_by = user.id
            record.confirmed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            record.eligibility_snapshot = snapshot
        profile = db.get(StudentProfile, student_id)
        if profile is None:
            profile = StudentProfile(user_id=student_id, graduation_status="graduated")
            db.add(profile)
        else:
            profile.graduation_status = "graduated"
        db.add(GraduationEvent(student_id=student_id, program_id=program_id,
            confirmed_by=user.id, eligibility_snapshot=snapshot))
    db.commit()
    return {"program_id": program_id, "confirmed_count": len(snapshots),
            "student_ids": [item["student_id"] for item in snapshots]}


@router.get("/study-programs/{program_id}/graduations/history")
def graduation_history(
    program_id: int, response: Response, page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100), db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "manager")),
):
    if db.get(StudyProgram, program_id) is None:
        raise HTTPException(status_code=404, detail="Study program not found.")
    query = select(GraduationEvent).where(GraduationEvent.program_id == program_id)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    events = db.scalars(query.order_by(GraduationEvent.confirmed_at.desc(), GraduationEvent.id.desc())
        .offset((page - 1) * page_size).limit(page_size)).all()
    set_pagination_headers(response, total=total, page=page, page_size=page_size)
    return [{"id": event.id, "student_id": event.student_id,
             "confirmed_by": event.confirmed_by,
             "confirmed_at": event.confirmed_at.isoformat(),
             "eligibility_snapshot": event.eligibility_snapshot} for event in events]


def _transcript(
    db: Session, student: User, *, page: int = 1, page_size: int = 20,
    include_all: bool = False,
) -> dict:
    course_query = (
        select(Course.id.label("course_id"), Course.name.label("course_name"))
        .join(TrainingClass, TrainingClass.course_id == Course.id)
        .join(ClassEnrollment, ClassEnrollment.class_id == TrainingClass.id)
        .where(ClassEnrollment.student_id == student.id)
        .group_by(Course.id, Course.name)
    )
    total = db.scalar(select(func.count()).select_from(course_query.subquery())) or 0
    if include_all:
        page_rows = db.execute(course_query.order_by(Course.name, Course.id)).all()
        page_size = max(total, 1)
    else:
        page_rows = db.execute(course_query.order_by(Course.name, Course.id)
            .offset((page - 1) * page_size).limit(page_size)).all()
    course_ids = [row.course_id for row in page_rows]
    courses: dict[int, dict] = {
        row.course_id: {
            "course_id": row.course_id, "course_name": row.course_name,
            "classes": [], "final_score": None, "result": "incomplete",
            "grade_breakdown": None, "finalized_at": None,
        }
        for row in page_rows
    }
    class_rows = db.execute(
        select(ClassEnrollment, TrainingClass)
        .join(TrainingClass, ClassEnrollment.class_id == TrainingClass.id)
        .where(ClassEnrollment.student_id == student.id,
            TrainingClass.course_id.in_(course_ids or [-1]))
        .order_by(ClassEnrollment.enrolled_at, ClassEnrollment.id)
    ).all()
    class_ids = [training_class.id for _, training_class in class_rows]
    for enrollment, training_class in class_rows:
        courses[training_class.course_id]["classes"].append({
            "class_id": training_class.id, "class_code": training_class.code,
            "enrollment_status": enrollment.enrollment_status,
        })
    if class_ids:
        grades = db.scalars(select(CourseFinalGrade).where(
            CourseFinalGrade.student_id == student.id,
            CourseFinalGrade.class_id.in_(class_ids),
            CourseFinalGrade.course_id.in_(course_ids),
            CourseFinalGrade.is_locked.is_(True),
        ).order_by(CourseFinalGrade.finalized_at.desc(), CourseFinalGrade.id.desc())).all()
        for grade in grades:
            record = courses.get(grade.course_id)
            if record is not None and record["finalized_at"] is None:
                record.update(final_score=str(grade.score), result=grade.result,
                    grade_breakdown=grade.breakdown, finalized_at=grade.finalized_at.isoformat())

    ranked_grades = select(
        CourseFinalGrade.score.label("score"),
        func.row_number().over(
            partition_by=CourseFinalGrade.course_id,
            order_by=(CourseFinalGrade.finalized_at.desc(), CourseFinalGrade.id.desc()),
        ).label("row_idx"),
    ).join(TrainingClass, TrainingClass.id == CourseFinalGrade.class_id).join(
        ClassEnrollment, and_(ClassEnrollment.class_id == TrainingClass.id,
            ClassEnrollment.student_id == CourseFinalGrade.student_id)
    ).where(CourseFinalGrade.student_id == student.id,
        CourseFinalGrade.course_id == TrainingClass.course_id,
        CourseFinalGrade.is_locked.is_(True)).subquery()
    average = db.scalar(select(func.avg(ranked_grades.c.score)).where(ranked_grades.c.row_idx == 1))
    average_decimal = Decimal(str(average)) if average is not None else None
    result = {
        "student_id": student.id, "student_code": student.student_code,
        "student_name": student.full_name, "issued_on": datetime.now().date().isoformat(),
        "courses": list(courses.values()),
        "course_average": str(average_decimal.quantize(Decimal("0.01"))) if average_decimal is not None else None,
    }
    if not include_all:
        result.update(page_metadata(total=total, page=page, page_size=page_size))
    return result


@router.get("/students/me/transcript")
def get_my_transcript(
    response: Response,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db), user: User = Depends(require_roles("student")),
):
    transcript = _transcript(db, user, page=page, page_size=page_size)
    set_pagination_headers(response, total=transcript["total"], page=page, page_size=page_size)
    return transcript


@router.get("/students/me/transcript.pdf")
def download_my_transcript(
    db: Session = Depends(get_db), user: User = Depends(require_roles("student")),
):
    transcript = _transcript(db, user, include_all=True)
    lines = ["ICTU x CodeGym - Academic Transcript",
             f"Student: {transcript['student_name']} ({transcript['student_code'] or 'N/A'})",
             f"Issue date: {transcript['issued_on']}", "Courses:"]
    for item in transcript["courses"]:
        score = item["final_score"] if item["final_score"] is not None else "Pending"
        lines.append(f"{item['course_name']}: {score} - {item['result']}")
    lines.append(f"Course average: {transcript['course_average'] or 'N/A'}")
    return Response(build_receipt_pdf(lines), media_type="application/pdf",
                    headers={"Content-Disposition": 'attachment; filename="transcript.pdf"'})


def _class_access(user: User, training_class: TrainingClass) -> None:
    roles = {role.code for role in user.roles}
    if roles.intersection({"admin", "manager"}):
        return
    if "teacher" in roles and training_class.teacher_id == user.id:
        return
    if "ta" in roles and (
        training_class.assistant_id == user.id
        or any(item.id == user.id for item in training_class.assistants)
    ):
        return
    raise HTTPException(status_code=403, detail="Class access denied.")


def _grade_payload(row: dict, stored: CourseFinalGrade | None = None) -> dict:
    return {
        **row,
        "final_grade_id": stored.id if stored else None,
        "final_grade_version": stored.version if stored else None,
        "locked": bool(stored and stored.is_locked),
        "finalized_at": stored.finalized_at.isoformat() if stored else None,
    }


@router.post("/grade-policies", status_code=201)
def create_grade_policy(
    payload: GradePolicyCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "manager")),
):
    if db.get(StudyProgram, payload.program_id) is None or db.get(Course, payload.course_id) is None:
        raise HTTPException(status_code=404, detail="Program or course not found.")
    if db.scalar(select(program_subjects.c.program_id).where(
        program_subjects.c.program_id == payload.program_id,
        program_subjects.c.subject_id == payload.course_id,
    )) is None:
        raise HTTPException(status_code=422, detail="Course is not part of the program.")
    if payload.class_id is not None:
        training_class = db.get(TrainingClass, payload.class_id)
        if training_class is None:
            raise HTTPException(status_code=404, detail="Class not found.")
        if training_class.course_id != payload.course_id:
            raise HTTPException(status_code=422, detail="Class course does not match policy course.")
    scope = f"class:{payload.class_id}" if payload.class_id else "program"
    version = int(db.scalar(select(func.max(GradePolicy.version)).where(
        GradePolicy.program_id == payload.program_id,
        GradePolicy.course_id == payload.course_id,
        GradePolicy.scope_key == scope,
    )) or 0) + 1
    policy = GradePolicy(
        program_id=payload.program_id,
        course_id=payload.course_id,
        class_id=payload.class_id,
        scope_key=scope,
        version=version,
        components=[component.model_dump(mode="json") for component in payload.components],
        pass_score=payload.pass_score,
        critical_component_key=payload.critical_component_key,
        critical_score=payload.critical_score,
        created_by=user.id,
    )
    db.add(policy)
    db.commit()
    db.refresh(policy)
    return {"id": policy.id, "program_id": policy.program_id, "course_id": policy.course_id,
            "class_id": policy.class_id, "version": policy.version,
            "components": policy.components, "pass_score": str(policy.pass_score),
            "critical_component_key": policy.critical_component_key,
            "critical_score": str(policy.critical_score) if policy.critical_score is not None else None}


@router.get("/grade-policies")
def list_grade_policies(
    response: Response,
    program_id: int = Query(ge=1), course_id: int = Query(ge=1),
    class_id: int | None = Query(default=None, ge=1),
    page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    if class_id is not None:
        training_class = db.get(TrainingClass, class_id)
        if training_class is None:
            raise HTTPException(status_code=404, detail="Class not found.")
        _class_access(user, training_class)
    else:
        roles = {role.code for role in user.roles}
        if not roles.intersection({"admin", "manager"}):
            raise HTTPException(status_code=403, detail="Class-scoped policy required.")
    scope = GradePolicy.class_id == class_id if class_id is not None else GradePolicy.class_id.is_(None)
    query = select(GradePolicy).where(
        GradePolicy.program_id == program_id, GradePolicy.course_id == course_id, scope,
    )
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    if class_id is not None and total == 0:
        query = select(GradePolicy).where(
            GradePolicy.program_id == program_id, GradePolicy.course_id == course_id,
            GradePolicy.class_id.is_(None),
        )
        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(GradePolicy.version.desc(), GradePolicy.id.desc())
        .offset((page - 1) * page_size).limit(page_size)).all()
    set_pagination_headers(response, total=total, page=page, page_size=page_size)
    return [{"id": p.id, "program_id": p.program_id, "course_id": p.course_id,
             "class_id": p.class_id, "version": p.version, "components": p.components,
             "pass_score": str(p.pass_score), "critical_component_key": p.critical_component_key,
             "critical_score": str(p.critical_score) if p.critical_score is not None else None}
            for p in rows]


@router.get("/classes/{class_id}/final-grades")
def get_class_final_grades(
    class_id: int, program_id: int = Query(ge=1),
    page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    training_class, policy = resolve_grade_policy(db, class_id=class_id, program_id=program_id)
    _class_access(user, training_class)
    student_query = select(ClassEnrollment.student_id).where(
        ClassEnrollment.class_id == class_id, ClassEnrollment.enrollment_status == "active")
    total = db.scalar(select(func.count()).select_from(student_query.subquery())) or 0
    student_ids = db.scalars(student_query.order_by(ClassEnrollment.student_id)
        .offset((page - 1) * page_size).limit(page_size)).all()
    result = []
    for row in calculate_class_final_grades(db, training_class=training_class,
            policy=policy, student_ids=student_ids):
        stored = latest_final_grade(db, class_id=class_id, student_id=row["student_id"], course_id=row["course_id"])
        result.append(_grade_payload(row, stored))
    return {"class_id": class_id, "course_id": training_class.course_id,
            "policy_id": policy.id, "policy_version": policy.version,
            **page_metadata(total=total, page=page, page_size=page_size), "students": result}


@router.post("/classes/{class_id}/final-grades/lock")
def lock_class_final_grades(
    class_id: int, program_id: int = Query(ge=1),
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    training_class, policy = resolve_grade_policy(db, class_id=class_id, program_id=program_id)
    _class_access(user, training_class)
    if not {role.code for role in user.roles}.intersection({"admin", "manager", "teacher"}):
        raise HTTPException(status_code=403, detail="Only a teacher or manager may finalize grades.")
    rows = calculate_class_final_grades(db, training_class=training_class, policy=policy)
    if not rows:
        raise HTTPException(status_code=409, detail="Cannot finalize a class with no active students.")
    incomplete = [row["student_id"] for row in rows if row["result"] == "incomplete"]
    if incomplete:
        raise HTTPException(status_code=409, detail={"message": "Grades are incomplete.", "student_ids": incomplete})
    for row in rows:
        existing = latest_final_grade(db, class_id=class_id, student_id=row["student_id"], course_id=row["course_id"])
        if existing and existing.is_locked:
            if existing.policy_id != policy.id or existing.score != Decimal(row["score"]) or existing.result != row["result"] or existing.breakdown != row["breakdown"]:
                raise HTTPException(status_code=409, detail=f"Student {row['student_id']} grade is locked; manager must reopen it first.")
            continue
        db.add(CourseFinalGrade(
            class_id=class_id, student_id=row["student_id"], course_id=row["course_id"],
            policy_id=policy.id, version=next_final_grade_version(db, class_id=class_id,
                student_id=row["student_id"], course_id=row["course_id"]),
            score=Decimal(row["score"]), result=row["result"], breakdown=row["breakdown"],
            is_locked=True, finalized_by=user.id,
        ))
    db.commit()
    return {"class_id": class_id, "locked_count": len(rows), "students": rows}


@router.post("/classes/{class_id}/students/{student_id}/final-grade/reopen")
def reopen_final_grade(
    class_id: int, student_id: int, payload: GradeReopenRequest,
    db: Session = Depends(get_db), user: User = Depends(require_roles("admin", "manager")),
):
    stored = latest_final_grade(db, class_id=class_id, student_id=student_id,
        course_id=db.scalar(select(TrainingClass.course_id).where(TrainingClass.id == class_id)) or -1)
    if stored is None:
        raise HTTPException(status_code=404, detail="Final grade not found.")
    if not stored.is_locked:
        raise HTTPException(status_code=409, detail="Final grade is already open.")
    stored.is_locked = False
    stored.reopened_by = user.id
    stored.reopened_at = datetime.now(timezone.utc).replace(tzinfo=None)
    stored.reopen_reason = payload.reason
    db.commit()
    return {"id": stored.id, "student_id": student_id, "is_locked": False,
            "reopen_reason": stored.reopen_reason}
