"""Add Vietnamese demo records once, without overwriting existing or edited data.

Run from the backend: .venv/Scripts/python.exe -X utf8 scripts/seed_demo_data.py
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.database import SessionLocal
from app.models.user import User
from app.models.role import Role
from app.models.user_role import UserRole
from app.models.subject import Subject, ClassSubject
from app.models.subject_lesson import SubjectLesson
from app.models.training_program import TrainingProgram, TrainingClass
from app.models.curriculum import CurriculumSubject
from app.models.consultation import ConsultationLead
from app.core.permissions import user_has_permission
from app.crud.leads import record_assignment

SUBJECTS = [
    ('DEMO-TIN', 'Tin học văn phòng', ['Quản lý tệp và thư mục', 'Soạn thảo văn bản', 'Bảng tính cơ bản', 'Trình bày báo cáo']),
    ('DEMO-PY1', 'Lập trình Python cơ bản', ['Biến và kiểu dữ liệu', 'Điều kiện và vòng lặp', 'Hàm và xử lý lỗi', 'Danh sách và từ điển', 'Đọc ghi tệp', 'Bài tập quản lý dữ liệu']),
    ('DEMO-SQL', 'Cơ sở dữ liệu SQL', ['Thiết kế bảng dữ liệu', 'Truy vấn và lọc', 'Kết nối nhiều bảng', 'Tổng hợp và thống kê']),
    ('DEMO-HTML', 'HTML và CSS', ['Cấu trúc trang web', 'Bố cục CSS', 'Giao diện đáp ứng', 'Xây dựng trang giới thiệu']),
    ('DEMO-JS', 'JavaScript căn bản', ['Biến và hàm', 'Mảng và đối tượng', 'Sự kiện và DOM', 'Gọi API bất đồng bộ', 'Kiểm tra biểu mẫu', 'Ứng dụng quản lý công việc']),
    ('DEMO-REACT', 'Phát triển giao diện React', ['Component và props', 'State và tương tác', 'Định tuyến trang', 'Kết nối backend', 'Tối ưu giao diện', 'Dự án cuối môn']),
    ('DEMO-EXCEL', 'Excel ứng dụng', ['Hàm tính toán', 'Tra cứu dữ liệu', 'PivotTable', 'Báo cáo trực quan']),
    ('DEMO-DATA', 'Phân tích dữ liệu', ['Làm sạch dữ liệu', 'Phân tích với Python', 'Biểu đồ và báo cáo', 'Trình bày kết quả']),
    ('DEMO-EN', 'Tiếng Anh giao tiếp', ['Giới thiệu bản thân', 'Hội thoại hằng ngày', 'Giao tiếp công sở', 'Thuyết trình ngắn']),
    ('DEMO-SOFT', 'Kỹ năng làm việc nhóm', ['Phân công công việc', 'Giao tiếp trong nhóm', 'Giải quyết vấn đề', 'Báo cáo tiến độ']),
]
PROGRAMS = [
    ('DEMO-PY', 'Lập trình Python thực hành', 60, 4500000, 'active', ['DEMO-TIN','DEMO-PY1','DEMO-SQL','DEMO-SOFT']),
    ('DEMO-WEB', 'Phát triển website với React', 90, 6900000, 'active', ['DEMO-HTML','DEMO-JS','DEMO-REACT','DEMO-SOFT']),
    ('DEMO-DA', 'Phân tích dữ liệu ứng dụng', 75, 5800000, 'active', ['DEMO-EXCEL','DEMO-PY1','DEMO-SQL','DEMO-DATA']),
    ('DEMO-OFFICE', 'Tin học và kỹ năng văn phòng', 36, 2400000, 'active', ['DEMO-TIN','DEMO-EXCEL','DEMO-SOFT']),
    ('DEMO-ENGLISH', 'Tiếng Anh giao tiếp cơ bản', 40, 3200000, 'inactive', ['DEMO-EN','DEMO-SOFT']),
]
CLASSES = [
    ('[Mẫu] Python P01', 'DEMO-PY', 'in_progress', -25),
    ('[Mẫu] Python P02', 'DEMO-PY', 'planned', 14),
    ('[Mẫu] React W01', 'DEMO-WEB', 'in_progress', -18),
    ('[Mẫu] Phân tích dữ liệu D01', 'DEMO-DA', 'planned', 21),
    ('[Mẫu] Văn phòng O01', 'DEMO-OFFICE', 'completed', -100),
    ('[Mẫu] Tiếng Anh E01', 'DEMO-ENGLISH', 'cancelled', -60),
]
LEAD_NAMES = ['Nguyễn Minh Anh','Trần Quốc Bảo','Lê Thu Hà','Phạm Gia Huy','Hoàng Ngọc Lan','Vũ Đức Long',
              'Đặng Mai Phương','Bùi Thành Nam','Đỗ Khánh Linh','Ngô Tuấn Kiệt','Dương Bảo Ngọc','Võ Minh Đức']


def role_users(db, role):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return db.query(User).join(UserRole, UserRole.user_id == User.user_id).join(Role, Role.role_id == UserRole.role_id).filter(
        Role.role_name == role, User.is_locked.is_(False), (User.locked_until.is_(None)) | (User.locked_until <= now)).order_by(User.user_id).all()


def seed_demo_data(db, today=None):
    today = today or datetime.now(timezone(timedelta(hours=7))).date()
    added = {key: 0 for key in ['programs','subjects','lessons','curriculum_subjects','classes','class_subjects','leads','assignments']}
    subjects, programs = {}, {}
    for code, name, topics in SUBJECTS:
        item = db.query(Subject).filter_by(code=code).first()
        if item is None:
            item = Subject(code=code, subject_name='[Mẫu] '+name, session_count=len(topics), weight=1,
                           description=f'Hoàn thành bài thực hành và vận dụng kiến thức {name.lower()} vào công việc.')
            db.add(item); db.flush(); added['subjects'] += 1
            for index, topic in enumerate(topics, 1):
                db.add(SubjectLesson(subject_id=item.id, sequence_order=index, topic=topic,
                                    objectives=f'Hiểu và thực hành {topic.lower()}; hoàn thành bài tập theo hướng dẫn.'))
                added['lessons'] += 1
        subjects[code] = item
    for code, name, hours, tuition, status, codes in PROGRAMS:
        item = db.query(TrainingProgram).filter_by(code=code).first()
        if item is None:
            item = TrainingProgram(code=code, course_name='[Mẫu] '+name, total_duration_hours=hours,
                standard_tuition=tuition, status=status, description='Chương trình mẫu để trải nghiệm quản lý đào tạo. Học lý thuyết kết hợp thực hành và dự án cuối khóa.')
            db.add(item); db.flush(); added['programs'] += 1
            previous = None
            for index, subject_code in enumerate(codes, 1):
                subject = subjects[subject_code]
                if subject.deleted_at is not None: continue
                db.add(CurriculumSubject(curriculum_id=item.course_id, subject_id=subject.id,
                    sequence_order=index, prerequisite_subject_id=previous))
                previous = subject.id; added['curriculum_subjects'] += 1
        programs[code] = item
    instructors = role_users(db, 'INSTRUCTOR')
    for index, (name, code, status, days) in enumerate(CLASSES):
        if db.query(TrainingClass).filter_by(class_name=name).first() is not None or programs[code].deleted_at is not None:
            continue
        item = TrainingClass(class_name=name, course_id=programs[code].course_id, status=status, start_date=today+timedelta(days=days),
                             instructor_id=instructors[index % len(instructors)].user_id if instructors and status != 'cancelled' else None)
        db.add(item); db.flush(); added['classes'] += 1
        # Snapshot actual membership for the class, preserving subject deletion guards.
        membership = db.query(CurriculumSubject).filter_by(curriculum_id=item.course_id).order_by(CurriculumSubject.sequence_order).all()
        for link in membership:
            db.add(ClassSubject(class_id=item.class_id, subject_id=link.subject_id, instructor_id=item.instructor_id)); added['class_subjects'] += 1
    advisors = [u for u in role_users(db, 'ADMISSIONS') if user_has_permission(u.user_id, 'LEAD_MANAGE', db)]
    managers = [u for u in role_users(db, 'TRAINING_MANAGER') if user_has_permission(u.user_id, 'LEAD_MANAGE', db)]
    sources = ['Facebook','Website','Giới thiệu','Zalo','Biểu mẫu công khai','Điện thoại']
    states = ['new','contacted','qualified','converted','closed','new']
    offsets = [0,1,2,3,7,10,14,20,25,30,35,40]
    for index, name in enumerate(LEAD_NAMES):
        email = f'demo.lead{index+1:02d}@example.com'
        # Include soft-deleted rows: rerunning does not resurrect deleted demo leads.
        if db.query(ConsultationLead).filter_by(email=email).first() is not None: continue
        phone_number = 909000101+index
        while db.query(ConsultationLead).filter(ConsultationLead.phone.in_(['0'+str(phone_number), '+84'+str(phone_number)])).first():
            phone_number += 100
        item = ConsultationLead(full_name='[Mẫu] '+name, phone='0'+str(phone_number), email=email,
            source=sources[index % len(sources)], status=states[index % len(states)],
            interest=programs[PROGRAMS[index % len(PROGRAMS)][0]].course_name,
            message='Dữ liệu mẫu để thử tìm kiếm, lọc và phân công. Mong được tư vấn lịch học và học phí.',
            created_at=datetime.combine(today-timedelta(days=offsets[index]), datetime.min.time())+timedelta(hours=5))
        db.add(item); db.flush(); added['leads'] += 1
        if index < 8 and advisors and managers:
            target = advisors[index % len(advisors)]
            item.assignee_id = target.user_id
            record_assignment(db, item, None, target, managers[0], 'Phân công dữ liệu mẫu để thử quản lý lead.')
            added['assignments'] += 1
    db.flush()
    return added


if __name__ == '__main__':
    with SessionLocal() as db:
        try:
            added = seed_demo_data(db)
            db.commit()
        except Exception:
            db.rollback()
            raise
    print(json.dumps({'added': added, 'note': 'Chỉ thêm dữ liệu mẫu; không ghi đè dữ liệu có sẵn.'}, ensure_ascii=False, indent=2))
