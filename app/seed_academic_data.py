from __future__ import annotations

import secrets
import string
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.attendance import AttendanceRecord, AttendanceSession
from app.models.course import Course
from app.models.role import Role
from app.models.study_program import StudyProgram, program_subjects
from app.models.training_class import ClassEnrollment, TrainingClass
from app.models.user import User


TERM = "HK1-2026-2027"

COURSES = (
    {
        "code": "MAT101",
        "name": "Toán cao cấp",
        "department": "Đại cương",
        "credits": 3,
        "prerequisite": None,
        "description": "Kiến thức toán nền tảng cho các ngành công nghệ.",
    },
    {
        "code": "TRI101",
        "name": "Triết học Mác-Lênin",
        "department": "Lý luận chính trị",
        "credits": 3,
        "prerequisite": None,
        "description": "Học phần lý luận chính trị đại cương.",
    },
    {
        "code": "ENG201",
        "name": "Tiếng Anh chuyên ngành CNTT",
        "department": "Ngoại ngữ",
        "credits": 2,
        "prerequisite": None,
        "description": "Đọc hiểu tài liệu và giao tiếp tiếng Anh chuyên ngành.",
    },
    {
        "code": "INT101",
        "name": "Nhập môn Công nghệ thông tin",
        "department": "Công nghệ thông tin",
        "credits": 3,
        "prerequisite": "MAT101",
        "description": "Tổng quan máy tính, hệ điều hành, mạng và nghề nghiệp CNTT.",
    },
    {
        "code": "PY101",
        "name": "Lập trình Python",
        "department": "Công nghệ thông tin",
        "credits": 3,
        "prerequisite": "INT101",
        "description": "Cú pháp Python, hàm, cấu trúc dữ liệu và lập trình hướng đối tượng.",
    },
    {
        "code": "DB201",
        "name": "Cơ sở dữ liệu",
        "department": "Công nghệ thông tin",
        "credits": 3,
        "prerequisite": "PY101",
        "description": "Mô hình quan hệ, SQL, chuẩn hóa và thiết kế cơ sở dữ liệu.",
    },
    {
        "code": "DSA201",
        "name": "Cấu trúc dữ liệu và Giải thuật",
        "department": "Công nghệ thông tin",
        "credits": 4,
        "prerequisite": "PY101",
        "description": "Cấu trúc dữ liệu, độ phức tạp và các giải thuật nền tảng.",
    },
    {
        "code": "SWE201",
        "name": "Công nghệ phần mềm",
        "department": "Kỹ thuật phần mềm",
        "credits": 3,
        "prerequisite": "DSA201",
        "description": "Quy trình phát triển, phân tích yêu cầu, thiết kế và kiểm thử.",
    },
    {
        "code": "WEB202",
        "name": "Lập trình ứng dụng Web",
        "department": "Kỹ thuật phần mềm",
        "credits": 3,
        "prerequisite": "DB201",
        "description": "Phát triển ứng dụng web phía máy khách và máy chủ.",
    },
    {
        "code": "NET202",
        "name": "Mạng máy tính",
        "department": "Công nghệ thông tin",
        "credits": 3,
        "prerequisite": "INT101",
        "description": "Mô hình OSI/TCP-IP, định tuyến và các dịch vụ mạng.",
    },
)

PROGRAMS = (
    {
        "code": "ICTU-IT",
        "name": "Công nghệ thông tin",
        "total_credits": 126,
        "description": "Chương trình đào tạo trình độ đại học, 8 học kỳ. "
        "Danh mục seed gồm các học phần tiêu biểu, không thay thế toàn bộ đề cương 126 tín chỉ.",
        "courses": ("MAT101", "TRI101", "ENG201", "INT101", "PY101", "DB201", "DSA201", "NET202"),
    },
    {
        "code": "ICTU-SWE",
        "name": "Kỹ thuật phần mềm",
        "total_credits": 126,
        "description": "Chương trình đào tạo trình độ đại học, 8 học kỳ. "
        "Danh mục seed gồm các học phần tiêu biểu, không thay thế toàn bộ đề cương 126 tín chỉ.",
        "courses": ("MAT101", "TRI101", "ENG201", "INT101", "PY101", "DB201", "DSA201", "SWE201", "WEB202"),
    },
)

CLASS_SCHEDULE = (
    ("INT101_01", "INT101", 0, time(8), time(10), "A101"),
    ("SWE201_02", "SWE201", 0, time(13), time(15), "Lab 3"),
    ("PY101_01", "PY101", 1, time(8), time(10), "Lab 3"),
    ("DB201_01", "DB201", 1, time(13), time(15), "B202"),
    ("DSA201_01", "DSA201", 2, time(8), time(10), "A101"),
    ("TRI101_01", "TRI101", 2, time(13), time(15), "B202"),
    ("ENG201_01", "ENG201", 3, time(8), time(10), "B202"),
    ("WEB202_01", "WEB202", 3, time(13), time(15), "Lab 3"),
    ("MAT101_01", "MAT101", 4, time(8), time(10), "A101"),
    ("NET202_01", "NET202", 4, time(13), time(15), "B202"),
)

SAMPLE_STUDENTS = (
    ("demo.student", "demo.student@ictu.edu.vn", "Demo Student", "DEMO0001"),
    ("ictu.student.02", "ictu.student.02@ictu.edu.vn", "Nguyễn Minh Anh", "SV2026002"),
    ("ictu.student.03", "ictu.student.03@ictu.edu.vn", "Trần Quốc Bảo", "SV2026003"),
    ("ictu.student.04", "ictu.student.04@ictu.edu.vn", "Lê Thu Hà", "SV2026004"),
    ("ictu.student.05", "ictu.student.05@ictu.edu.vn", "Phạm Gia Huy", "SV2026005"),
    ("ictu.student.06", "ictu.student.06@ictu.edu.vn", "Vũ Khánh Linh", "SV2026006"),
)

ATTENDANCE_PATTERN = ("present", "late", "present", "absent")


def _new_password() -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%*-_"
    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(20))
        if (
            any(char.islower() for char in password)
            and any(char.isupper() for char in password)
            and any(char.isdigit() for char in password)
            and any(char in "!@#$%*-_" for char in password)
        ):
            return password


def _get_or_create_user(
    db: Session,
    *,
    username: str,
    email: str,
    full_name: str,
    student_code: str | None,
    role: Role,
    password: str,
) -> tuple[User, bool]:
    user = db.scalar(
        select(User).where((User.username == username) | (User.email == email))
    )
    if user is None:
        user = User(
            username=username,
            email=email,
            full_name=full_name,
            student_code=student_code,
            hashed_password=hash_password(password),
            roles=[role],
        )
        db.add(user)
        db.flush()
        return user, True

    if user.username != username or user.email != email:
        raise RuntimeError(
            f"Seed username or email is already used by another account: {username}"
        )
    if student_code and user.student_code not in (None, student_code):
        raise RuntimeError(
            f"Seed student code is already set to a different value for {username}"
        )
    if student_code:
        user.student_code = student_code
    if role not in user.roles:
        user.roles.append(role)
    return user, False


def _upsert_courses(db: Session) -> dict[str, Course]:
    courses = {
        item["code"]: db.scalar(select(Course).where(Course.code == item["code"]))
        for item in COURSES
    }
    for item in COURSES:
        course = courses[item["code"]]
        if course is None:
            course = Course(
                code=item["code"],
                name=item["name"],
                department=item["department"],
                credits=item["credits"],
                description=item["description"],
            )
            db.add(course)
            courses[item["code"]] = course
        else:
            course.name = item["name"]
            course.department = item["department"]
            course.credits = item["credits"]
            course.description = item["description"]
    db.flush()

    for item in COURSES:
        courses[item["code"]].prerequisite = (
            courses[item["prerequisite"]] if item["prerequisite"] else None
        )
    db.flush()
    return courses


def _upsert_programs(
    db: Session, courses: dict[str, Course]
) -> dict[str, StudyProgram]:
    result: dict[str, StudyProgram] = {}
    for item in PROGRAMS:
        program = db.scalar(
            select(StudyProgram).where(StudyProgram.code == item["code"])
        )
        if program is None:
            program = StudyProgram(
                code=item["code"],
                name=item["name"],
                degree_level="Đại học",
                total_credits=item["total_credits"],
                duration_semesters=8,
                description=item["description"],
            )
            db.add(program)
        else:
            program.name = item["name"]
            program.degree_level = "Đại học"
            program.total_credits = item["total_credits"]
            program.duration_semesters = 8
            program.description = item["description"]
        program.courses = [courses[code] for code in item["courses"]]
        db.flush()
        db.execute(
            program_subjects.delete().where(
                program_subjects.c.program_id == program.id
            )
        )
        db.execute(
            program_subjects.insert(),
            [
                {
                    "program_id": program.id,
                    "subject_id": courses[code].id,
                    "order_index": index + 1,
                    "prerequisite_subject_id": None,
                }
                for index, code in enumerate(item["courses"])
            ],
        )
        result[item["code"]] = program
    db.flush()
    return result


def _recent_class_dates(weekday: int, count: int = 4) -> list[date]:
    today = date.today()
    first_offset = (today.weekday() - weekday) % 7
    return [
        today - timedelta(days=first_offset + 7 * index)
        for index in range(count)
    ]


def seed_academic_data(db: Session) -> dict[str, int]:
    roles = {
        role.code: role
        for role in db.scalars(
            select(Role).where(Role.code.in_(("teacher", "ta", "student")))
        ).all()
    }
    missing_roles = {"teacher", "ta", "student"} - roles.keys()
    if missing_roles:
        raise RuntimeError(
            "Missing required roles; run `alembic upgrade head` first: "
            + ", ".join(sorted(missing_roles))
        )

    password = _new_password()
    instructor, created_instructor = _get_or_create_user(
        db,
        username="demo.instructor",
        email="demo.instructor@ictu.edu.vn",
        full_name="Demo Instructor",
        student_code=None,
        role=roles["teacher"],
        password=password,
    )
    assistant, created_assistant = _get_or_create_user(
        db,
        username="demo.ta",
        email="demo.ta@ictu.edu.vn",
        full_name="Demo Teaching Assistant",
        student_code=None,
        role=roles["ta"],
        password=password,
    )
    students: list[User] = []
    new_user_count = int(created_instructor) + int(created_assistant)
    for username, email, full_name, student_code in SAMPLE_STUDENTS:
        student, created = _get_or_create_user(
            db,
            username=username,
            email=email,
            full_name=full_name,
            student_code=student_code,
            role=roles["student"],
            password=password,
        )
        students.append(student)
        new_user_count += int(created)

    courses = _upsert_courses(db)
    programs = _upsert_programs(db, courses)
    classes: dict[str, TrainingClass] = {}
    for code, course_code, weekday, start_time, end_time, room in CLASS_SCHEDULE:
        training_class = db.scalar(
            select(TrainingClass).where(TrainingClass.code == code)
        )
        if training_class is None:
            training_class = TrainingClass(
                code=code,
                name=f"{courses[course_code].name} - Nhóm {code.rsplit('_', 1)[-1]}",
                course_id=courses[course_code].id,
                teacher_id=instructor.id,
                assistant_id=assistant.id,
                room=room,
                term=TERM,
                day_of_week=weekday,
                start_time=start_time,
                end_time=end_time,
                capacity=40,
            )
            db.add(training_class)
        else:
            training_class.name = (
                f"{courses[course_code].name} - Nhóm {code.rsplit('_', 1)[-1]}"
            )
            training_class.course_id = courses[course_code].id
            training_class.teacher_id = instructor.id
            training_class.assistant_id = assistant.id
            training_class.room = room
            training_class.term = TERM
            training_class.day_of_week = weekday
            training_class.start_time = start_time
            training_class.end_time = end_time
            training_class.capacity = max(training_class.capacity, len(students))
        classes[code] = training_class
    db.flush()

    for training_class in classes.values():
        for student in students:
            enrollment = db.scalar(
                select(ClassEnrollment).where(
                    ClassEnrollment.class_id == training_class.id,
                    ClassEnrollment.student_id == student.id,
                )
            )
            if enrollment is None:
                enrollment = ClassEnrollment(
                    class_id=training_class.id,
                    student_id=student.id,
                    tuition_status="paid" if student.id % 2 == 0 else "unpaid",
                )
                db.add(enrollment)
        db.flush()

    record_count = 0
    for class_index, (code, _, weekday, start_time, end_time, _) in enumerate(
        CLASS_SCHEDULE
    ):
        training_class = classes[code]
        dates = _recent_class_dates(weekday)
        for session_index, session_date in enumerate(dates):
            attendance_session = db.scalar(
                select(AttendanceSession).where(
                    AttendanceSession.class_id == training_class.id,
                    AttendanceSession.session_date == session_date,
                )
            )
            if attendance_session is None:
                attendance_session = AttendanceSession(
                    class_id=training_class.id,
                    session_date=session_date,
                    start_time=start_time,
                    end_time=end_time,
                )
                db.add(attendance_session)
                db.flush()
            else:
                attendance_session.start_time = start_time
                attendance_session.end_time = end_time

            for student_index, student in enumerate(students):
                attendance_status = ATTENDANCE_PATTERN[
                    (class_index + session_index + student_index) % len(ATTENDANCE_PATTERN)
                ]
                marked_at = datetime.combine(session_date, start_time)
                record = db.scalar(
                    select(AttendanceRecord).where(
                        AttendanceRecord.session_id == attendance_session.id,
                        AttendanceRecord.student_id == student.id,
                    )
                )
                if record is None:
                    record = AttendanceRecord(
                        session_id=attendance_session.id,
                        student_id=student.id,
                        marked_by=instructor.id,
                        status=attendance_status,
                        marked_at=marked_at,
                    )
                    db.add(record)
                    record_count += 1
                else:
                    record.marked_by = instructor.id
                    record.status = attendance_status
                    record.marked_at = marked_at

                if session_index == 0:
                    enrollment = db.scalar(
                        select(ClassEnrollment).where(
                            ClassEnrollment.class_id == training_class.id,
                            ClassEnrollment.student_id == student.id,
                        )
                    )
                    if enrollment is not None:
                        enrollment.attendance_status = attendance_status
                        enrollment.last_attendance_at = marked_at

    db.commit()
    return {
        "programs": len(programs),
        "courses": len(courses),
        "classes": len(classes),
        "students": len(students),
        "attendance_sessions": len(classes) * 4,
        "new_attendance_records": record_count,
        "new_users": new_user_count,
        "new_user_password": password if new_user_count else "",
    }


def main() -> int:
    with SessionLocal() as db:
        summary = seed_academic_data(db)

    password = summary.pop("new_user_password")
    print("Seeded ICTU undergraduate academic data:")
    for name, value in summary.items():
        print(f"- {name}: {value}")
    print(f"- term: {TERM}")
    if summary["new_users"]:
        print(
            f"- shared password for {summary['new_users']} newly created users: "
            f"{password}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
