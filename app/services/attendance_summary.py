"""Pure aggregation helpers for class attendance summaries."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

AttendanceStatus = Literal[
    "present", "late", "excused_absent", "unexcused_absent"
]
LegacyAttendanceStatus = Literal["absent"]
CellStatus = AttendanceStatus | LegacyAttendanceStatus | None

try:
    HO_CHI_MINH = ZoneInfo("Asia/Ho_Chi_Minh")
except ZoneInfoNotFoundError:
    # Windows Python installations may not include an IANA timezone database.
    # Vietnam uses UTC+07:00 for the contemporary academic schedules in this app.
    HO_CHI_MINH = timezone(timedelta(hours=7), name="Asia/Ho_Chi_Minh")
ATTENDANCE_STATUSES = frozenset(
    {
        "present",
        "late",
        "excused_absent",
        "unexcused_absent",
        "absent",
        "not_recorded",
    }
)


@dataclass(frozen=True)
class AttendanceSessionSummary:
    id: int
    session_date: date
    start_time: time
    end_time: time


@dataclass(frozen=True)
class AttendanceCell:
    session_id: int
    eligible: bool
    status: CellStatus


@dataclass(frozen=True)
class StudentAttendanceSummary:
    student_id: int
    student_code: str | None
    full_name: str
    cells: tuple[AttendanceCell, ...]
    present_count: int
    late_count: int
    excused_absent_count: int
    unexcused_absent_count: int
    legacy_absent_count: int
    unrecorded_count: int
    absent_count: int
    total_sessions: int
    attendance_rate: float | None


@dataclass(frozen=True)
class ClassAttendanceSummary:
    sessions: tuple[AttendanceSessionSummary, ...]
    students: tuple[StudentAttendanceSummary, ...]


def _local_session_end(session_date: date, end_time: time) -> datetime:
    """Interpret the database Date + naive Time as Ho Chi Minh wall time."""
    if end_time.tzinfo is not None:
        raise ValueError("Attendance session end_time must be timezone-naive.")
    return datetime.combine(session_date, end_time).replace(tzinfo=HO_CHI_MINH)


def _enrollment_covers_session(enrollment: object, session_date: date) -> bool:
    """Mirror sprint4._effective_enrollments without importing its DB helper."""
    status = getattr(enrollment, "enrollment_status")
    effective_date = getattr(enrollment, "effective_date")
    if status == "active":
        return effective_date is None or effective_date <= session_date
    return effective_date is not None and session_date < effective_date


def summarize_class_attendance(
    enrollments: Sequence[object],
    sessions: Sequence[object],
    records: Sequence[object],
    as_of: datetime,
) -> ClassAttendanceSummary:
    """Aggregate one class using preloaded enrollments, sessions and records.

    Session dates and times are stored as a SQL ``Date`` and timezone-naive
    ``Time`` and represent local class schedule time. ``as_of`` must be aware;
    it is converted to Asia/Ho_Chi_Minh before comparing with session ends.
    A session is completed only when its end instant is strictly before
    ``as_of``. Cancelled, future and not-yet-ended sessions are omitted.
    """
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware.")
    local_as_of = as_of.astimezone(HO_CHI_MINH)

    eligible_sessions = [
        session
        for session in sessions
        if not session.is_cancelled
        and _local_session_end(session.session_date, session.end_time) < local_as_of
    ]
    eligible_sessions.sort(
        key=lambda session: (session.session_date, session.start_time, session.id)
    )
    session_summaries = tuple(
        AttendanceSessionSummary(
            id=session.id,
            session_date=session.session_date,
            start_time=session.start_time,
            end_time=session.end_time,
        )
        for session in eligible_sessions
    )

    session_ids = {session.id for session in eligible_sessions}
    eligible_enrollments = [
        enrollment
        for enrollment in enrollments
        if _enrollment_covers_session(enrollment, local_as_of.date())
        or any(
            _enrollment_covers_session(enrollment, session.session_date)
            for session in eligible_sessions
        )
    ]
    records_by_pair = {
        (record.session_id, record.student_id): record
        for record in records
        if record.session_id in session_ids
    }

    student_summaries: list[StudentAttendanceSummary] = []
    for enrollment in eligible_enrollments:
        student_id = enrollment.student_id
        cells: list[AttendanceCell] = []
        counts = {
            "present": 0,
            "late": 0,
            "excused_absent": 0,
            "unexcused_absent": 0,
        }
        legacy_absent_count = 0
        unrecorded_count = 0
        total_sessions = 0

        for session in eligible_sessions:
            eligible = _enrollment_covers_session(enrollment, session.session_date)
            record = records_by_pair.get((session.id, student_id)) if eligible else None
            stored_status = record.status if record is not None else None
            if record is not None and stored_status not in ATTENDANCE_STATUSES:
                raise ValueError(f"Unsupported attendance status: {stored_status!r}")
            # The legacy enrollment update flow persists this sentinel in an
            # AttendanceRecord when attendance is cleared. The matrix contract
            # represents that state as an eligible cell with a null status.
            status: CellStatus = (
                None if stored_status == "not_recorded" else stored_status
            )
            cells.append(
                AttendanceCell(session_id=session.id, eligible=eligible, status=status)
            )
            if not eligible:
                continue

            total_sessions += 1
            if status is None:
                unrecorded_count += 1
            elif status == "absent":
                # Preserve the legacy value without guessing its subtype.
                legacy_absent_count += 1
            else:
                counts[status] += 1

        present_count = counts["present"]
        late_count = counts["late"]
        excused_absent_count = counts["excused_absent"]
        unexcused_absent_count = counts["unexcused_absent"]
        attendance_rate = (
            round((present_count + late_count) / total_sessions * 100, 1)
            if total_sessions
            else None
        )
        student = enrollment.student
        student_summaries.append(
            StudentAttendanceSummary(
                student_id=student_id,
                student_code=student.student_code or student.username,
                full_name=student.full_name,
                cells=tuple(cells),
                present_count=present_count,
                late_count=late_count,
                excused_absent_count=excused_absent_count,
                unexcused_absent_count=unexcused_absent_count,
                legacy_absent_count=legacy_absent_count,
                unrecorded_count=unrecorded_count,
                absent_count=(
                    excused_absent_count
                    + unexcused_absent_count
                    + legacy_absent_count
                ),
                total_sessions=total_sessions,
                attendance_rate=attendance_rate,
            )
        )

    student_summaries.sort(
        key=lambda student: (
            student.attendance_rate is None,
            student.attendance_rate if student.attendance_rate is not None else 0,
            student.full_name.casefold(),
            student.student_id,
        )
    )
    return ClassAttendanceSummary(
        sessions=session_summaries,
        students=tuple(student_summaries),
    )
