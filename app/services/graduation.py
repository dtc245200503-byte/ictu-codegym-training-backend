from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.academic import CourseFinalGrade
from app.models.attendance import AttendanceRecord, AttendanceSession
from app.models.student_profile import StudentProfile
from app.models.course import Course
from app.models.study_program import StudyProgram, program_subjects
from app.models.training_class import ClassEnrollment, TrainingClass
from app.models.tuition import (
    TuitionDiscount, TuitionInstallment, TuitionPayment,
    TuitionPaymentReversal, TuitionPlan,
)
from app.models.user import User


def evaluate_student_graduation(db: Session, *, program: StudyProgram, student: User) -> dict:
    course_ids = set(db.scalars(select(program_subjects.c.subject_id).where(
        program_subjects.c.program_id == program.id
    )).all())
    enrollments = db.scalars(select(ClassEnrollment).join(
        TrainingClass, ClassEnrollment.class_id == TrainingClass.id
    ).where(
        ClassEnrollment.student_id == student.id,
        TrainingClass.course_id.in_(course_ids),
        ClassEnrollment.enrollment_status.in_(("active", "completed")),
    )).all() if course_ids else []
    enrollment_by_class = {row.class_id: row for row in enrollments}
    class_ids = list(enrollment_by_class)
    reasons: list[str] = []

    passed_course_ids = set()
    if class_ids:
        passed_course_ids = set(db.scalars(select(CourseFinalGrade.course_id).where(
            CourseFinalGrade.student_id == student.id,
            CourseFinalGrade.class_id.in_(class_ids),
            CourseFinalGrade.is_locked.is_(True),
            CourseFinalGrade.result == "pass",
        ).distinct()).all())
    missing_courses = sorted(course_ids - passed_course_ids)
    if missing_courses:
        reasons.append("courses_not_passed")

    sessions = db.scalars(select(AttendanceSession).join(
        TrainingClass, AttendanceSession.class_id == TrainingClass.id
    ).where(
        AttendanceSession.class_id.in_(class_ids or [-1]),
        TrainingClass.course_id.in_(course_ids or [-1]),
        AttendanceSession.is_cancelled.is_(False),
    )).all()
    eligible_sessions = [
        session for session in sessions
        if session.session_date >= (enrollment_by_class[session.class_id].effective_date
            or enrollment_by_class[session.class_id].enrolled_at.date())
    ]
    present_count = 0
    session_ids = [session.id for session in eligible_sessions]
    records = db.scalars(select(AttendanceRecord).where(
        AttendanceRecord.student_id == student.id,
        AttendanceRecord.session_id.in_(session_ids or [-1]),
    )).all()
    record_by_session = {record.session_id: record.status for record in records}
    for session in eligible_sessions:
        if record_by_session.get(session.id) in {"present", "late"}:
            present_count += 1
    attendance_rate = (
        Decimal(present_count) * Decimal("100") / Decimal(len(eligible_sessions))
        if eligible_sessions else None
    )
    threshold = program.graduation_attendance_threshold
    if threshold is None:
        reasons.append("attendance_threshold_not_configured")
    elif attendance_rate is None:
        reasons.append("attendance_data_missing")
    elif attendance_rate <= Decimal(threshold):
        reasons.append("attendance_below_threshold")

    outstanding = Decimal("0")
    if enrollment_by_class:
        plans = db.scalars(select(TuitionPlan).where(
            TuitionPlan.enrollment_id.in_([row.id for row in enrollments])
        )).all()
        for plan in plans:
            discounts = db.scalars(select(TuitionDiscount.amount_vnd).where(
                TuitionDiscount.plan_id == plan.id,
                TuitionDiscount.status == "approved",
            )).all()
            paid = Decimal("0")
            installments = db.scalars(select(TuitionInstallment).where(
                TuitionInstallment.plan_id == plan.id
            )).all()
            for installment in installments:
                payments = db.scalars(select(TuitionPayment).where(
                    TuitionPayment.installment_id == installment.id
                )).all()
                for payment in payments:
                    paid += Decimal(payment.amount_vnd)
                    paid -= Decimal(db.scalar(select(TuitionPaymentReversal.amount_vnd).where(
                        TuitionPaymentReversal.payment_id == payment.id
                    )) or 0)
            outstanding += max(Decimal("0"), Decimal(plan.total_due_vnd)
                               - sum((Decimal(value) for value in discounts), Decimal("0"))
                               - paid)
    if outstanding > 0:
        reasons.append("tuition_debt_outstanding")

    reason_details = []
    if "courses_not_passed" in reasons:
        names = db.scalars(select(Course.name).where(Course.id.in_(missing_courses))).all()
        reason_details.append("Chưa đạt toàn bộ môn trong chương trình: " + ", ".join(names))
    if "attendance_threshold_not_configured" in reasons:
        reason_details.append("Chương trình chưa cấu hình ngưỡng chuyên cần.")
    if "attendance_data_missing" in reasons:
        reason_details.append("Chưa có dữ liệu chuyên cần để xét.")
    if "attendance_below_threshold" in reasons:
        reason_details.append(
            f"Tỷ lệ chuyên cần {attendance_rate.quantize(Decimal('0.01'))}% chưa cao hơn ngưỡng {Decimal(threshold)}%."
        )
    if "tuition_debt_outstanding" in reasons:
        reason_details.append(f"Còn công nợ {outstanding:.0f} VND.")

    profile = db.get(StudentProfile, student.id)
    return {
        "student_id": student.id,
        "student_code": student.student_code,
        "student_name": student.full_name,
        "program_id": program.id,
        "eligible": not reasons,
        "reasons": reasons,
        "reason_details": reason_details,
        "missing_course_ids": missing_courses,
        "attendance_rate_percent": str(attendance_rate.quantize(Decimal("0.01"))) if attendance_rate is not None else None,
        "attendance_threshold_percent": str(threshold) if threshold is not None else None,
        "attendance_sessions": len(eligible_sessions),
        "outstanding_vnd": str(outstanding),
        "graduation_status": profile.graduation_status if profile else "studying",
    }
