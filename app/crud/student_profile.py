import hashlib
import hmac
import json
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from app.core.security import SECRET_KEY
from app.crud import attendance as clock
from app.models.student_profile import StudentProfileDetails
from app.models.user import User


def personal_info(student, details):
    return {'full_name': student.full_name, 'email': student.email, 'phone': student.phone,
            'date_of_birth': student.date_of_birth, 'address': student.address,
            'entry_level': details.entry_level if details else None, 'notes': details.notes if details else None}


def profile_token(student, details):
    values = {'student_id': student.user_id, **personal_info(student, details)}
    return hmac.new(SECRET_KEY.encode(), json.dumps(values, sort_keys=True, default=str).encode(), hashlib.sha256).hexdigest()


def update_profile(db, student_id, data, actor):
    from app.routers.student_enrollments import student_or_404
    try:
        # Serialize writes, including SQLite; then read current rows on MySQL.
        db.query(User).filter_by(user_id=student_id).update({User.full_name: User.full_name}, synchronize_session=False)
        student = db.query(User).filter_by(user_id=student_id).populate_existing().with_for_update().first()
        student_or_404(db, student_id)
        details = db.query(StudentProfileDetails).filter_by(student_id=student_id).populate_existing().with_for_update().first()
        if not hmac.compare_digest(data.expected_token, profile_token(student, details)):
            raise HTTPException(412, 'Hồ sơ đã được cập nhật ở nơi khác. Đóng biểu mẫu và tải lại hồ sơ trước khi sửa.')
        if details is None:
            details = StudentProfileDetails(student_id=student_id)
            db.add(details)
        for name in ['full_name', 'email', 'phone', 'date_of_birth', 'address']:
            setattr(student, name, getattr(data, name))
        details.entry_level, details.notes = data.entry_level, data.notes
        details.updated_by_id, details.updated_by_name, details.updated_at = actor.user_id, actor.full_name, clock.utcnow()
        db.commit()
        db.refresh(student)
        return student
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, 'Email đã được tài khoản khác sử dụng. Vui lòng chọn email khác.')
    except Exception:
        db.rollback()
        raise
