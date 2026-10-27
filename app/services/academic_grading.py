from decimal import Decimal, ROUND_HALF_UP

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.academic import CourseFinalGrade, GradePolicy
from app.models.study_program import StudyProgram, program_subjects
from app.models.training_class import (
    Assignment,
    AssignmentGradeVersion,
    ClassEnrollment,
    TrainingClass,
)


def resolve_grade_policy(
    db: Session, *, class_id: int, program_id: int
) -> tuple[TrainingClass, GradePolicy]:
    training_class = db.get(TrainingClass, class_id)
    if training_class is None:
        raise HTTPException(status_code=404, detail="Class not found.")
    if db.get(StudyProgram, program_id) is None:
        raise HTTPException(status_code=404, detail="Study program not found.")
    in_program = db.scalar(
        select(program_subjects.c.program_id).where(
            program_subjects.c.program_id == program_id,
            program_subjects.c.subject_id == training_class.course_id,
        )
    )
    if in_program is None:
        raise HTTPException(status_code=422, detail="Class course is not in this study program.")
    policy = db.scalar(
        select(GradePolicy)
        .where(
            GradePolicy.class_id == class_id,
            GradePolicy.program_id == program_id,
            GradePolicy.course_id == training_class.course_id,
        )
        .order_by(GradePolicy.version.desc())
        .limit(1)
    )
    if policy is None:
        policy = db.scalar(
            select(GradePolicy)
            .where(
                GradePolicy.class_id.is_(None),
                GradePolicy.program_id == program_id,
                GradePolicy.course_id == training_class.course_id,
            )
            .order_by(GradePolicy.version.desc())
            .limit(1)
        )
    if policy is None:
        raise HTTPException(status_code=404, detail="No grade policy is configured for this course.")
    return training_class, policy


def calculate_class_final_grades(
    db: Session,
    *,
    training_class: TrainingClass,
    policy: GradePolicy,
    student_ids: list[int] | None = None,
) -> list[dict]:
    components = policy.components
    assignments = db.scalars(
        select(Assignment).where(
            Assignment.class_id == training_class.id,
            Assignment.grade_component_key.is_not(None),
            Assignment.status == "published",
        )
    ).all()
    assignments_by_component: dict[str, list[Assignment]] = {}
    for assignment in assignments:
        assignments_by_component.setdefault(assignment.grade_component_key, []).append(assignment)

    enrollment_query = select(ClassEnrollment).where(
        ClassEnrollment.class_id == training_class.id,
        ClassEnrollment.enrollment_status == "active",
    )
    if student_ids is not None:
        enrollment_query = enrollment_query.where(ClassEnrollment.student_id.in_(student_ids or [-1]))
    enrollments = db.scalars(
        enrollment_query
        .order_by(ClassEnrollment.student_id)
    ).all()
    result_rows = []
    for enrollment in enrollments:
        scores_by_assignment: dict[int, Decimal] = {}
        for assignment in assignments:
            if assignment.max_score is None or assignment.max_score <= 0:
                continue
            grade = db.scalar(
                select(AssignmentGradeVersion)
                .where(
                    AssignmentGradeVersion.assignment_id == assignment.id,
                    AssignmentGradeVersion.student_id == enrollment.student_id,
                    AssignmentGradeVersion.status == "published",
                )
                .order_by(AssignmentGradeVersion.version_number.desc())
                .limit(1)
            )
            if grade is not None:
                percent = (Decimal(grade.score) * Decimal("100") / Decimal(assignment.max_score))
                scores_by_assignment[assignment.id] = min(Decimal("100"), percent)

        weighted_total = Decimal("0")
        missing: list[str] = []
        breakdown_components = []
        critical_component_score: Decimal | None = None
        for component in components:
            component_assignments = assignments_by_component.get(component["key"], [])
            component_scores = [
                scores_by_assignment[item.id]
                for item in component_assignments
                if item.id in scores_by_assignment
            ]
            score = (
                sum(component_scores, Decimal("0")) / Decimal(len(component_scores))
                if component_scores
                else None
            )
            if score is None and (
                component.get("required", True)
                or component["key"] == policy.critical_component_key
            ):
                missing.append(component["key"])
            elif score is not None:
                weighted_total += score * Decimal(component["weight"]) / Decimal("100")
            if component["key"] == policy.critical_component_key:
                critical_component_score = score
            breakdown_components.append({
                "key": component["key"],
                "name": component["name"],
                "weight": str(component["weight"]),
                "score_percent": str(score.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) if score is not None else None,
                "assignment_ids": [item.id for item in component_assignments],
                "graded_assignment_count": len(component_scores),
                "required": component.get("required", True),
            })
        weighted_total = weighted_total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if missing:
            result = "incomplete"
        elif (
            policy.critical_score is not None
            and critical_component_score is not None
            and critical_component_score < Decimal(policy.critical_score)
        ) or weighted_total < Decimal(policy.pass_score):
            result = "fail"
        else:
            result = "pass"
        result_rows.append({
            "class_id": training_class.id,
            "course_id": training_class.course_id,
            "student_id": enrollment.student_id,
            "policy_id": policy.id,
            "score": str(weighted_total),
            "result": result,
            "missing_components": missing,
            "critical_component_score": (
                str(critical_component_score.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                if critical_component_score is not None else None
            ),
            "breakdown": {
                "components": breakdown_components,
                "weighted_score": str(weighted_total),
                "pass_score": str(policy.pass_score),
                "critical_component_key": policy.critical_component_key,
                "critical_score": str(policy.critical_score) if policy.critical_score is not None else None,
                "missing_components": missing,
            },
        })
    return result_rows


def latest_final_grade(db: Session, *, class_id: int, student_id: int, course_id: int):
    return db.scalar(
        select(CourseFinalGrade)
        .where(
            CourseFinalGrade.class_id == class_id,
            CourseFinalGrade.student_id == student_id,
            CourseFinalGrade.course_id == course_id,
        )
        .order_by(CourseFinalGrade.version.desc())
        .limit(1)
    )


def next_final_grade_version(db: Session, *, class_id: int, student_id: int, course_id: int) -> int:
    return int(db.scalar(
        select(func.max(CourseFinalGrade.version)).where(
            CourseFinalGrade.class_id == class_id,
            CourseFinalGrade.student_id == student_id,
            CourseFinalGrade.course_id == course_id,
        )
    ) or 0) + 1
