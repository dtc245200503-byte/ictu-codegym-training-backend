from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import extract, func, select
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, require_roles
from app.db.session import get_db
from app.models.attendance import AttendanceRecord, AttendanceRiskState, AttendanceSession
from app.models.lead import Lead
from app.models.training_class import (
    Assignment, AssignmentGradeVersion, AssignmentSubmission, AssignmentRecipient,
    ClassEnrollment, TrainingClass,
)
from app.models.tuition import (
    TuitionDiscount, TuitionInstallment, TuitionPayment, TuitionPaymentReversal,
    TuitionPlan,
)
from app.models.user import User
from app.services.attendance_summary import HO_CHI_MINH

router = APIRouter(tags=["Dashboards"])


def _admin_overdue_balance(db: Session) -> Decimal:
    # Pull balances with one joined query; allocate approved discounts to later
    # installments first, matching the ledger allocation used by tuition APIs.
    paid = func.coalesce(func.sum(TuitionPayment.amount_vnd), 0)
    reversed_amount = func.coalesce(func.sum(TuitionPaymentReversal.amount_vnd), 0)
    rows = db.execute(select(
        TuitionInstallment.id, TuitionInstallment.plan_id,
        TuitionInstallment.installment_number, TuitionInstallment.amount_due_vnd,
        TuitionInstallment.due_date, paid.label("paid"), reversed_amount.label("reversed"),
    ).outerjoin(TuitionPayment, TuitionPayment.installment_id == TuitionInstallment.id)
      .outerjoin(TuitionPaymentReversal, TuitionPaymentReversal.payment_id == TuitionPayment.id)
      .group_by(TuitionInstallment.id)).all()
    approved_discounts = dict(db.execute(select(
        TuitionDiscount.plan_id, func.sum(TuitionDiscount.amount_vnd)
    ).where(TuitionDiscount.status == "approved").group_by(TuitionDiscount.plan_id)).all())
    by_plan = defaultdict(list)
    for row in rows:
        net_paid = Decimal(str(row.paid or 0)) - Decimal(str(row.reversed or 0))
        gross_open = max(Decimal("0"), Decimal(row.amount_due_vnd) - net_paid)
        by_plan[row.plan_id].append((row, gross_open))
    overdue_total = Decimal("0")
    today = datetime.now(HO_CHI_MINH).date()
    for plan_id, installments in by_plan.items():
        ordered = sorted(installments, key=lambda item: item[0].installment_number)
        discount_total = Decimal(str(approved_discounts.get(plan_id) or 0))
        for index, (row, gross_open) in enumerate(ordered):
            later_open = sum((amount for _, amount in ordered[index + 1:]), Decimal("0"))
            allocatable = max(Decimal("0"), discount_total - later_open)
            allocated = min(gross_open, allocatable)
            remaining = gross_open - allocated
            if row.due_date < today:
                overdue_total += remaining
    return overdue_total


@router.get("/dashboards/management")
def management_dashboard(db: Session = Depends(get_db),
                         user: User = Depends(require_roles("admin", "manager"))):
    today = datetime.now(HO_CHI_MINH).date()
    active_classes = int(db.scalar(select(func.count(TrainingClass.id)).where(
        TrainingClass.status == "active")) or 0)
    active_students = int(db.scalar(select(func.count(func.distinct(ClassEnrollment.student_id))).where(
        ClassEnrollment.enrollment_status == "active")) or 0)
    expected_cells = int(db.scalar(select(func.count()).select_from(ClassEnrollment)
        .join(AttendanceSession, AttendanceSession.class_id == ClassEnrollment.class_id)
        .where(ClassEnrollment.enrollment_status == "active",
            AttendanceSession.is_cancelled.is_(False), AttendanceSession.session_date < today,
            (ClassEnrollment.effective_date.is_(None)
             | (AttendanceSession.session_date >= ClassEnrollment.effective_date)))) or 0)
    attended_cells = int(db.scalar(select(func.count()).select_from(AttendanceRecord)
        .join(AttendanceSession, AttendanceRecord.session_id == AttendanceSession.id)
        .join(ClassEnrollment, (ClassEnrollment.class_id == AttendanceSession.class_id)
             & (ClassEnrollment.student_id == AttendanceRecord.student_id))
        .where(ClassEnrollment.enrollment_status == "active",
            AttendanceSession.is_cancelled.is_(False), AttendanceSession.session_date < today,
            (ClassEnrollment.effective_date.is_(None)
             | (AttendanceSession.session_date >= ClassEnrollment.effective_date)),
            AttendanceRecord.status.in_(("present", "late")))) or 0)
    attendance_average = round(attended_cells * 100 / expected_cells, 2) if expected_cells else None

    months = db.execute(select(
        extract("year", Lead.created_at).label("year"),
        extract("month", Lead.created_at).label("month"),
        func.count(Lead.id),
    ).where(Lead.created_at >= datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=365))
      .group_by("year", "month").order_by("year", "month")).all()
    lead_monthly = [{"year": int(year), "month": int(month), "intake": int(count)}
                    for year, month, count in months]
    funnel = [{"status": str(status), "count": int(count)} for status, count in db.execute(
        select(Lead.status, func.count(Lead.id)).group_by(Lead.status).order_by(Lead.status)
    ).all()]

    risk_states = db.execute(select(AttendanceRiskState, User, TrainingClass).join(
        User, User.id == AttendanceRiskState.student_id
    ).join(TrainingClass, TrainingClass.id == AttendanceRiskState.class_id).where(
        AttendanceRiskState.is_at_risk.is_(True)
    )).all()
    risk = []
    for state, student, training_class in risk_states:
        rate = (Decimal(state.absent_count) * Decimal("100")
            / Decimal(state.completed_session_count) if state.completed_session_count else Decimal("0"))
        threshold = Decimal(state.absence_threshold_percent)
        risk.append({"student_id": student.id, "student_code": student.student_code,
            "student_name": student.full_name, "class_id": training_class.id,
            "class_code": training_class.code, "absence_count": state.absent_count,
            "recorded_sessions": state.completed_session_count,
            "absence_rate_percent": round(float(rate), 2),
            "absence_threshold_percent": round(float(threshold), 2),
            "severity_percent": round(float(rate - threshold), 2)})
    risk.sort(key=lambda item: (-item["severity_percent"], -item["absence_count"],
        item["student_id"], item["class_id"]))
    return {"generated_at": datetime.now(HO_CHI_MINH).isoformat(),
        "cards": {"active_classes": active_classes, "active_students": active_students,
            "attendance_average_percent": attendance_average,
            "overdue_debt_vnd": str(_admin_overdue_balance(db))},
        "lead_intake_by_month": lead_monthly, "lead_funnel": funnel,
        "high_absence_students": risk[:50]}


@router.get("/dashboards/teacher")
def teacher_dashboard(db: Session = Depends(get_db),
                      user: User = Depends(require_roles("teacher"))):
    now_local = datetime.now(HO_CHI_MINH)
    class_ids = list(db.scalars(select(TrainingClass.id).where(
        TrainingClass.teacher_id == user.id
    )).all())
    upcoming = []
    if class_ids:
        sessions = db.scalars(select(AttendanceSession).join(
            TrainingClass, AttendanceSession.class_id == TrainingClass.id
        ).where(AttendanceSession.class_id.in_(class_ids),
            AttendanceSession.is_cancelled.is_(False),
            AttendanceSession.session_date >= now_local.date()).order_by(
                AttendanceSession.session_date, AttendanceSession.start_time).limit(50)).all()
        for session in sessions:
            starts = datetime.combine(session.session_date, session.start_time).replace(tzinfo=HO_CHI_MINH)
            if starts >= now_local:
                training_class = db.get(TrainingClass, session.class_id)
                upcoming.append({"session_id": session.id, "class_id": session.class_id,
                    "class_name": training_class.name if training_class else None,
                    "class_code": training_class.code if training_class else None,
                    "session_date": session.session_date.isoformat(),
                    "start_time": session.start_time.isoformat(),
                    "end_time": session.end_time.isoformat(),
                    "attendance_url": f"/attendance?class_id={session.class_id}&session_id={session.id}"})
                break

    ungraded = 0
    most_absent = []
    if class_ids:
        latest_submission = select(
            AssignmentSubmission.assignment_id.label("assignment_id"),
            AssignmentSubmission.student_id.label("student_id"),
            func.max(AssignmentSubmission.version_number).label("version_number"),
        ).group_by(AssignmentSubmission.assignment_id, AssignmentSubmission.student_id).subquery()
        ungraded = int(db.scalar(select(func.count()).select_from(latest_submission).join(
            Assignment, Assignment.id == latest_submission.c.assignment_id
        ).join(TrainingClass, TrainingClass.id == Assignment.class_id).outerjoin(
            AssignmentSubmission, (AssignmentSubmission.assignment_id == latest_submission.c.assignment_id)
            & (AssignmentSubmission.student_id == latest_submission.c.student_id)
            & (AssignmentSubmission.version_number == latest_submission.c.version_number)
        ).outerjoin(AssignmentGradeVersion, (AssignmentGradeVersion.submission_id == AssignmentSubmission.id)
            & (AssignmentGradeVersion.status == "published")).where(
                TrainingClass.teacher_id == user.id,
                AssignmentGradeVersion.id.is_(None),
            )) or 0)
        absent_rows = db.execute(select(
            AttendanceRecord.student_id, User.student_code, User.full_name,
            TrainingClass.id, TrainingClass.name, func.count(AttendanceRecord.id).label("absence_count"),
        ).join(User, User.id == AttendanceRecord.student_id)
          .join(AttendanceSession, AttendanceSession.id == AttendanceRecord.session_id)
          .join(TrainingClass, TrainingClass.id == AttendanceSession.class_id)
          .where(TrainingClass.teacher_id == user.id,
            AttendanceRecord.status.in_(("absent", "excused_absent", "unexcused_absent")))
          .group_by(AttendanceRecord.student_id, User.student_code, User.full_name,
                    TrainingClass.id, TrainingClass.name)
          .order_by(func.count(AttendanceRecord.id).desc()).limit(10)).all()
        most_absent = [{"student_id": row.student_id, "student_code": row.student_code,
            "student_name": row.full_name, "class_id": row.id, "class_name": row.name,
            "absence_count": int(row.absence_count),
            "attendance_url": f"/attendance?class_id={row.id}&student_id={row.student_id}"}
            for row in absent_rows]
    return {"generated_at": now_local.isoformat(), "next_class": upcoming[0] if upcoming else None,
        "ungraded_submission_count": ungraded, "most_absent_students": most_absent,
        "grading_url": "/assignments"}
