from fastapi import HTTPException
from app.models.attendance import StudentClass
from app.models.student_enrollment import StudentEnrollment


def periods_for(db, student_id, class_id):
    return db.query(StudentEnrollment).filter_by(student_id=student_id, class_id=class_id).order_by(
        StudentEnrollment.enrollment_id).populate_existing().with_for_update().all()


def materialize_legacy(db, student_id, class_id):
    periods = periods_for(db, student_id, class_id)
    if periods:
        return periods
    legacy = db.query(StudentClass).filter_by(student_id=student_id, class_id=class_id).populate_existing().with_for_update().first()
    if legacy:
        period = StudentEnrollment(student_id=student_id, class_id=class_id, started_on=legacy.enrolled_date, origin="legacy")
        db.add(period)
        db.flush()
        return [period]
    return []


def active_on(period, day):
    return (period.started_on is None or period.started_on <= day) and (period.ended_on is None or day < period.ended_on)


def enrollment_rows(db, student_id, day):
    periods = db.query(StudentEnrollment).filter_by(student_id=student_id).order_by(StudentEnrollment.enrollment_id.desc()).all()
    known = {period.class_id for period in periods}
    result = []
    for period in periods:
        status = "upcoming" if period.started_on and period.started_on > day else (
            period.end_status if period.ended_on and period.ended_on <= day else "active")
        result.append({"enrollment_id": period.enrollment_id, "class_id": period.class_id,
                       "started_on": period.started_on, "ended_on": period.ended_on, "end_status": period.end_status,
                       "status": status, "can_change": period.ended_on is None})
    for row in db.query(StudentClass).filter_by(student_id=student_id):
        if row.class_id not in known:
            result.append({"enrollment_id": None, "class_id": row.class_id, "started_on": row.enrolled_date,
                           "ended_on": None, "end_status": None,
                           "status": "upcoming" if row.enrolled_date and row.enrolled_date > day else "active",
                           "can_change": True})
    return result


def attendance_membership(db, class_id, day):
    """Return interval-managed IDs, currently active IDs, and incoming transfer IDs."""
    periods = db.query(StudentEnrollment).filter_by(class_id=class_id).all()
    managed = {row.student_id for row in periods}
    active = {row.student_id for row in periods if active_on(row, day)}
    transfers = {row.student_id for row in periods if row.origin == "transfer" and active_on(row, day)}
    return managed, active, transfers


def validate_reenrollment(db, class_id, ids):
    # The preparation screen must not undo a formal defer/withdraw/transfer.
    closed = db.query(StudentEnrollment).filter(StudentEnrollment.class_id == class_id,
        StudentEnrollment.student_id.in_(ids), StudentEnrollment.ended_on.is_not(None)).first()
    if closed:
        raise HTTPException(409, "Có học viên đã có quyết định bảo lưu, chuyển lớp hoặc thôi học tại lớp này. Không thể dùng thêm học viên để thay đổi quyết định.")
