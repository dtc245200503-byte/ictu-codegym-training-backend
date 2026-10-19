"""Minimal read-model setup for SCRUM-57; safe alongside attendance migrations."""
from sqlalchemy import inspect, text


def ensure_attendance_summary_schema(engine):
    # create_all creates the tables on a fresh installation; older attendance
    # installations need only these two extra columns for this read-only report.
    columns = {column["name"] for column in inspect(engine).get_columns("class_sessions")}
    with engine.begin() as connection:
        if "status" not in columns:
            connection.execute(text("ALTER TABLE class_sessions ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'scheduled'"))
        if "substitute_instructor_id" not in columns:
            connection.execute(text("ALTER TABLE class_sessions ADD COLUMN substitute_instructor_id INTEGER NULL"))


def seed_attendance_summary_permission():
    from app.database import SessionLocal
    from app.models.permission import Permission
    from app.models.role import Role
    from app.models.role_permission import RolePermission
    with SessionLocal() as db:
        # Preserve an existing administrator's permission configuration.
        if db.query(Permission).filter_by(permission_name="ATTENDANCE_VIEW").first():
            return
        roles = db.query(Role).filter(Role.role_name.in_(["TRAINING_MANAGER", "MANAGER", "ADMIN", "INSTRUCTOR", "TEACHER"])).all()
        if not roles:
            return
        permission = Permission(permission_name="ATTENDANCE_VIEW", description="Xem điểm danh và bảng chuyên cần")
        db.add(permission)
        db.flush()
        db.add_all([RolePermission(role_id=role.role_id, permission_id=permission.permission_id) for role in roles])
        db.commit()
