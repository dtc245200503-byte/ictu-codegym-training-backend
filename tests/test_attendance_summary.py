from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services.attendance_summary import HO_CHI_MINH, summarize_class_attendance


HCM = HO_CHI_MINH
AS_OF = datetime(2026, 10, 20, 12, 0, tzinfo=HCM)


def make_enrollment(
    student_id: int,
    *,
    status: str = "active",
    effective_date: date | None = None,
    name: str | None = None,
    code: str | None = None,
):
    return SimpleNamespace(
        student_id=student_id,
        enrollment_status=status,
        effective_date=effective_date,
        student=SimpleNamespace(
            student_code=code or f"SV{student_id}",
            username=f"user{student_id}",
            full_name=name or f"Student {student_id}",
        ),
    )


def make_session(
    session_id: int,
    *,
    session_date: date = date(2026, 10, 20),
    end_time: time = time(10, 0),
    cancelled: bool = False,
):
    return SimpleNamespace(
        id=session_id,
        session_date=session_date,
        start_time=time(9, 0),
        end_time=end_time,
        is_cancelled=cancelled,
    )


def make_record(session_id: int, student_id: int, status: str):
    return SimpleNamespace(
        session_id=session_id,
        student_id=student_id,
        status=status,
    )


def summarize(enrollments, sessions, records=(), as_of=AS_OF):
    return summarize_class_attendance(enrollments, sessions, records, as_of)


@pytest.mark.parametrize(
    ("status", "expected_rate", "expected_absent"),
    [
        ("present", 100.0, 0),
        ("late", 100.0, 0),
        ("excused_absent", 0.0, 1),
        ("unexcused_absent", 0.0, 1),
    ],
)
def test_each_attendance_status_has_expected_counter_and_rate(
    status, expected_rate, expected_absent
):
    result = summarize(
        [make_enrollment(1)],
        [make_session(10)],
        [make_record(10, 1, status)],
    )
    student = result.students[0]

    assert student.cells[0].eligible is True
    assert student.cells[0].status == status
    assert student.attendance_rate == expected_rate
    assert student.absent_count == expected_absent
    assert student.present_count == (status == "present")
    assert student.late_count == (status == "late")
    assert student.excused_absent_count == (status == "excused_absent")
    assert student.unexcused_absent_count == (status == "unexcused_absent")


def test_present_and_late_are_both_counted_as_attended():
    result = summarize(
        [make_enrollment(1)],
        [make_session(1), make_session(2, session_date=date(2026, 10, 19))],
        [make_record(1, 1, "present"), make_record(2, 1, "late")],
    )

    student = result.students[0]
    assert student.present_count == 1
    assert student.late_count == 1
    assert student.attendance_rate == 100.0


def test_excused_and_unexcused_absences_are_separate_and_combined():
    result = summarize(
        [make_enrollment(1)],
        [
            make_session(1),
            make_session(2, session_date=date(2026, 10, 19)),
        ],
        [
            make_record(1, 1, "excused_absent"),
            make_record(2, 1, "unexcused_absent"),
        ],
    )

    student = result.students[0]
    assert student.excused_absent_count == 1
    assert student.unexcused_absent_count == 1
    assert student.absent_count == 2
    assert student.attendance_rate == 0.0


def test_legacy_absent_is_preserved_and_counted_without_subtype_mapping():
    result = summarize(
        [make_enrollment(1)],
        [make_session(1), make_session(2, session_date=date(2026, 10, 19))],
        [make_record(1, 1, "absent"), make_record(2, 1, "present")],
    )

    student = result.students[0]
    assert [cell.status for cell in student.cells] == ["present", "absent"]
    assert student.legacy_absent_count == 1
    assert student.excused_absent_count == 0
    assert student.unexcused_absent_count == 0
    assert student.absent_count == 1
    assert student.present_count == 1
    assert student.attendance_rate == 50.0


def test_missing_record_is_unrecorded_but_remains_in_rate_denominator():
    result = summarize(
        [make_enrollment(1)],
        [make_session(1), make_session(2, session_date=date(2026, 10, 19))],
        [make_record(1, 1, "present")],
    )

    student = result.students[0]
    assert [cell.status for cell in student.cells] == [None, "present"]
    assert student.unrecorded_count == 1
    assert student.total_sessions == 2
    assert student.absent_count == 0
    assert student.attendance_rate == 50.0


def test_persisted_not_recorded_sentinel_maps_to_unrecorded_matrix_cell():
    result = summarize(
        [make_enrollment(1)],
        [make_session(1)],
        [make_record(1, 1, "not_recorded")],
    )

    student = result.students[0]
    assert student.cells[0].eligible is True
    assert student.cells[0].status is None
    assert student.unrecorded_count == 1
    assert student.total_sessions == 1
    assert student.absent_count == 0
    assert student.attendance_rate == 0.0


def test_cancelled_session_is_omitted_from_matrix_and_denominator():
    result = summarize(
        [make_enrollment(1)],
        [make_session(1), make_session(2, cancelled=True)],
        [make_record(1, 1, "present"), make_record(2, 1, "unexcused_absent")],
    )

    assert [session.id for session in result.sessions] == [1]
    assert len(result.students[0].cells) == 1
    assert result.students[0].total_sessions == 1
    assert result.students[0].attendance_rate == 100.0


def test_future_session_is_omitted():
    result = summarize(
        [make_enrollment(1)],
        [make_session(1, session_date=date(2026, 10, 21))],
        as_of=AS_OF,
    )

    assert result.sessions == ()
    assert result.students[0].total_sessions == 0
    assert result.students[0].attendance_rate is None


def test_session_that_has_not_ended_is_omitted():
    result = summarize(
        [make_enrollment(1)],
        [make_session(1, end_time=time(13, 0))],
        as_of=AS_OF,
    )

    assert result.sessions == ()


def test_naive_database_time_uses_hcm_and_end_boundary_is_strict():
    session = make_session(1, end_time=time(10, 0))
    exact_end = datetime(2026, 10, 20, 10, 0, tzinfo=HCM)
    one_second_after = exact_end + timedelta(seconds=1)

    at_boundary = summarize([make_enrollment(1)], [session], as_of=exact_end)
    after_boundary = summarize([make_enrollment(1)], [session], as_of=one_second_after)
    same_instant_utc = summarize(
        [make_enrollment(1)],
        [session],
        as_of=one_second_after.astimezone(timezone.utc),
    )

    assert at_boundary.sessions == ()
    assert len(after_boundary.sessions) == 1
    assert len(same_instant_utc.sessions) == 1


def test_naive_reference_datetime_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        summarize([make_enrollment(1)], [], as_of=datetime(2026, 10, 20, 12, 0))


def test_active_enrollment_effective_date_excludes_earlier_sessions():
    result = summarize(
        [make_enrollment(1, effective_date=date(2026, 10, 20))],
        [
            make_session(1, session_date=date(2026, 10, 19)),
            make_session(2, session_date=date(2026, 10, 20)),
        ],
        [
            make_record(1, 1, "present"),
            make_record(2, 1, "present"),
        ],
    )

    student = result.students[0]
    assert [cell.eligible for cell in student.cells] == [False, True]
    assert [cell.status for cell in student.cells] == [None, "present"]
    assert student.total_sessions == 1
    assert student.attendance_rate == 100.0


def test_inactive_enrollment_includes_before_effective_date_only():
    result = summarize(
        [
            make_enrollment(
                1,
                status="withdrawn",
                effective_date=date(2026, 10, 20),
            )
        ],
        [
            make_session(1, session_date=date(2026, 10, 19)),
            make_session(2, session_date=date(2026, 10, 20)),
            make_session(3, session_date=date(2026, 10, 21)),
        ],
        [
            make_record(1, 1, "present"),
            make_record(2, 1, "unexcused_absent"),
            make_record(3, 1, "late"),
        ],
        as_of=datetime(2026, 10, 22, 12, 0, tzinfo=HCM),
    )

    student = result.students[0]
    assert [cell.eligible for cell in student.cells] == [True, False, False]
    assert student.total_sessions == 1
    assert student.present_count == 1
    assert student.absent_count == 0
    assert student.attendance_rate == 100.0


def test_class_without_sessions_returns_enrollments_with_null_rate():
    result = summarize([make_enrollment(1)], [])

    assert result.sessions == ()
    assert len(result.students) == 1
    assert result.students[0].cells == ()
    assert result.students[0].total_sessions == 0
    assert result.students[0].unrecorded_count == 0
    assert result.students[0].attendance_rate is None


def test_no_eligible_enrollments_returns_no_students():
    result = summarize([], [make_session(1)])

    assert len(result.sessions) == 1
    assert result.students == ()


def test_no_eligible_sessions_has_null_rate_not_zero_or_one_hundred():
    result = summarize(
        [make_enrollment(1)],
        [make_session(1, cancelled=True)],
    )

    student = result.students[0]
    assert student.total_sessions == 0
    assert student.attendance_rate is None


def test_mixed_states_and_unrecorded_session_produce_expected_totals():
    sessions = [
        make_session(1, session_date=date(2026, 10, 16)),
        make_session(2, session_date=date(2026, 10, 17)),
        make_session(3, session_date=date(2026, 10, 18)),
        make_session(4, session_date=date(2026, 10, 19)),
        make_session(5),
    ]
    records = [
        make_record(1, 1, "present"),
        make_record(2, 1, "late"),
        make_record(3, 1, "excused_absent"),
        make_record(4, 1, "unexcused_absent"),
    ]
    student = summarize([make_enrollment(1)], sessions, records).students[0]

    assert student.present_count == 1
    assert student.late_count == 1
    assert student.excused_absent_count == 1
    assert student.unexcused_absent_count == 1
    assert student.absent_count == 2
    assert student.unrecorded_count == 1
    assert student.total_sessions == 5
    assert student.attendance_rate == 40.0


def test_students_sort_by_rate_then_name_with_null_rates_last():
    enrollments = [
        make_enrollment(1, name="Zoe"),
        make_enrollment(2, name="Amy"),
        make_enrollment(3, name="No Sessions"),
        make_enrollment(4, name="Amy"),
    ]
    sessions = [
        make_session(10, session_date=date(2026, 10, 19)),
        make_session(11, session_date=date(2026, 10, 18)),
    ]
    records = [
        make_record(10, 1, "present"),
        make_record(10, 2, "unexcused_absent"),
        make_record(10, 4, "unexcused_absent"),
        make_record(11, 1, "present"),
        make_record(11, 2, "present"),
        make_record(11, 4, "present"),
    ]

    # Student 3 is active as of today but started after the past sessions.
    enrollments[2].effective_date = date(2026, 10, 20)
    result = summarize(enrollments, sessions, records)

    assert [student.student_id for student in result.students] == [2, 4, 1, 3]
    assert [student.attendance_rate for student in result.students] == [50.0, 50.0, 100.0, None]


def test_unknown_status_on_eligible_record_is_rejected():
    with pytest.raises(ValueError, match="Unsupported attendance status"):
        summarize(
            [make_enrollment(1)],
            [make_session(1)],
            [make_record(1, 1, "unknown")],
        )
