from io import BytesIO
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from openpyxl import Workbook, load_workbook
from sqlalchemy import false, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from app.api.dependencies import get_current_user, require_roles
from app.core.config import settings
from app.core.security import DEFAULT_ACCOUNT_PASSWORD, hash_password, verify_password
from app.db.session import get_db
from app.models.audit_log import AuditLog
from app.models.attendance import (
    AttendanceChangeLog,
    AttendanceRecord,
    AttendanceSession,
)
from app.models.auth_session import AuthSession
from app.models.classroom import Classroom
from app.models.course import Course
from app.models.course_lesson import CourseLesson
from app.models.lead import Lead
from app.models.notification import Notification
from app.models.role import Role
from app.models.study_program import StudyProgram, program_subjects
from app.models.student_profile import StudentProfile
from app.models.training_class import Assignment, ClassEnrollment, TrainingClass
from app.models.user import User
from app.schemas.management import (
    AccountCreate,
    AccountStatusRequest,
    AccountUpdate,
    AssignmentCreate,
    ClassroomCreate,
    ClassroomUpdate,
    ChangePasswordRequest,
    CourseCreate,
    CourseLessonCreate,
    CourseLessonsClone,
    CourseLessonUpdate,
    CourseUpdate,
    EnrollmentRequest,
    EnrollmentStatusUpdate,
    RoleAssignmentRequest,
    StudyProgramCoursesUpdate,
    StudyProgramSubjectCreate,
    StudyProgramSubjectsUpdate,
    StudentProfileUpdate,
    TrainingClassCreate,
    TrainingClassUpdate,
)
from app.services.email_service import send_account_credentials_email

router = APIRouter(tags=["Training management"])
ADMIN_ROLES = ("admin", "manager")
ALL_ROLES = (
    "admin",
    "manager",
    "admissions",
    "accountant",
    "teacher",
    "ta",
    "student",
    "guest",
)
ROLE_NAMES = {
    "admin": "Admin",
    "manager": "Training Manager",
    "admissions": "Admissions",
    "accountant": "Accountant",
    "teacher": "Instructor",
    "ta": "TA",
    "student": "Student",
    "guest": "Guest",
}
WEEKDAYS = ("Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật")


def _user_payload(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "student_code": user.student_code,
        "email": user.email,
        "full_name": user.full_name,
        "phone": user.phone,
        "birth_date": user.birth_date.isoformat() if user.birth_date else None,
        "address": user.address,
        "roles": [role.code for role in user.roles],
        "is_active": user.is_active,
        "is_locked": user.is_locked,
        "must_change_password": user.must_change_password,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


def _course_payload(course: Course) -> dict:
    return {
        "id": course.id,
        "code": course.code,
        "name": course.name,
        "department": course.department,
        "credits": course.credits,
        "session_count": course.session_count,
        "description": course.description,
        "prerequisite_id": course.prerequisite_id,
        "prerequisite_name": (
            course.prerequisite.name if course.prerequisite else None
        ),
        "is_active": course.is_active,
    }


def _class_payload(training_class: TrainingClass) -> dict:
    return {
        "id": training_class.id,
        "code": training_class.code,
        "name": training_class.name,
        "course_id": training_class.course_id,
        "course_code": training_class.course.code,
        "course_name": training_class.course.name,
        "teacher_id": training_class.teacher_id,
        "teacher_name": training_class.teacher.full_name,
        "assistant_id": training_class.assistant_id,
        "assistant_name": (
            training_class.assistant.full_name if training_class.assistant else None
        ),
        "room": training_class.room,
        "term": training_class.term,
        "day_of_week": training_class.day_of_week,
        "weekday": WEEKDAYS[training_class.day_of_week],
        "start_time": training_class.start_time.strftime("%H:%M"),
        "end_time": training_class.end_time.strftime("%H:%M"),
        "capacity": training_class.capacity,
        "student_count": len(training_class.enrollments),
        "status": training_class.status,
        "assistants": [
            {"id": assistant.id, "full_name": assistant.full_name}
            for assistant in training_class.assistants
        ],
    }


def _record_audit(
    db: Session,
    actor: User,
    action: str,
    target_user_id: int | None = None,
    details: str | None = None,
) -> None:
    db.add(
        AuditLog(
            actor_id=actor.id,
            target_user_id=target_user_id,
            action=action,
            details=details,
        )
    )


@router.post("/auth/change-password")
def change_password(
    request: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if not verify_password(request.current_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Mật khẩu hiện tại không chính xác.",
        )
    if verify_password(request.new_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Mật khẩu mới phải khác mật khẩu hiện tại.",
        )

    now = datetime.now(timezone.utc)
    current_user.hashed_password = hash_password(request.new_password)
    current_user.must_change_password = False
    db.query(AuthSession).filter(
        AuthSession.user_id == current_user.id,
        AuthSession.revoked_at.is_(None),
    ).update({AuthSession.revoked_at: now}, synchronize_session=False)
    db.commit()
    return {"message": "Đổi mật khẩu thành công. Vui lòng đăng nhập lại."}


@router.get("/dashboard/summary")
def dashboard_summary(
    current_user: User = Depends(require_roles(*ALL_ROLES)),
    db: Session = Depends(get_db),
) -> dict:
    roles = {role.code for role in current_user.roles}
    class_query = select(func.count(TrainingClass.id))
    if roles.intersection(("teacher", "ta")) and not roles.intersection(ADMIN_ROLES):
        class_query = class_query.where(
            or_(
                TrainingClass.teacher_id == current_user.id,
                TrainingClass.assistant_id == current_user.id,
                TrainingClass.assistants.any(User.id == current_user.id),
            )
        )
    elif "student" in roles and not roles.intersection(ADMIN_ROLES + ("teacher",)):
        class_query = class_query.join(ClassEnrollment).where(
            ClassEnrollment.student_id == current_user.id
        )
    elif "guest" in roles and not roles.intersection(ADMIN_ROLES):
        class_query = class_query.where(false())

    return {
        "students": db.scalar(
            select(func.count(func.distinct(User.id)))
            .join(User.roles)
            .where(Role.code == "student", User.is_active.is_(True))
        )
        or 0,
        "teachers": db.scalar(
            select(func.count(func.distinct(User.id)))
            .join(User.roles)
            .where(Role.code == "teacher", User.is_active.is_(True))
        )
        or 0,
        "courses": db.scalar(
            select(func.count(Course.id)).where(Course.is_active.is_(True))
        )
        or 0,
        "classes": db.scalar(class_query) or 0,
    }


@router.get("/accounts")
def list_accounts(
    q: str = Query(default="", max_length=150),
    role: str | None = Query(
        default=None,
        pattern="^(admin|manager|admissions|accountant|teacher|ta|student|guest)$",
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    current_user: User = Depends(require_roles(*ADMIN_ROLES, "admissions")),
    db: Session = Depends(get_db),
) -> dict:
    if "admissions" in {role.code for role in current_user.roles} and role != "student":
        raise HTTPException(
            status_code=403,
            detail="Admissions chỉ được tra cứu danh sách học viên.",
        )
    query = select(User).options(selectinload(User.roles))
    if role:
        query = query.join(User.roles).where(Role.code == role)
    if q.strip():
        search = f"%{q.strip()}%"
        query = query.where(
            or_(
                User.full_name.ilike(search),
                User.email.ilike(search),
                User.username.ilike(search),
                User.student_code.ilike(search),
            )
        )
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    users = db.scalars(
        query.order_by(User.created_at.desc(), User.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [_user_payload(user) for user in users],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size,
    }


@router.get("/students")
def list_student_profiles(
    q: str = Query(default="", max_length=150),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    _staff: User = Depends(require_roles("admin", "manager", "admissions")),
    db: Session = Depends(get_db),
) -> dict:
    query = (
        select(User, StudentProfile)
        .join(User.roles)
        .outerjoin(StudentProfile, StudentProfile.user_id == User.id)
        .where(Role.code == "student")
    )
    if q.strip():
        search = f"%{q.strip()}%"
        query = query.where(
            or_(
                User.full_name.ilike(search),
                User.email.ilike(search),
                User.username.ilike(search),
                User.student_code.ilike(search),
                StudentProfile.study_program.ilike(search),
            )
        )
    total = db.scalar(
        select(func.count()).select_from(query.order_by(None).subquery())
    ) or 0
    rows = db.execute(
        query.order_by(User.created_at.desc(), User.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    items = []
    for student, profile in rows:
        items.append(
            {
                **_user_payload(student),
                "study_program": profile.study_program if profile else None,
                "lead_id": profile.lead_id if profile else None,
                "profile_created_at": (
                    profile.created_at.isoformat() if profile else None
                ),
            }
        )
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size,
    }


@router.patch("/students/{student_id}")
def update_student_profile(
    student_id: int,
    request: StudentProfileUpdate,
    _staff: User = Depends(require_roles("admin", "manager", "admissions")),
    db: Session = Depends(get_db),
) -> dict:
    student = db.scalar(
        select(User).options(selectinload(User.roles)).where(User.id == student_id)
    )
    if student is None or "student" not in {role.code for role in student.roles}:
        raise HTTPException(status_code=404, detail="Không tìm thấy hồ sơ học viên.")
    student.full_name = request.full_name.strip()
    student.phone = request.phone.strip() if request.phone else None
    student.birth_date = request.birth_date
    student.address = request.address.strip() if request.address else None
    profile = db.get(StudentProfile, student.id)
    if profile is None:
        profile = StudentProfile(user_id=student.id)
        db.add(profile)
    profile.study_program = (
        request.study_program.strip() if request.study_program else None
    )
    db.commit()
    db.refresh(student)
    return {
        **_user_payload(student),
        "study_program": profile.study_program,
        "lead_id": profile.lead_id,
        "profile_created_at": profile.created_at.isoformat(),
    }


def _classroom_payload(classroom: Classroom) -> dict:
    return {
        "id": classroom.id,
        "code": classroom.code,
        "capacity": classroom.capacity,
        "online_url": classroom.online_url,
    }


@router.get("/rooms")
def list_classrooms(
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    return [
        _classroom_payload(room)
        for room in db.scalars(select(Classroom).order_by(Classroom.code)).all()
    ]


@router.post("/rooms", status_code=status.HTTP_201_CREATED)
def create_classroom(
    request: ClassroomCreate,
    _admin: User = Depends(require_roles(*ADMIN_ROLES)),
    db: Session = Depends(get_db),
) -> dict:
    code = request.code.strip()
    if db.scalar(select(Classroom.id).where(Classroom.code == code)):
        raise HTTPException(status_code=409, detail="Mã phòng đã tồn tại.")
    classroom = Classroom(
        code=code,
        capacity=request.capacity,
        online_url=request.online_url.strip() if request.online_url else None,
    )
    db.add(classroom)
    db.commit()
    db.refresh(classroom)
    return _classroom_payload(classroom)


@router.patch("/rooms/{room_id}")
def update_classroom(
    room_id: int,
    request: ClassroomUpdate,
    _admin: User = Depends(require_roles(*ADMIN_ROLES)),
    db: Session = Depends(get_db),
) -> dict:
    classroom = db.get(Classroom, room_id)
    if classroom is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy phòng học.")
    updates = request.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="Không có dữ liệu cần cập nhật.")
    if "code" in updates and updates["code"] != classroom.code and db.scalar(
        select(Classroom.id).where(Classroom.code == updates["code"])
    ):
        raise HTTPException(status_code=409, detail="Mã phòng đã tồn tại.")
    next_capacity = updates.get("capacity", classroom.capacity)
    oversized_class = db.scalar(
        select(TrainingClass.id).where(
            TrainingClass.room == classroom.code,
            TrainingClass.capacity > next_capacity,
        )
    )
    if oversized_class:
        raise HTTPException(
            status_code=409,
            detail="Sức chứa phòng không thể nhỏ hơn sức chứa lớp học phần đang sử dụng.",
        )
    old_code = classroom.code
    for field, value in updates.items():
        setattr(classroom, field, value.strip() if field in {"code", "online_url"} and value else value)
    if classroom.code != old_code:
        db.query(TrainingClass).filter(TrainingClass.room == old_code).update(
            {TrainingClass.room: classroom.code}, synchronize_session=False
        )
    db.commit()
    db.refresh(classroom)
    return _classroom_payload(classroom)


@router.delete("/rooms/{room_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_classroom(
    room_id: int,
    _admin: User = Depends(require_roles(*ADMIN_ROLES)),
    db: Session = Depends(get_db),
) -> None:
    classroom = db.get(Classroom, room_id)
    if classroom is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy phòng học.")
    if db.scalar(select(TrainingClass.id).where(TrainingClass.room == classroom.code)):
        raise HTTPException(
            status_code=409, detail="Không thể xóa phòng đang được lớp học sử dụng."
        )
    db.delete(classroom)
    db.commit()


@router.post("/accounts", status_code=status.HTTP_201_CREATED)
async def create_account(
    request: AccountCreate,
    _admin: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> dict:
    email = str(request.email).lower()
    if db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(status_code=409, detail="Email đã được sử dụng.")
    if db.scalar(select(User.id).where(User.username == request.username)):
        raise HTTPException(status_code=409, detail="Mã định danh đã được sử dụng.")
    if request.student_code and db.scalar(
        select(User.id).where(User.student_code == request.student_code)
    ):
        raise HTTPException(status_code=409, detail="Mã học viên đã được sử dụng.")

    selected_codes = request.selected_roles()
    assigned_roles = db.scalars(select(Role).where(Role.code.in_(selected_codes))).all()
    if len(assigned_roles) != len(selected_codes):
        raise HTTPException(
            status_code=503,
            detail="Danh mục vai trò chưa được khởi tạo. Chạy migration hệ thống trước.",
        )

    temporary_password = request.password
    mail_configured = all(
        value.strip()
        for value in (
            settings.MAIL_USERNAME,
            settings.MAIL_PASSWORD,
            settings.MAIL_FROM,
            settings.MAIL_SERVER,
        )
    )
    user = User(
        username=request.username.strip(),
        email=email,
        full_name=request.full_name.strip(),
        student_code=request.student_code,
        phone=request.phone,
        hashed_password=hash_password(temporary_password),
        must_change_password=True,
        roles=assigned_roles,
    )
    db.add(user)
    try:
        db.flush()
        _record_audit(
            db,
            _admin,
            "account.created",
            target_user_id=user.id,
            details=f"roles={','.join(selected_codes)}",
        )
        if mail_configured:
            await send_account_credentials_email(
                email,
                user.full_name,
                user.username,
                temporary_password,
            )
        db.commit()
        db.refresh(user)
    except Exception as error:
        db.rollback()
        if mail_configured:
            raise HTTPException(
                status_code=502,
                detail="Không thể gửi thông tin tài khoản qua email. Tài khoản chưa được tạo.",
            ) from error
        if isinstance(error, IntegrityError):
            raise HTTPException(
                status_code=409,
                detail="Email hoặc mã định danh đã được sử dụng.",
            ) from error
        raise

    result = _user_payload(user)
    result["credential_delivery"] = "email" if mail_configured else "manual"
    if not mail_configured:
        result["temporary_password"] = temporary_password
    return result


@router.get("/accounts/template")
def account_import_template(
    _admin: User = Depends(require_roles("admin")),
) -> Response:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Accounts"
    sheet.append(
        [
            "username",
            "email",
            "full_name",
            "role",
            "student_code",
            "phone",
            "password",
        ]
    )
    sheet.append(
        [
            "sv0001",
            "student@example.edu.vn",
            "Nguyễn Văn A",
            "student",
            "SV0001",
            "",
            DEFAULT_ACCOUNT_PASSWORD,
        ]
    )
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = "A1:G2"
    output = BytesIO()
    workbook.save(output)
    return Response(
        content=output.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": 'attachment; filename="ictu-account-template.xlsx"'
        },
    )


@router.post("/accounts/import")
async def import_accounts(
    request: Request,
    preview: bool = Query(default=False),
    admin: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> dict:
    workbook_bytes = await request.body()
    if not workbook_bytes or len(workbook_bytes) > 10 * 1024 * 1024:
        raise HTTPException(
            status_code=413, detail="File Excel trống hoặc vượt quá 10 MB."
        )
    try:
        workbook = load_workbook(BytesIO(workbook_bytes), read_only=True, data_only=True)
    except Exception as error:
        raise HTTPException(
            status_code=400, detail="File không đúng định dạng Excel .xlsx."
        ) from error

    sheet = workbook.active
    headers = [
        str(value).strip().lower() if value is not None else ""
        for value in next(sheet.iter_rows(min_row=1, max_row=1, values_only=True), ())
    ]
    required_headers = {"username", "email", "full_name", "role"}
    if not required_headers.issubset(headers):
        workbook.close()
        missing = ", ".join(sorted(required_headers - set(headers)))
        raise HTTPException(
            status_code=422, detail=f"Thiếu cột bắt buộc trong file: {missing}."
        )

    validated: list[tuple[int, dict[str, str]]] = []
    row_errors: list[dict] = []
    preview_rows: list[dict] = []
    file_emails: set[str] = set()
    file_usernames: set[str] = set()
    file_student_codes: set[str] = set()
    for row_number, values in enumerate(
        sheet.iter_rows(min_row=2, values_only=True), start=2
    ):
        if not any(value is not None and str(value).strip() for value in values):
            continue
        row = {
            key: str(values[index]).strip() if index < len(values) and values[index] is not None else ""
            for index, key in enumerate(headers)
        }
        errors: list[str] = []
        if len(row.get("username", "")) < 3:
            errors.append("username phải có ít nhất 3 ký tự")
        if "@" not in row.get("email", "") or "." not in row.get("email", "").split("@")[-1]:
            errors.append("email không hợp lệ")
        if not row.get("full_name"):
            errors.append("full_name không được để trống")
        if row.get("role") not in ROLE_NAMES:
            errors.append(
                "role phải là admin, manager, admissions, accountant, teacher, ta, student hoặc guest"
            )

        email = row.get("email", "").lower()
        username = row.get("username", "")
        student_code = row.get("student_code", "")
        if email in file_emails or db.scalar(select(User.id).where(User.email == email)):
            errors.append("email đã tồn tại")
        if username in file_usernames or db.scalar(
            select(User.id).where(User.username == username)
        ):
            errors.append("username đã tồn tại")
        if student_code and (
            student_code in file_student_codes
            or db.scalar(select(User.id).where(User.student_code == student_code))
        ):
            errors.append("student_code đã tồn tại")

        file_emails.add(email)
        file_usernames.add(username)
        if student_code:
            file_student_codes.add(student_code)
        if errors:
            row_errors.append({"row": row_number, "errors": errors})
        else:
            row["email"] = email
            validated.append((row_number, row))
        preview_rows.append(
            {
                "row": row_number,
                "username": username,
                "email": email,
                "full_name": row.get("full_name", ""),
                "valid": not errors,
                "errors": errors,
            }
        )
    workbook.close()

    if preview:
        return {
            "preview": True,
            "total_rows": len(preview_rows),
            "valid_count": len(validated),
            "invalid_count": len(row_errors),
            "rows": preview_rows,
        }
    if not validated:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Không có dòng hợp lệ để nhập.",
                "rows": row_errors,
            },
        )

    roles = {
        role.code: role
        for role in db.scalars(select(Role).where(Role.code.in_(ROLE_NAMES))).all()
    }
    if len(roles) != len(ROLE_NAMES):
        raise HTTPException(
            status_code=503,
            detail="Danh mục vai trò chưa được khởi tạo. Chạy migration hệ thống trước.",
        )

    created: list[tuple[User, str]] = []
    for _, row in validated:
        password = row.get("password") or DEFAULT_ACCOUNT_PASSWORD
        user = User(
            username=row["username"],
            email=row["email"],
            full_name=row["full_name"],
            student_code=row.get("student_code") or None,
            phone=row.get("phone") or None,
            hashed_password=hash_password(password),
            must_change_password=True,
            roles=[roles[row["role"]]],
        )
        db.add(user)
        created.append((user, password))
    try:
        db.flush()
        for user, _ in created:
            _record_audit(
                db,
                admin,
                "account.imported",
                target_user_id=user.id,
                details=f"role={user.roles[0].code}",
            )
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Dữ liệu tài khoản bị trùng trong lúc nhập; chưa có tài khoản nào được tạo.",
        ) from error

    mail_configured = all(
        value.strip()
        for value in (
            settings.MAIL_USERNAME,
            settings.MAIL_PASSWORD,
            settings.MAIL_FROM,
            settings.MAIL_SERVER,
        )
    )
    results = []
    for user, password in created:
        delivery = "manual"
        if mail_configured:
            try:
                await send_account_credentials_email(
                    user.email, user.full_name, user.username, password
                )
                delivery = "email"
            except Exception:
                delivery = "failed"
        result = {"id": user.id, "email": user.email, "credential_delivery": delivery}
        if delivery != "email":
            result["temporary_password"] = password
        results.append(result)
    return {
        "created_count": len(results),
        "skipped_count": len(row_errors),
        "total_rows": len(preview_rows),
        "row_errors": row_errors,
        "accounts": results,
    }


@router.patch("/accounts/{user_id}")
def update_account(
    user_id: int,
    request: AccountUpdate,
    admin: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> dict:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản.")
    updates = request.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="Không có dữ liệu cần cập nhật.")
    for field, value in updates.items():
        setattr(user, field, value)
    _record_audit(
        db,
        admin,
        "account.updated",
        target_user_id=user.id,
        details=",".join(sorted(updates)),
    )
    db.commit()
    db.refresh(user)
    return _user_payload(user)


@router.post("/accounts/{user_id}/lock")
def lock_account(
    user_id: int,
    request: AccountStatusRequest,
    admin: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> dict:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản.")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="Không thể khóa tài khoản đang sử dụng.")
    user.is_locked = True
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db.query(AuthSession).filter(
        AuthSession.user_id == user.id,
        AuthSession.revoked_at.is_(None),
    ).update({AuthSession.revoked_at: now}, synchronize_session=False)
    handover_classes = db.scalars(
        select(TrainingClass.code).where(
            or_(
                TrainingClass.teacher_id == user.id,
                TrainingClass.assistant_id == user.id,
                TrainingClass.assistants.any(User.id == user.id),
            )
        )
    ).all()
    _record_audit(
        db,
        admin,
        "account.locked",
        target_user_id=user.id,
        details=(
            f"{request.reason.strip()} | "
            f"handover_classes={','.join(handover_classes) or 'none'}"
        ),
    )
    db.commit()
    return {
        "id": user.id,
        "is_locked": user.is_locked,
        "handover_classes": handover_classes,
    }


@router.post("/accounts/{user_id}/unlock")
def unlock_account(
    user_id: int,
    request: AccountStatusRequest,
    admin: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> dict:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản.")
    user.is_locked = False
    _record_audit(
        db,
        admin,
        "account.unlocked",
        target_user_id=user.id,
        details=request.reason.strip(),
    )
    db.commit()
    return {"id": user.id, "is_locked": user.is_locked}


@router.put("/accounts/{user_id}/role")
def assign_account_role(
    user_id: int,
    request: RoleAssignmentRequest,
    admin: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> dict:
    user = db.scalar(
        select(User).options(selectinload(User.roles)).where(User.id == user_id)
    )
    if user is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản.")
    selected_codes = request.selected_roles()
    roles = db.scalars(select(Role).where(Role.code.in_(selected_codes))).all()
    roles_by_code = {role.code: role for role in roles}
    if len(roles_by_code) != len(selected_codes):
        raise HTTPException(status_code=422, detail="Có vai trò không tồn tại.")
    if user.id == admin.id and "admin" not in selected_codes:
        raise HTTPException(status_code=400, detail="Không thể gỡ quyền quản trị của chính mình.")
    user.roles = [roles_by_code[code] for code in selected_codes]
    _record_audit(
        db, admin, "account.role_changed", target_user_id=user.id,
        details=",".join(selected_codes),
    )
    db.commit()
    return _user_payload(user)


@router.get("/roles")
def list_roles(
    _admin: User = Depends(require_roles("admin")),
) -> list[dict]:
    return [{"code": code, "name": name} for code, name in ROLE_NAMES.items()]


@router.get("/courses")
def list_courses(
    q: str = Query(default="", max_length=150),
    department: str | None = Query(default=None, max_length=100),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    query = select(Course).options(joinedload(Course.prerequisite))
    if q.strip():
        search = f"%{q.strip()}%"
        query = query.where(or_(Course.code.ilike(search), Course.name.ilike(search)))
    if department:
        query = query.where(Course.department == department)
    return [
        _course_payload(course)
        for course in db.scalars(query.order_by(Course.department, Course.code)).all()
    ]


def _course_lesson_payload(lesson: CourseLesson) -> dict:
    return {
        "id": lesson.id,
        "course_id": lesson.course_id,
        "sequence": lesson.sequence,
        "title": lesson.title,
        "objectives": lesson.objectives,
        "description": lesson.description,
    }


@router.get("/courses/{course_id}/lessons")
def list_course_lessons(
    course_id: int,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    if db.get(Course, course_id) is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy môn học.")
    lessons = db.scalars(
        select(CourseLesson)
        .where(CourseLesson.course_id == course_id)
        .order_by(CourseLesson.sequence, CourseLesson.id)
    ).all()
    return [_course_lesson_payload(lesson) for lesson in lessons]


@router.post("/courses/{course_id}/lessons", status_code=status.HTTP_201_CREATED)
def create_course_lesson(
    course_id: int,
    request: CourseLessonCreate,
    _admin: User = Depends(require_roles(*ADMIN_ROLES)),
    db: Session = Depends(get_db),
) -> dict:
    course = db.get(Course, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy môn học.")
    if request.sequence > course.session_count:
        raise HTTPException(
            status_code=422, detail="Số buổi vượt quá số buổi khai báo của môn học."
        )
    if db.scalar(
        select(CourseLesson.id).where(
            CourseLesson.course_id == course_id,
            CourseLesson.sequence == request.sequence,
        )
    ):
        raise HTTPException(status_code=409, detail="Thứ tự buổi học đã được sử dụng.")
    lesson = CourseLesson(course_id=course_id, **request.model_dump())
    db.add(lesson)
    db.commit()
    db.refresh(lesson)
    return _course_lesson_payload(lesson)


@router.patch("/courses/{course_id}/lessons/{lesson_id}")
def update_course_lesson(
    course_id: int,
    lesson_id: int,
    request: CourseLessonUpdate,
    _admin: User = Depends(require_roles(*ADMIN_ROLES)),
    db: Session = Depends(get_db),
) -> dict:
    lesson = db.scalar(
        select(CourseLesson).where(
            CourseLesson.id == lesson_id, CourseLesson.course_id == course_id
        )
    )
    if lesson is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy buổi học.")
    course = db.get(Course, course_id)
    updates = request.model_dump(exclude_unset=True)
    next_sequence = updates.get("sequence", lesson.sequence)
    if next_sequence > course.session_count:
        raise HTTPException(
            status_code=422, detail="Số buổi vượt quá số buổi khai báo của môn học."
        )
    if db.scalar(
        select(CourseLesson.id).where(
            CourseLesson.course_id == course_id,
            CourseLesson.sequence == next_sequence,
            CourseLesson.id != lesson_id,
        )
    ):
        raise HTTPException(status_code=409, detail="Thứ tự buổi học đã được sử dụng.")
    for field, value in updates.items():
        setattr(lesson, field, value)
    db.commit()
    db.refresh(lesson)
    return _course_lesson_payload(lesson)


@router.post("/courses/{course_id}/lessons/clone", status_code=status.HTTP_201_CREATED)
def clone_course_lessons(
    course_id: int,
    request: CourseLessonsClone,
    _admin: User = Depends(require_roles(*ADMIN_ROLES)),
    db: Session = Depends(get_db),
) -> list[dict]:
    target = db.get(Course, course_id)
    source = db.get(Course, request.source_course_id)
    if target is None or source is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy môn học nguồn hoặc đích.")
    if target.id == source.id:
        raise HTTPException(status_code=422, detail="Môn nguồn và môn đích phải khác nhau.")
    if db.scalar(select(CourseLesson.id).where(CourseLesson.course_id == target.id)):
        raise HTTPException(
            status_code=409, detail="Môn đích đã có syllabus; hãy xóa nội dung trước khi nhân bản."
        )
    source_lessons = db.scalars(
        select(CourseLesson)
        .where(CourseLesson.course_id == source.id)
        .order_by(CourseLesson.sequence)
    ).all()
    if len(source_lessons) > target.session_count:
        raise HTTPException(
            status_code=422,
            detail="Syllabus nguồn có nhiều buổi hơn số buổi của môn đích.",
        )
    copied = [
        CourseLesson(
            course_id=target.id,
            sequence=lesson.sequence,
            title=lesson.title,
            objectives=lesson.objectives,
            description=lesson.description,
        )
        for lesson in source_lessons
    ]
    db.add_all(copied)
    db.commit()
    return [_course_lesson_payload(lesson) for lesson in copied]


@router.delete(
    "/courses/{course_id}/lessons/{lesson_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_course_lesson(
    course_id: int,
    lesson_id: int,
    _admin: User = Depends(require_roles(*ADMIN_ROLES)),
    db: Session = Depends(get_db),
) -> None:
    lesson = db.scalar(
        select(CourseLesson).where(
            CourseLesson.id == lesson_id, CourseLesson.course_id == course_id
        )
    )
    if lesson is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy buổi học.")
    db.delete(lesson)
    db.commit()


@router.get("/admissions-staff")
def list_admissions_staff(
    _user: User = Depends(require_roles("admin", "admissions")),
    db: Session = Depends(get_db),
) -> list[dict]:
    staff = db.scalars(
        select(User)
        .options(selectinload(User.roles))
        .where(User.is_active.is_(True), User.is_locked.is_(False))
        .order_by(User.full_name, User.id)
    ).all()
    return [
        {"id": member.id, "full_name": member.full_name, "email": member.email}
        for member in staff
        if any(role.code == "admissions" for role in member.roles)
    ]


@router.get("/study-programs")
def list_study_programs(
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    programs = db.scalars(
        select(StudyProgram)
        .options(selectinload(StudyProgram.courses))
        .order_by(StudyProgram.name)
    ).all()
    result = []
    for program in programs:
        course_sequence = {
            row.subject_id: {
                "sequence": row.order_index,
                "prerequisite_course_id": row.prerequisite_subject_id,
            }
            for row in db.execute(
                select(
                    program_subjects.c.subject_id,
                    program_subjects.c.order_index,
                    program_subjects.c.prerequisite_subject_id,
                ).where(program_subjects.c.program_id == program.id)
            )
        }
        result.append({
            "id": program.id,
            "code": program.code,
            "name": program.name,
            "degree_level": program.degree_level,
            "total_credits": program.total_credits,
            "duration_semesters": program.duration_semesters,
            "description": program.description,
            "courses": [
                {
                    "id": course.id,
                    "code": course.code,
                    "name": course.name,
                    "sequence": course_sequence[course.id]["sequence"],
                    "prerequisite_course_id": course_sequence[course.id][
                        "prerequisite_course_id"
                    ],
                }
                for course in program.courses
            ],
        })
    return result


def _program_subject_payload(db: Session, program_id: int) -> list[dict]:
    rows = db.execute(
        select(
            Course.id,
            Course.code,
            Course.name,
            program_subjects.c.order_index,
            program_subjects.c.prerequisite_subject_id,
        )
        .join(program_subjects, program_subjects.c.subject_id == Course.id)
        .where(program_subjects.c.program_id == program_id)
        .order_by(program_subjects.c.order_index, Course.id)
    )
    return [
        {
            "subject_id": row.id,
            "id": row.id,
            "code": row.code,
            "name": row.name,
            "order_index": row.order_index,
            "prerequisite_subject_id": row.prerequisite_subject_id,
        }
        for row in rows
    ]


@router.get("/study-programs/{program_id}/subjects")
def get_study_program_subjects(
    program_id: int,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    if db.get(StudyProgram, program_id) is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy chương trình đào tạo.")
    return _program_subject_payload(db, program_id)


@router.post(
    "/study-programs/{program_id}/subjects",
    status_code=status.HTTP_201_CREATED,
)
def add_study_program_subject(
    program_id: int,
    request: StudyProgramSubjectCreate,
    _admin: User = Depends(require_roles(*ADMIN_ROLES)),
    db: Session = Depends(get_db),
) -> dict:
    if db.get(StudyProgram, program_id) is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy chương trình đào tạo.")
    if db.get(Course, request.subject_id) is None:
        raise HTTPException(status_code=422, detail="Môn học không tồn tại.")
    if db.scalar(
        select(program_subjects.c.subject_id).where(
            program_subjects.c.program_id == program_id,
            program_subjects.c.subject_id == request.subject_id,
        )
    ) is not None:
        raise HTTPException(status_code=409, detail="Môn học đã có trong chương trình.")
    next_order = db.scalar(
        select(func.max(program_subjects.c.order_index)).where(
            program_subjects.c.program_id == program_id
        )
    )
    db.execute(
        program_subjects.insert().values(
            program_id=program_id,
            subject_id=request.subject_id,
            order_index=(next_order or 0) + 1,
            prerequisite_subject_id=None,
        )
    )
    db.commit()
    return next(
        item
        for item in _program_subject_payload(db, program_id)
        if item["subject_id"] == request.subject_id
    )


@router.put("/study-programs/{program_id}/subjects")
def update_study_program_subjects(
    program_id: int,
    request: StudyProgramSubjectsUpdate,
    _admin: User = Depends(require_roles(*ADMIN_ROLES)),
    db: Session = Depends(get_db),
) -> dict:
    program = db.get(StudyProgram, program_id)
    if program is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy chương trình đào tạo.")
    subject_ids = [item.subject_id for item in request.subjects]
    if len(subject_ids) != len(set(subject_ids)):
        raise HTTPException(status_code=422, detail="Không thể gắn trùng môn học.")
    if subject_ids:
        found_ids = set(db.scalars(select(Course.id).where(Course.id.in_(subject_ids))).all())
        if found_ids != set(subject_ids):
            raise HTTPException(status_code=422, detail="Có môn học không tồn tại.")
    prerequisites = {
        item.subject_id: item.prerequisite_subject_id for item in request.subjects
    }
    positions = {subject_id: index for index, subject_id in enumerate(subject_ids)}
    for subject_id, prerequisite_id in prerequisites.items():
        if prerequisite_id == subject_id:
            raise HTTPException(
                status_code=422, detail="Môn học không thể là tiên quyết của chính nó."
            )
        if prerequisite_id is not None and prerequisite_id not in prerequisites:
            raise HTTPException(
                status_code=422,
                detail="Môn tiên quyết phải được gắn vào cùng chương trình đào tạo.",
            )
        if prerequisite_id is not None and positions[prerequisite_id] >= positions[subject_id]:
            raise HTTPException(
                status_code=422,
                detail="Môn tiên quyết phải đứng trước môn học trong chương trình.",
            )

    db.execute(
        program_subjects.delete().where(
            program_subjects.c.program_id == program_id
        )
    )
    rows = [
        {
            "program_id": program_id,
            "subject_id": item.subject_id,
            "order_index": index + 1,
            "prerequisite_subject_id": item.prerequisite_subject_id,
        }
        for index, item in enumerate(request.subjects)
    ]
    if rows:
        db.execute(program_subjects.insert(), rows)
    db.commit()
    return {"program_id": program_id, "subject_ids": subject_ids}


@router.delete(
    "/study-programs/{program_id}/subjects/{subject_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_study_program_subject(
    program_id: int,
    subject_id: int,
    _admin: User = Depends(require_roles(*ADMIN_ROLES)),
    db: Session = Depends(get_db),
) -> Response:
    if db.get(StudyProgram, program_id) is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy chương trình đào tạo.")
    deleted = db.execute(
        program_subjects.delete().where(
            program_subjects.c.program_id == program_id,
            program_subjects.c.subject_id == subject_id,
        )
    )
    if not deleted.rowcount:
        raise HTTPException(status_code=404, detail="Môn học không thuộc chương trình này.")
    db.execute(
        program_subjects.update()
        .where(
            program_subjects.c.program_id == program_id,
            program_subjects.c.prerequisite_subject_id == subject_id,
        )
        .values(prerequisite_subject_id=None)
    )
    remaining = db.execute(
        select(
            program_subjects.c.subject_id,
            program_subjects.c.order_index,
        )
        .where(program_subjects.c.program_id == program_id)
        .order_by(program_subjects.c.order_index, program_subjects.c.subject_id)
    ).all()
    for index, row in enumerate(remaining, start=1):
        if row.order_index != index:
            db.execute(
                program_subjects.update()
                .where(
                    program_subjects.c.program_id == program_id,
                    program_subjects.c.subject_id == row.subject_id,
                )
                .values(order_index=index)
            )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/study-programs/{program_id}/courses")
def update_study_program_courses(
    program_id: int,
    request: StudyProgramCoursesUpdate,
    _admin: User = Depends(require_roles(*ADMIN_ROLES)),
    db: Session = Depends(get_db),
) -> dict:
    program = db.get(StudyProgram, program_id)
    if program is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy chương trình đào tạo.")
    course_ids = [item.course_id for item in request.courses]
    if len(course_ids) != len(set(course_ids)):
        raise HTTPException(status_code=422, detail="Không thể gắn trùng môn học.")
    if course_ids:
        found_ids = set(db.scalars(select(Course.id).where(Course.id.in_(course_ids))).all())
        if found_ids != set(course_ids):
            raise HTTPException(status_code=422, detail="Có môn học không tồn tại.")
    requested_prerequisites = {
        item.course_id: item.prerequisite_course_id for item in request.courses
    }
    course_positions = {course_id: index for index, course_id in enumerate(course_ids)}
    for course_id, prerequisite_id in requested_prerequisites.items():
        if prerequisite_id == course_id:
            raise HTTPException(
                status_code=422, detail="Môn học không thể là tiên quyết của chính nó."
            )
        if prerequisite_id is not None and prerequisite_id not in requested_prerequisites:
            raise HTTPException(
                status_code=422,
                detail="Môn tiên quyết phải được gắn vào cùng chương trình đào tạo.",
            )
        if prerequisite_id is not None and course_positions[prerequisite_id] >= course_positions[course_id]:
            raise HTTPException(
                status_code=422,
                detail="Môn tiên quyết phải đứng trước môn học trong chương trình.",
            )

    db.execute(
        program_subjects.delete().where(
            program_subjects.c.program_id == program_id
        )
    )
    rows = [
        {
            "program_id": program_id,
            "subject_id": item.course_id,
            "order_index": index + 1,
            "prerequisite_subject_id": item.prerequisite_course_id,
        }
        for index, item in enumerate(request.courses)
    ]
    if rows:
        db.execute(program_subjects.insert(), rows)
    db.commit()
    return {"id": program_id, "course_ids": course_ids}


@router.post("/courses", status_code=status.HTTP_201_CREATED)
def create_course(
    request: CourseCreate,
    _admin: User = Depends(require_roles(*ADMIN_ROLES)),
    db: Session = Depends(get_db),
) -> dict:
    if db.scalar(select(Course.id).where(Course.code == request.code)):
        raise HTTPException(status_code=409, detail="Mã môn học đã tồn tại.")
    if request.prerequisite_id == 0:
        raise HTTPException(status_code=422, detail="Môn tiên quyết không hợp lệ.")
    course = Course(**request.model_dump())
    db.add(course)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=422, detail="Môn tiên quyết không tồn tại."
        ) from error
    db.refresh(course)
    return _course_payload(course)


@router.patch("/courses/{course_id}")
def update_course(
    course_id: int,
    request: CourseUpdate,
    _admin: User = Depends(require_roles(*ADMIN_ROLES)),
    db: Session = Depends(get_db),
) -> dict:
    course = db.get(Course, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy môn học.")
    updates = request.model_dump(exclude_unset=True)
    if updates.get("prerequisite_id") == course.id:
        raise HTTPException(status_code=422, detail="Môn học không thể là tiên quyết của chính nó.")
    if (
        "session_count" in updates
        and (db.scalar(
            select(func.max(CourseLesson.sequence)).where(
                CourseLesson.course_id == course.id
            )
        ) or 0) > updates["session_count"]
    ):
        raise HTTPException(
            status_code=409,
            detail="Không thể giảm số buổi xuống thấp hơn buổi đã khai báo trong syllabus.",
        )
    for field, value in updates.items():
        setattr(course, field, value)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=422, detail="Dữ liệu môn tiên quyết không hợp lệ.") from error
    db.refresh(course)
    return _course_payload(course)


@router.delete("/courses/{course_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_course(
    course_id: int,
    _admin: User = Depends(require_roles(*ADMIN_ROLES)),
    db: Session = Depends(get_db),
) -> None:
    course = db.get(Course, course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy môn học.")
    if db.scalar(
        select(TrainingClass.id).where(TrainingClass.course_id == course_id).limit(1)
    ):
        raise HTTPException(
            status_code=409,
            detail="Không thể xóa môn học đã được xếp vào lớp. Hãy ngừng mở môn học.",
        )
    db.delete(course)
    db.commit()


def _visible_class_query(current_user: User):
    roles = {role.code for role in current_user.roles}
    query = select(TrainingClass).options(
        joinedload(TrainingClass.course),
        joinedload(TrainingClass.teacher),
        joinedload(TrainingClass.assistant),
        selectinload(TrainingClass.assistants),
        selectinload(TrainingClass.enrollments),
    )
    if roles.intersection(ADMIN_ROLES + ("admissions", "accountant")):
        return query
    if roles.intersection(("teacher", "ta")):
        return query.where(
            or_(
                TrainingClass.teacher_id == current_user.id,
                TrainingClass.assistant_id == current_user.id,
                TrainingClass.assistants.any(User.id == current_user.id),
            )
        )
    if "student" in roles:
        return query.join(ClassEnrollment).where(
            ClassEnrollment.student_id == current_user.id
        )
    return query.where(false())


@router.get("/classes")
def list_classes(
    term: str | None = Query(default=None, max_length=50),
    q: str = Query(default="", max_length=150),
    current_user: User = Depends(require_roles(*ALL_ROLES)),
    db: Session = Depends(get_db),
) -> list[dict]:
    query = _visible_class_query(current_user)
    if term:
        query = query.where(TrainingClass.term == term)
    if q.strip():
        search = f"%{q.strip()}%"
        query = query.join(Course).where(
            or_(
                TrainingClass.code.ilike(search),
                TrainingClass.name.ilike(search),
                Course.code.ilike(search),
                Course.name.ilike(search),
            )
        )
    rows = db.scalars(query.order_by(TrainingClass.day_of_week, TrainingClass.start_time)).unique().all()
    return [_class_payload(item) for item in rows]


@router.post("/classes", status_code=status.HTTP_201_CREATED)
def create_class(
    request: TrainingClassCreate,
    _admin: User = Depends(require_roles(*ADMIN_ROLES)),
    db: Session = Depends(get_db),
) -> dict:
    if db.scalar(
        select(TrainingClass.id).where(TrainingClass.code == request.code)
    ):
        raise HTTPException(status_code=409, detail="Mã lớp học đã tồn tại.")
    course = db.get(Course, request.course_id)
    teacher = db.scalar(
        select(User)
        .options(selectinload(User.roles))
        .where(User.id == request.teacher_id)
    )
    if course is None:
        raise HTTPException(status_code=422, detail="Môn học không tồn tại.")
    if request.program_id is not None:
        program = db.scalar(
            select(StudyProgram)
            .options(selectinload(StudyProgram.courses))
            .where(StudyProgram.id == request.program_id)
        )
        if program is None or request.course_id not in {
            item.id for item in program.courses
        }:
            raise HTTPException(
                status_code=422,
                detail="Môn học không thuộc chương trình đào tạo đã chọn.",
            )
    classroom = db.scalar(
        select(Classroom).where(Classroom.code == request.room.strip())
    )
    if classroom is None and db.scalar(select(Classroom.id).limit(1)):
        raise HTTPException(
            status_code=422, detail="Phòng học không có trong danh mục phòng."
        )
    if classroom is not None and request.capacity > classroom.capacity:
        raise HTTPException(
            status_code=422, detail="Sĩ số lớp vượt quá sức chứa của phòng học."
        )
    if teacher is None or "teacher" not in {role.code for role in teacher.roles}:
        raise HTTPException(status_code=422, detail="Giảng viên không hợp lệ.")
    assistant_ids = set(request.assistant_ids)
    if len(assistant_ids) != len(request.assistant_ids):
        raise HTTPException(status_code=422, detail="Danh sách trợ giảng bị trùng.")
    if request.assistant_id is not None:
        if request.assistant_id in assistant_ids:
            raise HTTPException(status_code=422, detail="Danh sách trợ giảng bị trùng.")
        assistant_ids.add(request.assistant_id)
    assistants = _resolve_assistants(db, assistant_ids, teacher.id)

    overlap = db.scalar(
        select(TrainingClass.id).where(
            TrainingClass.term == request.term,
            TrainingClass.day_of_week == request.day_of_week,
            TrainingClass.start_time < request.end_time,
            TrainingClass.end_time > request.start_time,
            or_(
                TrainingClass.teacher_id == request.teacher_id,
                TrainingClass.room == request.room,
                *([TrainingClass.assistant_id.in_(assistant_ids)] if assistant_ids else []),
                *(
                    [TrainingClass.assistants.any(User.id.in_(assistant_ids))]
                    if assistant_ids
                    else []
                ),
            ),
        ).limit(1)
    )
    if overlap:
        raise HTTPException(
            status_code=409,
            detail="Lịch bị trùng với lớp khác của giảng viên hoặc phòng học.",
        )
    training_class = TrainingClass(
        **request.model_dump(exclude={"program_id", "assistant_ids", "assistant_id"}),
        assistant_id=assistants[0].id if assistants else None,
        assistants=assistants,
    )
    db.add(training_class)
    db.commit()
    training_class = db.scalar(
        select(TrainingClass)
        .options(
            joinedload(TrainingClass.course),
            joinedload(TrainingClass.teacher),
            joinedload(TrainingClass.assistant),
            selectinload(TrainingClass.assistants),
            selectinload(TrainingClass.enrollments),
        )
        .where(TrainingClass.id == training_class.id)
    )
    return _class_payload(training_class)


def _resolve_assistants(
    db: Session, assistant_ids: set[int], teacher_id: int
) -> list[User]:
    if teacher_id in assistant_ids:
        raise HTTPException(
            status_code=422, detail="Giảng viên không thể đồng thời là trợ giảng."
        )
    if not assistant_ids:
        return []
    assistants = db.scalars(
        select(User)
        .options(selectinload(User.roles))
        .where(User.id.in_(assistant_ids))
    ).all()
    if len(assistants) != len(assistant_ids) or any(
        "ta" not in {role.code for role in assistant.roles}
        for assistant in assistants
    ):
        raise HTTPException(status_code=422, detail="Có trợ giảng không hợp lệ.")
    return assistants


def _assert_class_staff_access(current_user: User, training_class: TrainingClass) -> None:
    roles = {role.code for role in current_user.roles}
    if roles.intersection(ADMIN_ROLES):
        return
    if "teacher" in roles and training_class.teacher_id == current_user.id:
        return
    if "ta" in roles and (
        training_class.assistant_id == current_user.id
        or any(assistant.id == current_user.id for assistant in training_class.assistants)
    ):
        return
    raise HTTPException(status_code=403, detail="Bạn không được phân công vào lớp này.")


@router.get("/classes/{class_id}/students")
def list_class_students(
    class_id: int,
    current_user: User = Depends(
        require_roles("admin", "manager", "admissions", "accountant", "teacher", "ta")
    ),
    db: Session = Depends(get_db),
) -> list[dict]:
    training_class = db.get(TrainingClass, class_id)
    if training_class is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy lớp học.")
    if {role.code for role in current_user.roles}.intersection(("teacher", "ta")):
        _assert_class_staff_access(current_user, training_class)
    enrollments = db.scalars(
        select(ClassEnrollment)
        .options(
            joinedload(ClassEnrollment.student).selectinload(User.roles)
        )
        .where(ClassEnrollment.class_id == class_id)
        .join(User, ClassEnrollment.student_id == User.id)
        .order_by(User.full_name)
    ).all()
    attendance_records = db.execute(
        select(
            AttendanceRecord.student_id,
            AttendanceSession.session_date,
            AttendanceRecord.status,
        )
        .join(AttendanceSession, AttendanceRecord.session_id == AttendanceSession.id)
        .where(AttendanceSession.class_id == class_id)
        .order_by(AttendanceSession.session_date.desc())
    ).all()
    history_by_student: dict[int, list[dict[str, str]]] = {}
    for student_id, session_date, attendance_status in attendance_records:
        history_by_student.setdefault(student_id, []).append(
            {"session_date": session_date.isoformat(), "status": attendance_status}
        )
    return [
        {
            **_user_payload(enrollment.student),
            "tuition_status": enrollment.tuition_status,
            "attendance_status": enrollment.attendance_status,
            "last_attendance_at": (
                enrollment.last_attendance_at.isoformat()
                if enrollment.last_attendance_at
                else None
            ),
            "attendance_history": history_by_student.get(enrollment.student_id, []),
        }
        for enrollment in enrollments
    ]


@router.get("/classes/{class_id}/students/export")
def export_class_students(
    class_id: int,
    current_user: User = Depends(
        require_roles("admin", "manager", "admissions", "accountant", "teacher", "ta")
    ),
    db: Session = Depends(get_db),
) -> Response:
    training_class = db.get(TrainingClass, class_id)
    if training_class is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy lớp học.")
    current_roles = {role.code for role in current_user.roles}
    if current_roles.intersection(("teacher", "ta")):
        _assert_class_staff_access(current_user, training_class)
    enrollments = db.scalars(
        select(ClassEnrollment)
        .join(User, ClassEnrollment.student_id == User.id)
        .options(joinedload(ClassEnrollment.student))
        .where(ClassEnrollment.class_id == class_id)
        .order_by(User.full_name)
    ).all()
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Danh sách học viên"
    sheet.append(
        [
            "Mã học viên",
            "Họ và tên",
            "Email",
            "Số điện thoại",
            "Học phí",
            "Điểm danh",
        ]
    )
    for enrollment in enrollments:
        student = enrollment.student
        sheet.append(
            [
                student.student_code or student.username,
                student.full_name,
                student.email,
                student.phone or "",
                "Đã đóng" if enrollment.tuition_status == "paid" else "Chưa đóng",
                {
                    "not_recorded": "Chưa điểm danh",
                    "present": "Có mặt",
                    "absent": "Vắng",
                    "late": "Đi muộn",
                }[enrollment.attendance_status],
            ]
        )
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    output = BytesIO()
    workbook.save(output)
    filename = f"{training_class.code}-students.xlsx"
    return Response(
        content=output.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.patch("/classes/{class_id}/students/{student_id}/status")
def update_enrollment_status(
    class_id: int,
    student_id: int,
    request: EnrollmentStatusUpdate,
    current_user: User = Depends(
        require_roles("admin", "manager", "accountant", "teacher", "ta")
    ),
    db: Session = Depends(get_db),
) -> dict:
    training_class = db.get(TrainingClass, class_id)
    if training_class is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy lớp học.")
    current_roles = {role.code for role in current_user.roles}
    if current_roles.intersection(("teacher", "ta")):
        _assert_class_staff_access(current_user, training_class)
    if request.tuition_status is not None and not current_roles.intersection(
        ADMIN_ROLES + ("accountant",)
    ):
        raise HTTPException(
            status_code=403,
            detail="Chỉ quản trị viên, quản lý hoặc kế toán mới được cập nhật học phí.",
        )
    if request.attendance_status is not None and not current_roles.intersection(
        ADMIN_ROLES + ("teacher", "ta")
    ):
        raise HTTPException(
            status_code=403,
            detail="Chỉ giảng viên, trợ giảng hoặc quản lý mới được cập nhật điểm danh.",
        )
    enrollment = db.scalar(
        select(ClassEnrollment).where(
            ClassEnrollment.class_id == class_id,
            ClassEnrollment.student_id == student_id,
        )
    )
    if enrollment is None:
        raise HTTPException(status_code=404, detail="Học viên chưa đăng ký lớp này.")
    if (
        enrollment.enrollment_status != "active"
        and enrollment.effective_date is not None
        and enrollment.effective_date <= datetime.now(timezone.utc).date()
    ):
        raise HTTPException(
            status_code=409,
            detail="Không thể điểm danh học viên sau ngày hiệu lực biến động.",
        )
    if request.tuition_status is not None:
        enrollment.tuition_status = request.tuition_status
    if request.attendance_status is not None:
        enrollment.attendance_status = request.attendance_status
        now = datetime.now(timezone.utc)
        enrollment.last_attendance_at = (
            None
            if request.attendance_status == "not_recorded"
            else now
        )
        attendance_session = db.scalar(
            select(AttendanceSession)
            .where(
                AttendanceSession.class_id == class_id,
                AttendanceSession.session_date == now.date(),
            )
            .limit(1)
        )
        if attendance_session is None:
            attendance_session = AttendanceSession(
                class_id=class_id,
                session_date=now.date(),
                start_time=training_class.start_time,
                end_time=training_class.end_time,
            )
            db.add(attendance_session)
            db.flush()
        attendance_record = db.scalar(
            select(AttendanceRecord).where(
                AttendanceRecord.session_id == attendance_session.id,
                AttendanceRecord.student_id == student_id,
            )
        )
        old_status = attendance_record.status if attendance_record else None
        if attendance_record is None:
            attendance_record = AttendanceRecord(
                session_id=attendance_session.id,
                student_id=student_id,
                marked_by=current_user.id,
                status=request.attendance_status,
                marked_at=now.replace(tzinfo=None),
            )
            db.add(attendance_record)
            db.flush()
        else:
            attendance_record.marked_by = current_user.id
            attendance_record.status = request.attendance_status
            attendance_record.marked_at = now.replace(tzinfo=None)
        if old_status != request.attendance_status:
            db.add(
                AttendanceChangeLog(
                    record_id=attendance_record.id,
                    changed_by=current_user.id,
                    old_status=old_status,
                    new_status=request.attendance_status,
                )
            )
    db.commit()
    attendance_history = []
    if request.attendance_status is not None:
        attendance_history = [
            {"session_date": session_date.isoformat(), "status": attendance_status}
            for session_date, attendance_status in db.execute(
                select(AttendanceSession.session_date, AttendanceRecord.status)
                .join(
                    AttendanceRecord,
                    AttendanceRecord.session_id == AttendanceSession.id,
                )
                .where(
                    AttendanceSession.class_id == class_id,
                    AttendanceRecord.student_id == student_id,
                )
                .order_by(AttendanceSession.session_date.desc())
                .limit(4)
            ).all()
        ]
    return {
        "student_id": student_id,
        "tuition_status": enrollment.tuition_status,
        "attendance_status": enrollment.attendance_status,
        "last_attendance_at": (
            enrollment.last_attendance_at.isoformat()
            if enrollment.last_attendance_at
            else None
        ),
        "attendance_history": attendance_history,
    }


@router.patch("/classes/{class_id}")
def update_class(
    class_id: int,
    request: TrainingClassUpdate,
    _admin: User = Depends(require_roles(*ADMIN_ROLES)),
    db: Session = Depends(get_db),
) -> dict:
    training_class = db.scalar(
        select(TrainingClass)
        .options(
            selectinload(TrainingClass.enrollments),
            selectinload(TrainingClass.assistants),
        )
        .where(TrainingClass.id == class_id)
    )
    if training_class is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy lớp học.")
    updates = request.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="Không có dữ liệu cần cập nhật.")
    requested_assistant_ids = updates.pop("assistant_ids", None)
    legacy_assistant_id = updates.pop("assistant_id", None) if "assistant_id" in updates else None
    for required_field in (
        "course_id",
        "teacher_id",
        "room",
        "term",
        "day_of_week",
        "start_time",
        "end_time",
        "capacity",
    ):
        if required_field in updates and updates[required_field] is None:
            raise HTTPException(
                status_code=422, detail=f"Trường {required_field} không được để trống."
            )

    course_id = updates.get("course_id", training_class.course_id)
    teacher_id = updates.get("teacher_id", training_class.teacher_id)
    room = updates.get("room", training_class.room)
    term = updates.get("term", training_class.term)
    weekday = updates.get("day_of_week", training_class.day_of_week)
    start_time = updates.get("start_time", training_class.start_time)
    end_time = updates.get("end_time", training_class.end_time)
    capacity = updates.get("capacity", training_class.capacity)
    if end_time <= start_time:
        raise HTTPException(status_code=422, detail="Giờ kết thúc phải sau giờ bắt đầu.")
    if capacity < len(training_class.enrollments):
        raise HTTPException(
            status_code=409, detail="Sức chứa mới nhỏ hơn sĩ số hiện tại của lớp."
        )
    if db.get(Course, course_id) is None:
        raise HTTPException(status_code=422, detail="Môn học không tồn tại.")
    classroom = db.scalar(select(Classroom).where(Classroom.code == room.strip()))
    if classroom is None and db.scalar(select(Classroom.id).limit(1)):
        raise HTTPException(
            status_code=422, detail="Phòng học không có trong danh mục phòng."
        )
    if classroom is not None and capacity > classroom.capacity:
        raise HTTPException(
            status_code=422, detail="Sĩ số lớp vượt quá sức chứa của phòng học."
        )
    if classroom is not None and len(training_class.enrollments) > classroom.capacity:
        raise HTTPException(
            status_code=409,
            detail="Sức chứa phòng mới nhỏ hơn sĩ số đã ghi danh.",
        )
    teacher = db.scalar(
        select(User).options(selectinload(User.roles)).where(User.id == teacher_id)
    )
    if teacher is None or "teacher" not in {role.code for role in teacher.roles}:
        raise HTTPException(status_code=422, detail="Giảng viên không hợp lệ.")
    current_assistant_ids = {
        assistant.id for assistant in training_class.assistants
    }
    if training_class.assistant_id is not None:
        current_assistant_ids.add(training_class.assistant_id)
    if requested_assistant_ids is not None:
        if len(set(requested_assistant_ids)) != len(requested_assistant_ids):
            raise HTTPException(status_code=422, detail="Danh sách trợ giảng bị trùng.")
        next_assistant_ids = set(requested_assistant_ids)
        if legacy_assistant_id is not None:
            next_assistant_ids.add(legacy_assistant_id)
    elif "assistant_id" in request.model_fields_set:
        next_assistant_ids = (
            {legacy_assistant_id} if legacy_assistant_id is not None else set()
        )
    else:
        next_assistant_ids = current_assistant_ids
    next_assistants = _resolve_assistants(db, next_assistant_ids, teacher_id)

    allowed_status_transitions = {
        "preparing": {"active", "cancelled"},
        "active": {"completed", "cancelled"},
        "completed": set(),
        "cancelled": set(),
    }
    next_status = updates.get("status", training_class.status)
    if next_status != training_class.status and next_status not in allowed_status_transitions[
        training_class.status
    ]:
        raise HTTPException(
            status_code=409,
            detail=f"Không thể chuyển vòng đời lớp từ {training_class.status} sang {next_status}.",
        )

    overlap = db.scalar(
        select(TrainingClass.id).where(
            TrainingClass.id != class_id,
            TrainingClass.term == term,
            TrainingClass.day_of_week == weekday,
            TrainingClass.start_time < end_time,
            TrainingClass.end_time > start_time,
            or_(
                TrainingClass.teacher_id == teacher_id,
                TrainingClass.room == room,
                *(
                    [TrainingClass.assistant_id.in_(next_assistant_ids)]
                    if next_assistant_ids
                    else []
                ),
                *(
                    [TrainingClass.assistants.any(User.id.in_(next_assistant_ids))]
                    if next_assistant_ids
                    else []
                ),
            ),
        ).limit(1)
    )
    if overlap:
        raise HTTPException(
            status_code=409,
            detail="Lịch bị trùng với lớp khác của giảng viên hoặc phòng học.",
        )

    student_ids = [enrollment.student_id for enrollment in training_class.enrollments]
    if student_ids:
        conflicting_enrollments = db.scalars(
            select(ClassEnrollment)
            .join(TrainingClass, ClassEnrollment.class_id == TrainingClass.id)
            .where(
                ClassEnrollment.student_id.in_(student_ids),
                TrainingClass.id != class_id,
                TrainingClass.term == term,
                TrainingClass.day_of_week == weekday,
                TrainingClass.start_time < end_time,
                TrainingClass.end_time > start_time,
            )
        ).all()
        if conflicting_enrollments:
            conflicts = sorted(
                {enrollment.student_id for enrollment in conflicting_enrollments}
            )
            raise HTTPException(
                status_code=409,
                detail=f"Lịch mới bị trùng với lớp khác của học viên: {conflicts}.",
            )

    previous_schedule = (
        training_class.term,
        training_class.day_of_week,
        training_class.start_time,
        training_class.end_time,
        training_class.room,
    )
    for field, value in updates.items():
        setattr(training_class, field, value)
    training_class.assistants = next_assistants
    training_class.assistant_id = next_assistants[0].id if next_assistants else None
    next_schedule = (term, weekday, start_time, end_time, room)
    if previous_schedule != next_schedule:
        message = (
            f"Lịch lớp {training_class.code} đã thay đổi: {WEEKDAYS[weekday]}, "
            f"{start_time.strftime('%H:%M')}–{end_time.strftime('%H:%M')}, "
            f"phòng {room}, học kỳ {term}."
        )
        db.add_all(
            Notification(
                user_id=student_id,
                class_id=class_id,
                title="Lịch học được cập nhật",
                message=message,
            )
            for student_id in student_ids
        )
    db.commit()
    updated = db.scalar(
        select(TrainingClass)
        .options(
            joinedload(TrainingClass.course),
            joinedload(TrainingClass.teacher),
            joinedload(TrainingClass.assistant),
            selectinload(TrainingClass.assistants),
            selectinload(TrainingClass.enrollments),
        )
        .where(TrainingClass.id == class_id)
    )
    return _class_payload(updated)


@router.post("/classes/{class_id}/students", status_code=status.HTTP_201_CREATED)
def enroll_students(
    class_id: int,
    request: EnrollmentRequest,
    _admin: User = Depends(require_roles(*ADMIN_ROLES, "admissions")),
    db: Session = Depends(get_db),
) -> dict:
    training_class = db.scalar(
        select(TrainingClass)
        .options(selectinload(TrainingClass.enrollments))
        .where(TrainingClass.id == class_id)
    )
    if training_class is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy lớp học.")
    student_ids = set(request.student_ids)
    current_ids = {enrollment.student_id for enrollment in training_class.enrollments}
    new_ids = student_ids - current_ids
    if not new_ids:
        raise HTTPException(status_code=409, detail="Học viên đã có trong lớp.")
    if len(training_class.enrollments) + len(new_ids) > training_class.capacity:
        raise HTTPException(status_code=409, detail="Sĩ số lớp vượt quá sức chứa.")
    if training_class.status not in {"preparing", "active"}:
        raise HTTPException(
            status_code=409,
            detail="Không thể ghi danh vào lớp đã kết thúc hoặc bị hủy.",
        )
    classroom = db.scalar(
        select(Classroom).where(Classroom.code == training_class.room)
    )
    if (
        classroom is not None
        and len(training_class.enrollments) + len(new_ids) > classroom.capacity
    ):
        raise HTTPException(
            status_code=409,
            detail="Sĩ số ghi danh vượt quá sức chứa của phòng học.",
        )

    students = db.scalars(
        select(User).options(selectinload(User.roles)).where(User.id.in_(new_ids))
    ).all()
    student_by_id = {student.id: student for student in students}
    if len(student_by_id) != len(new_ids):
        raise HTTPException(status_code=422, detail="Có học viên không tồn tại.")
    if any(
        not student.is_active
        or student.is_locked
        or "student" not in {role.code for role in student.roles}
        for student in students
    ):
        raise HTTPException(
            status_code=422, detail="Chỉ thêm được tài khoản học viên đang hoạt động."
        )

    overlapping_classes = db.scalars(
        select(TrainingClass)
        .join(ClassEnrollment)
        .where(
            ClassEnrollment.student_id.in_(new_ids),
            TrainingClass.term == training_class.term,
            TrainingClass.day_of_week == training_class.day_of_week,
            TrainingClass.start_time < training_class.end_time,
            TrainingClass.end_time > training_class.start_time,
        )
    ).all()
    overlapping_class_ids = [item.id for item in overlapping_classes]
    conflicts = db.scalars(
        select(ClassEnrollment).where(
            ClassEnrollment.student_id.in_(new_ids),
            ClassEnrollment.class_id.in_(overlapping_class_ids or [-1]),
        )
    ).all()
    if conflicts:
        conflicted_ids = sorted({enrollment.student_id for enrollment in conflicts})
        raise HTTPException(
            status_code=409,
            detail=f"Lịch học bị trùng với lớp hiện tại của học viên: {conflicted_ids}.",
        )

    db.add_all(
        ClassEnrollment(class_id=class_id, student_id=student_id)
        for student_id in sorted(new_ids)
    )
    db.commit()
    return {
        "class_id": class_id,
        "enrolled_count": len(new_ids),
        "student_count": len(current_ids) + len(new_ids),
    }


@router.delete("/classes/{class_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_class(
    class_id: int,
    _admin: User = Depends(require_roles(*ADMIN_ROLES)),
    db: Session = Depends(get_db),
) -> None:
    training_class = db.get(TrainingClass, class_id)
    if training_class is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy lớp học.")
    db.delete(training_class)
    db.commit()


@router.get("/assignments")
def list_assignments(
    current_user: User = Depends(require_roles(*ALL_ROLES)),
    db: Session = Depends(get_db),
) -> list[dict]:
    roles = {role.code for role in current_user.roles}
    query = (
        select(Assignment, TrainingClass)
        .join(TrainingClass, Assignment.class_id == TrainingClass.id)
        .order_by(Assignment.due_at.is_(None), Assignment.due_at, Assignment.id.desc())
    )
    if roles.intersection(ADMIN_ROLES + ("admissions", "accountant")):
        pass
    elif roles.intersection(("teacher", "ta")):
        query = query.where(
            or_(
                TrainingClass.teacher_id == current_user.id,
                TrainingClass.assistant_id == current_user.id,
            )
        )
    elif "student" in roles:
        query = query.join(ClassEnrollment).where(
            ClassEnrollment.student_id == current_user.id
        )
    else:
        return []

    return [
        {
            "id": assignment.id,
            "class_id": training_class.id,
            "class_code": training_class.code,
            "class_name": training_class.name,
            "course_name": training_class.course.name,
            "title": assignment.title,
            "description": assignment.description,
            "due_at": assignment.due_at.isoformat() if assignment.due_at else None,
            "created_at": assignment.created_at.isoformat(),
        }
        for assignment, training_class in db.execute(query).all()
    ]


@router.post(
    "/classes/{class_id}/assignments", status_code=status.HTTP_201_CREATED
)
def create_assignment(
    class_id: int,
    request: AssignmentCreate,
    current_user: User = Depends(
        require_roles("admin", "manager", "teacher", "ta")
    ),
    db: Session = Depends(get_db),
) -> dict:
    training_class = db.scalar(
        select(TrainingClass)
        .options(joinedload(TrainingClass.course))
        .where(TrainingClass.id == class_id)
    )
    if training_class is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy lớp học.")
    _assert_class_staff_access(current_user, training_class)

    assignment = Assignment(
        class_id=class_id,
        created_by=current_user.id,
        **request.model_dump(),
    )
    db.add(assignment)
    db.commit()
    db.refresh(assignment)
    return {
        "id": assignment.id,
        "class_id": training_class.id,
        "class_code": training_class.code,
        "class_name": training_class.name,
        "course_name": training_class.course.name,
        "title": assignment.title,
        "description": assignment.description,
        "due_at": assignment.due_at.isoformat() if assignment.due_at else None,
        "created_at": assignment.created_at.isoformat(),
    }


@router.get("/notifications")
def list_notifications(
    limit: int = Query(default=20, ge=1, le=50),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    notifications = db.scalars(
        select(Notification)
        .where(Notification.user_id == current_user.id)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(limit)
    ).all()
    unread_count = db.scalar(
        select(func.count(Notification.id)).where(
            Notification.user_id == current_user.id,
            Notification.read_at.is_(None),
        )
    ) or 0
    return {
        "unread_count": unread_count,
        "items": [
            {
                "id": item.id,
                "class_id": item.class_id,
                "lead_id": item.lead_id,
                "title": item.title,
                "message": item.message,
                "scheduled_for": (
                    f"{item.scheduled_for.isoformat()}Z" if item.scheduled_for else None
                ),
                "is_read": item.read_at is not None,
                "created_at": item.created_at.isoformat(),
            }
            for item in notifications
        ],
    }


@router.post("/notifications/{notification_id}/read")
def mark_notification_read(
    notification_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    notification = db.scalar(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == current_user.id,
        )
    )
    if notification is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy thông báo.")
    if notification.read_at is None:
        notification.read_at = datetime.now(timezone.utc)
        db.commit()
    return {"id": notification.id, "is_read": True}
