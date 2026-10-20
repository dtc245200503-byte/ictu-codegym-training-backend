from app.attendance_risk_worker import lifespan as risk_lifespan
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.database import engine, Base
from app.migrations import ensure_consultation_timestamp_precision
from app.migrations import ensure_lead_columns, seed_lead_permission
from app.migrations import ensure_lead_assignment_columns, ensure_lead_care_schema
from app.migrations import ensure_profile_columns, ensure_training_program_columns, seed_training_program_permission
import app.models  # Import toàn bộ models để SQLAlchemy nhận diện các định nghĩa bảng

from app.routers.auth import router as auth_router
from app.routers.rbac import router as rbac_router
from app.routers.user_roles import router as user_roles_router
from app.routers.accounts import router as accounts_router
from app.routers.users import router as users_router
from app.routers.me import router as me_router
from app.routers.consultations import router as consultations_router
from app.routers.leads import router as leads_router
from app.routers.lead_care import router as lead_care_router
from app.routers.classes import router as classes_router
from app.routers.subject_lessons import router as subject_lessons_router
from app.routers.role_permissions import router as role_permissions_router
from app.routers import curriculum
from app.routers.training_programs import router as training_program_router
from app.routers.subjects import router as subject_router
from app.migrations import (
    ensure_subject_columns, seed_subject_permission,
    ensure_class_assignment_schema, seed_class_permissions_and_roles
)


# 1. Tự động khởi tạo/tạo các bảng chưa có trong Database (bao gồm bảng subjects, class_subjects, etc.)
Base.metadata.create_all(bind=engine)

ensure_consultation_timestamp_precision(engine)
ensure_lead_columns(engine)
ensure_lead_assignment_columns(engine)
ensure_lead_care_schema(engine)
seed_lead_permission()
ensure_profile_columns(engine)
ensure_training_program_columns(engine)
seed_training_program_permission()
ensure_subject_columns(engine)
seed_subject_permission()
ensure_class_assignment_schema(engine)
seed_class_permissions_and_roles()





# 2. Khởi tạo FastAPI application
app = FastAPI(
    lifespan=risk_lifespan,
    title="Training Management System API",
    version="1.0.0"
)

# 3. Cấu hình CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://192.168.2.7:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After"],
)

# 4. Đăng ký tất cả các Routers vào app
app.include_router(auth_router)
app.include_router(rbac_router)
app.include_router(user_roles_router)
app.include_router(accounts_router)
app.include_router(users_router)
app.include_router(me_router)
app.include_router(consultations_router)
app.include_router(leads_router)
app.include_router(lead_care_router)
app.include_router(classes_router)
app.include_router(subject_lessons_router)
app.include_router(role_permissions_router)
app.include_router(curriculum.router)  # Router chương trình đào tạo
app.include_router(training_program_router)
app.include_router(subject_router)
app.include_router(subject_router)


@app.get("/health")
def health_check():
    return {
        "status": "ok"
    }


@app.get("/health/db")
def database_health_check():
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))

    return {
        "database": "connected"
    }

from app.attendance_summary_setup import ensure_attendance_summary_schema, seed_attendance_summary_permission
ensure_attendance_summary_schema(engine)
seed_attendance_summary_permission()
from app.routers.attendance_summary import router as attendance_summary_router
app.include_router(attendance_summary_router)

from app.migrations import seed_attendance_permissions, seed_student_management_permission, ensure_timetable_columns, ensure_session_change_columns, ensure_generated_session_columns, ensure_session_room_column, ensure_class_capacity_column, ensure_absence_prefilled_column, ensure_class_opening_schema
seed_attendance_permissions()
seed_student_management_permission()
ensure_timetable_columns(engine)
ensure_session_change_columns(engine)
ensure_generated_session_columns(engine)
ensure_session_room_column(engine)
ensure_class_capacity_column(engine)
ensure_absence_prefilled_column(engine)
ensure_class_opening_schema(engine)
from app.routers.attendance import router as attendance_router
app.include_router(attendance_router)
from app.routers.student_enrollments import router as student_enrollments_router
app.include_router(student_enrollments_router)
from app.routers.notifications import router as notifications_router
app.include_router(notifications_router)
from app.routers.attendance_risk import router as attendance_risk_router
app.include_router(attendance_risk_router)
from app.routers.rooms import router as rooms_router
app.include_router(rooms_router)
