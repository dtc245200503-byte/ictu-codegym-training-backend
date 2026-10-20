from uuid import uuid4
from fastapi import HTTPException
from app.crud import attendance as clock
from app.crud.leads import user_roles
from app.models.attendance import StudentClass
from app.models.student_enrollment import StudentEnrollment
from app.models.class_enrollment import ClassEnrollmentRecord
from app.models.training_program import TrainingClass
from app.models.user import User
from app.models.role import Role
from app.models.user_role import UserRole


def lock_classes(db, class_ids):
    # Actual writes serialize capacity checks on SQLite as well as MySQL.
    # Lock in the same order as transfers; the transaction holds locks until commit.
    for class_id in sorted(set(class_ids)):
        db.query(TrainingClass).filter_by(class_id=class_id).update(
            {TrainingClass.class_name: TrainingClass.class_name}, synchronize_session=False)


def membership(db, class_id, *, lock=False):
    periods = db.query(StudentEnrollment).filter_by(class_id=class_id).populate_existing()
    legacy = db.query(StudentClass).filter_by(class_id=class_id).populate_existing()
    if lock:
        periods, legacy = periods.with_for_update(), legacy.with_for_update()
    periods, legacy = periods.all(), legacy.all()
    managed = {row.student_id for row in periods}
    intervals = [(row.student_id, row.started_on, row.ended_on) for row in periods]
    intervals += [(row.student_id, row.enrolled_date, None) for row in legacy if row.student_id not in managed]
    # Any earlier decision must be handled through the student profile workflow.
    blocked = {row.student_id: 'Đã có quá trình học tại lớp; xem hồ sơ để xử lý.' for row in periods}
    blocked.update({row.student_id: 'Học viên đã ở trong lớp.' for row in legacy if row.student_id not in managed})
    for row in periods:
        if row.ended_on is None or row.ended_on > clock.today():
            blocked[row.student_id] = 'Học viên đã ở trong lớp hoặc đã có lịch chuyển vào lớp.'
    return intervals, blocked


def occupied(intervals, day):
    return len({student for student, start, end in intervals if (start is None or start <= day) and (end is None or day < end)})


def capacity(db, classroom, day, *, lock=False):
    intervals, blocked = membership(db, classroom.class_id, lock=lock)
    days = {day} | {start for _, start, _ in intervals if start is not None and start > day}
    peak = max(occupied(intervals, value) for value in days)
    return {'current_students': occupied(intervals, day), 'reserved_students': peak,
            'max_students': classroom.max_students,
            'available_seats': None if classroom.max_students is None else max(0, classroom.max_students - peak)}, blocked


def ensure_capacity(db, classroom, day, count):
    info, _ = capacity(db, classroom, day, lock=True)
    if info['available_seats'] is not None and count > info['available_seats']:
        raise HTTPException(409, f"Lớp {classroom.class_name} chỉ còn {info['available_seats']} chỗ (đã tính lịch chuyển lớp). Không thể ghi danh thêm {count} học viên.")


def enroll(db, class_id, student_ids, user):
    if not user_roles(db, user) & {'TRAINING_MANAGER', 'MANAGER'}:
        raise HTTPException(403, 'Chỉ Quản lý đào tạo được ghi danh học viên.')
    try:
        lock_classes(db, [class_id])
        classroom = db.query(TrainingClass).filter_by(class_id=class_id).populate_existing().with_for_update().first()
        if classroom is None:
            raise HTTPException(404, 'Không tìm thấy lớp học.')
        if classroom.status not in {'planned', 'in_progress'}:
            raise HTTPException(409, 'Chỉ ghi danh vào lớp đang học hoặc sắp khai giảng.')
        students = db.query(User).join(UserRole, UserRole.user_id == User.user_id).join(Role, Role.role_id == UserRole.role_id).filter(
            Role.role_name == 'STUDENT', User.user_id.in_(student_ids)).order_by(User.user_id).populate_existing().with_for_update().all()
        if {row.user_id for row in students} != set(student_ids):
            raise HTTPException(422, 'Có tài khoản không tồn tại hoặc không phải học viên.')
        day, now = clock.today(), clock.utcnow()
        info, blocked = capacity(db, classroom, day, lock=True)
        duplicates = [row for row in students if row.user_id in blocked]
        if duplicates:
            names = ', '.join(f'{row.full_name} ({row.username})' for row in duplicates[:10])
            raise HTTPException(409, f'Không thể ghi danh: {names} đã có quá trình học tại lớp. Vui lòng bỏ chọn hoặc xử lý trong hồ sơ học viên.')
        if info['available_seats'] is not None and len(students) > info['available_seats']:
            raise HTTPException(409, f"Lớp chỉ còn {info['available_seats']} chỗ (đã tính lịch chuyển lớp), nhưng đã chọn {len(students)} học viên. Chưa ghi danh học viên nào.")
        batch_id = str(uuid4())
        for student in students:
            period = StudentEnrollment(student_id=student.user_id, class_id=class_id, started_on=day, origin='enrolled')
            db.add(period)
            db.flush()
            db.add(StudentClass(student_id=student.user_id, class_id=class_id, enrolled_date=day))
            db.add(ClassEnrollmentRecord(batch_id=batch_id, enrollment_id=period.enrollment_id,
                student_id=student.user_id, student_name=student.full_name, student_code=student.username,
                class_id=class_id, class_name=classroom.class_name, enrolled_on=day,
                actor_id=user.user_id, actor_name=user.full_name, created_at=now))
        db.commit()
        return {'added': len(students), 'batch_id': batch_id, 'enrolled_on': day, 'actor_name': user.full_name}
    except Exception:
        db.rollback()
        raise
