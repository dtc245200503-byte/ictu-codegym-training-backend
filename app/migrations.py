from sqlalchemy import inspect, text


def ensure_lead_care_schema(engine):
    """Preserve existing leads; add a nullable callback and its lookup index."""
    from app.models.lead_care import LeadCareLog
    columns = {column["name"] for column in inspect(engine).get_columns("consultation_leads")}
    if "next_callback_at" not in columns:
        sql_type = "DATETIME(6)" if engine.dialect.name == "mysql" else "DATETIME"
        with engine.begin() as connection:
            connection.execute(text(f"ALTER TABLE consultation_leads ADD COLUMN next_callback_at {sql_type} NULL"))
    indexes = inspect(engine).get_indexes("consultation_leads")
    if not any(item["column_names"] == ["next_callback_at"] for item in indexes):
        with engine.begin() as connection:
            connection.execute(text("CREATE INDEX ix_consultation_leads_next_callback_at ON consultation_leads (next_callback_at)"))
    LeadCareLog.__table__.create(bind=engine, checkfirst=True)


def ensure_class_assignment_schema(engine):
    """Add subject-level assignments; migrate legacy class teachers only once."""
    from app.models.subject import ClassSubjectAssistant
    columns = {column["name"] for column in inspect(engine).get_columns("class_subjects")}
    if "instructor_id" not in columns:
        with engine.begin() as connection:
            if engine.dialect.name == "mysql":
                connection.execute(text("ALTER TABLE class_subjects ADD COLUMN instructor_id INT NULL, "
                    "ADD CONSTRAINT fk_class_subject_instructor FOREIGN KEY (instructor_id) REFERENCES users(user_id)"))
            else:
                connection.execute(text("ALTER TABLE class_subjects ADD COLUMN instructor_id INTEGER NULL REFERENCES users(user_id)"))
            connection.execute(text("""UPDATE class_subjects SET instructor_id = (
                SELECT c.instructor_id FROM classes c WHERE c.class_id = class_subjects.class_id
                AND EXISTS (SELECT 1 FROM user_roles ur JOIN roles r ON r.role_id = ur.role_id
                            WHERE ur.user_id = c.instructor_id AND r.role_name IN ('INSTRUCTOR', 'TEACHER'))
            )"""))
    ClassSubjectAssistant.__table__.create(bind=engine, checkfirst=True)


def seed_class_permissions_and_roles():
    """Initialize reference data without creating accounts or restoring revoked grants."""
    from app.database import SessionLocal
    from app.models.permission import Permission
    from app.models.role import Role
    from app.models.role_permission import RolePermission
    with SessionLocal() as db:
        if not db.query(Role).first():
            return  # Fresh database role setup is handled by the existing initializer.
        if not db.query(Role).filter(Role.role_name.in_(["ASSISTANT", "TA"])).first():
            maximum = db.query(Role.role_id).order_by(Role.role_id.desc()).first()[0]
            db.add(Role(role_id=maximum + 1, role_name="ASSISTANT"))
        if not db.query(Permission).filter_by(permission_name="CLASS_MANAGE").first():
            permission = Permission(permission_name="CLASS_MANAGE", description="Quản lý lớp học và phân công giảng dạy")
            db.add(permission)
            db.flush()
            for role in db.query(Role).filter(Role.role_name.in_(["ADMIN", "TRAINING_MANAGER", "MANAGER", "ACADEMIC_AFFAIRS"])):
                db.add(RolePermission(role_id=role.role_id, permission_id=permission.permission_id))
        db.commit()


def ensure_profile_columns(engine):
    """Add nullable profile/avatar columns without changing existing rows."""
    columns = {column["name"] for column in inspect(engine).get_columns("users")}
    with engine.begin() as connection:
        for name, sql_type in (
            ("date_of_birth", "DATE"), ("address", "VARCHAR(255)"),
            ("avatar_url", "TEXT"), ("avatar_thumbnail_url", "TEXT"),
        ):
            if name not in columns:
                connection.execute(text(f"ALTER TABLE users ADD COLUMN {name} {sql_type} NULL"))


def ensure_training_program_columns(engine):
    """Extend the existing program/class tables; preserve all rows and links."""
    columns = {column["name"] for column in inspect(engine).get_columns("courses")}
    with engine.begin() as connection:
        for name, sql_type in (
            ("code", "VARCHAR(50) NULL"),
            ("total_duration_hours", "DECIMAL(8,2) NOT NULL DEFAULT 0"),
            ("standard_tuition", "DECIMAL(12,0) NOT NULL DEFAULT 0"),
            ("status", "VARCHAR(20) NOT NULL DEFAULT 'active'"),
            ("deleted_at", "DATETIME NULL"),
        ):
            if name not in columns:
                connection.execute(text(f"ALTER TABLE courses ADD COLUMN {name} {sql_type}"))
        # Backfill only missing codes; never invent duration/fees for existing rows.
        missing = connection.execute(text("SELECT course_id FROM courses WHERE code IS NULL")).scalars().all()
        for course_id in missing:
            code = f"CT-{course_id}"
            while connection.execute(text("SELECT 1 FROM courses WHERE code = :code"), {"code": code}).first():
                code += "-CU"
            connection.execute(text("UPDATE courses SET code = :code WHERE course_id = :id"),
                               {"code": code, "id": course_id})
    indexes = inspect(engine).get_indexes("courses")
    uniques = inspect(engine).get_unique_constraints("courses")
    if not any(index.get("unique", True) and index["column_names"] == ["code"] for index in indexes + uniques):
        with engine.begin() as connection:
            connection.execute(text("CREATE UNIQUE INDEX uq_courses_program_code ON courses (code)"))
    class_columns = {column["name"] for column in inspect(engine).get_columns("classes")}
    if "status" not in class_columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE classes ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'in_progress'"))


def ensure_subject_columns(engine):
    columns = {column["name"] for column in inspect(engine).get_columns("subjects")}
    with engine.begin() as connection:
        for name, sql_type in (("code", "VARCHAR(50) NULL"), ("session_count", "INTEGER NOT NULL DEFAULT 1"),
                               ("weight", "DECIMAL(6,2) NOT NULL DEFAULT 1"), ("deleted_at", "DATETIME NULL")):
            if name not in columns:
                connection.execute(text(f"ALTER TABLE subjects ADD COLUMN {name} {sql_type}"))
        for subject_id in connection.execute(text("SELECT id FROM subjects WHERE code IS NULL")).scalars().all():
            code = f"MH-{subject_id}"
            while connection.execute(text("SELECT 1 FROM subjects WHERE code = :code"), {"code": code}).first():
                code += "-CU"
            connection.execute(text("UPDATE subjects SET code = :code WHERE id = :id"), {"code": code, "id": subject_id})
    indexes = inspect(engine).get_indexes("subjects") + inspect(engine).get_unique_constraints("subjects")
    if not any(item.get("unique", True) and item["column_names"] == ["code"] for item in indexes):
        with engine.begin() as connection:
            connection.execute(text("CREATE UNIQUE INDEX uq_subject_code ON subjects (code)"))
    with engine.begin() as connection:
        connection.execute(text("""INSERT INTO class_subjects (class_id, subject_id)
            SELECT DISTINCT c.class_id, cs.subject_id FROM classes c
            JOIN curriculum_subjects cs ON cs.curriculum_id = c.course_id
            JOIN subjects s ON s.id = cs.subject_id
            WHERE NOT EXISTS (SELECT 1 FROM class_subjects old
                              WHERE old.class_id = c.class_id AND old.subject_id = cs.subject_id)"""))


def seed_subject_permission():
    from app.database import SessionLocal
    from app.models.permission import Permission
    from app.models.role import Role
    from app.models.role_permission import RolePermission
    with SessionLocal() as db:
        roles = db.query(Role).filter(Role.role_name.in_(["ADMIN", "TRAINING_MANAGER", "MANAGER"])).all()
        if not roles or db.query(Permission).filter_by(permission_name="SUBJECT_MANAGE").first():
            return
        permission = Permission(permission_name="SUBJECT_MANAGE", description="Quản lý danh mục môn học")
        db.add(permission); db.flush()
        for role in roles:
            db.add(RolePermission(role_id=role.role_id, permission_id=permission.permission_id))
        db.commit()


def ensure_subject_columns(engine):
    columns = {column["name"] for column in inspect(engine).get_columns("subjects")}
    with engine.begin() as connection:
        for name, sql_type in (("code", "VARCHAR(50) NULL"), ("session_count", "INTEGER NOT NULL DEFAULT 1"),
                               ("weight", "DECIMAL(6,2) NOT NULL DEFAULT 1"), ("deleted_at", "DATETIME NULL")):
            if name not in columns:
                connection.execute(text(f"ALTER TABLE subjects ADD COLUMN {name} {sql_type}"))
        for subject_id in connection.execute(text("SELECT id FROM subjects WHERE code IS NULL")).scalars().all():
            code = f"MH-{subject_id}"
            while connection.execute(text("SELECT 1 FROM subjects WHERE code = :code"), {"code": code}).first():
                code += "-CU"
            connection.execute(text("UPDATE subjects SET code = :code WHERE id = :id"), {"code": code, "id": subject_id})
    indexes = inspect(engine).get_indexes("subjects") + inspect(engine).get_unique_constraints("subjects")
    if not any(item.get("unique", True) and item["column_names"] == ["code"] for item in indexes):
        with engine.begin() as connection:
            connection.execute(text("CREATE UNIQUE INDEX uq_subject_code ON subjects (code)"))
    with engine.begin() as connection:
        connection.execute(text("""INSERT INTO class_subjects (class_id, subject_id)
            SELECT DISTINCT c.class_id, cs.subject_id FROM classes c
            JOIN curriculum_subjects cs ON cs.curriculum_id = c.course_id
            JOIN subjects s ON s.id = cs.subject_id
            WHERE NOT EXISTS (SELECT 1 FROM class_subjects old
                              WHERE old.class_id = c.class_id AND old.subject_id = cs.subject_id)"""))


def seed_subject_permission():
    from app.database import SessionLocal
    from app.models.permission import Permission
    from app.models.role import Role
    from app.models.role_permission import RolePermission
    with SessionLocal() as db:
        roles = db.query(Role).filter(Role.role_name.in_(["ADMIN", "TRAINING_MANAGER", "MANAGER"])).all()
        if not roles or db.query(Permission).filter_by(permission_name="SUBJECT_MANAGE").first():
            return
        permission = Permission(permission_name="SUBJECT_MANAGE", description="Quản lý danh mục môn học")
        db.add(permission); db.flush()
        for role in roles:
            db.add(RolePermission(role_id=role.role_id, permission_id=permission.permission_id))
        db.commit()


def ensure_lead_assignment_columns(engine):
    columns = {column["name"] for column in inspect(engine).get_columns("consultation_leads")}
    if "assignee_id" not in columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE consultation_leads ADD COLUMN assignee_id INTEGER NULL REFERENCES users(user_id)"))
            connection.execute(text("CREATE INDEX ix_consultation_leads_assignee_id ON consultation_leads (assignee_id)"))
            if engine.dialect.name == "mysql":
                connection.execute(text("ALTER TABLE consultation_leads ADD CONSTRAINT fk_lead_assignee FOREIGN KEY (assignee_id) REFERENCES users(user_id)"))


def ensure_consultation_timestamp_precision(engine):
    if engine.dialect.name != "mysql":
        return
    tables = {"consultation_leads": ["created_at"], "consultation_challenges": ["issued_at", "expires_at"],
              "consultation_rate_buckets": ["window_start"]}
    for table, names in tables.items():
        columns = {column["name"]: column for column in inspect(engine).get_columns(table)}
        with engine.begin() as connection:
            for name in names:
                if getattr(columns[name]["type"], "fsp", None) != 6:
                    connection.execute(text(f"ALTER TABLE {table} MODIFY COLUMN {name} DATETIME(6) NOT NULL"))


def seed_training_program_permission():
    from app.database import SessionLocal
    from app.models.permission import Permission
    from app.models.role import Role
    from app.models.role_permission import RolePermission

    with SessionLocal() as db:
        roles = db.query(Role).filter(Role.role_name.in_(["ADMIN", "TRAINING_MANAGER", "MANAGER"])).all()
        if not roles:
            return
        # Seed once; later changes through role management remain authoritative.
        permission = db.query(Permission).filter_by(permission_name="CURRICULUM_MANAGE").first()
        if permission is not None:
            return
        permission = Permission(permission_name="CURRICULUM_MANAGE", description="Quản lý chương trình đào tạo")
        db.add(permission)
        db.flush()
        for role in roles:
            db.add(RolePermission(role_id=role.role_id, permission_id=permission.permission_id))
        db.commit()


def ensure_lead_columns(engine):
    columns = {column["name"] for column in inspect(engine).get_columns("consultation_leads")}
    timestamp_type = "DATETIME(6)" if engine.dialect.name == "mysql" else "DATETIME"
    with engine.begin() as connection:
        if "source" not in columns:
            connection.execute(text("ALTER TABLE consultation_leads ADD COLUMN source VARCHAR(100) NOT NULL DEFAULT 'Biểu mẫu công khai'"))
        if "deleted_at" not in columns:
            connection.execute(text(f"ALTER TABLE consultation_leads ADD COLUMN deleted_at {timestamp_type} NULL"))


def seed_lead_permission():
    from app.database import SessionLocal
    from app.models.permission import Permission
    from app.models.role import Role
    from app.models.role_permission import RolePermission
    with SessionLocal() as db:
        roles = db.query(Role).filter(Role.role_name.in_(["ADMIN", "TRAINING_MANAGER", "ADMISSIONS"])).all()
        if not roles or db.query(Permission).filter_by(permission_name="LEAD_MANAGE").first():
            return
        permission = Permission(permission_name="LEAD_MANAGE", description="Xem, tạo và sửa khách hàng tiềm năng")
        db.add(permission); db.flush()
        for role in roles:
            db.add(RolePermission(role_id=role.role_id, permission_id=permission.permission_id))
        db.commit()


def ensure_lead_columns(engine):
    columns = {column["name"] for column in inspect(engine).get_columns("consultation_leads")}
    timestamp_type = "DATETIME(6)" if engine.dialect.name == "mysql" else "DATETIME"
    with engine.begin() as connection:
        if "source" not in columns:
            connection.execute(text("ALTER TABLE consultation_leads ADD COLUMN source VARCHAR(100) NOT NULL DEFAULT 'Biểu mẫu công khai'"))
        if "deleted_at" not in columns:
            connection.execute(text(f"ALTER TABLE consultation_leads ADD COLUMN deleted_at {timestamp_type} NULL"))


def seed_lead_permission():
    from app.database import SessionLocal
    from app.models.permission import Permission
    from app.models.role import Role
    from app.models.role_permission import RolePermission
    with SessionLocal() as db:
        roles = db.query(Role).filter(Role.role_name.in_(["ADMIN", "TRAINING_MANAGER", "ADMISSIONS"])).all()
        if not roles or db.query(Permission).filter_by(permission_name="LEAD_MANAGE").first():
            return
        permission = Permission(permission_name="LEAD_MANAGE", description="Xem, tạo và sửa khách hàng tiềm năng")
        db.add(permission); db.flush()
        for role in roles:
            db.add(RolePermission(role_id=role.role_id, permission_id=permission.permission_id))
        db.commit()


def seed_attendance_permissions():
    """Seed missing permissions only; never restore a revoked role grant."""
    from app.database import SessionLocal
    from app.models.permission import Permission
    from app.models.role import Role
    from app.models.role_permission import RolePermission
    with SessionLocal() as db:
        roles = db.query(Role).filter(Role.role_name.in_(["ADMIN", "TRAINING_MANAGER", "MANAGER", "INSTRUCTOR", "TEACHER"])).all()
        if not roles:
            return
        for name, description in [("ATTENDANCE_VIEW", "Xem điểm danh"), ("ATTENDANCE_EDIT", "Cập nhật điểm danh")]:
            if db.query(Permission).filter_by(permission_name=name).first():
                continue
            permission = Permission(permission_name=name, description=description)
            db.add(permission)
            db.flush()
            for role in roles:
                db.add(RolePermission(role_id=role.role_id, permission_id=permission.permission_id))
        db.commit()


def seed_student_management_permission():
    from app.database import SessionLocal
    from app.models.permission import Permission
    from app.models.role import Role
    from app.models.role_permission import RolePermission
    with SessionLocal() as db:
        roles = db.query(Role).filter(Role.role_name.in_(["TRAINING_MANAGER", "MANAGER"])).all()
        if not roles or db.query(Permission).filter_by(permission_name="STUDENT_MANAGE").first():
            return
        permission = Permission(permission_name="STUDENT_MANAGE", description="Quản lý hồ sơ học tập: bảo lưu, chuyển lớp, thôi học")
        db.add(permission)
        db.flush()
        for role in roles:
            db.add(RolePermission(role_id=role.role_id, permission_id=permission.permission_id))
        db.commit()


def ensure_timetable_columns(engine):
    columns = {column["name"] for column in inspect(engine).get_columns("class_sessions")}
    with engine.begin() as connection:
        if "end_time" not in columns:
            connection.execute(text("ALTER TABLE class_sessions ADD COLUMN end_time TIME NULL"))
        if "room_name" not in columns:
            connection.execute(text("ALTER TABLE class_sessions ADD COLUMN room_name VARCHAR(100) NULL"))


def ensure_session_change_columns(engine):
    columns = {column["name"] for column in inspect(engine).get_columns("class_sessions")}
    additions = {
        "substitute_instructor_id": "INTEGER NULL",
        "status": "VARCHAR(20) NOT NULL DEFAULT 'scheduled'",
        "cancellation_reason": "VARCHAR(1000) NULL",
        "schedule_version": "INTEGER NOT NULL DEFAULT 0",
    }
    with engine.begin() as connection:
        for name, definition in additions.items():
            if name not in columns:
                connection.execute(text(f"ALTER TABLE class_sessions ADD COLUMN {name} {definition}"))


def ensure_generated_session_columns(engine):
    columns = {column["name"] for column in inspect(engine).get_columns("class_sessions")}
    with engine.begin() as connection:
        for name, definition in {"lesson_id": "INTEGER NULL", "lesson_sequence": "INTEGER NULL", "lesson_objectives": "TEXT NULL"}.items():
            if name not in columns:
                connection.execute(text(f"ALTER TABLE class_sessions ADD COLUMN {name} {definition}"))


def ensure_session_room_column(engine):
    columns = {column["name"] for column in inspect(engine).get_columns("class_sessions")}
    if "room_id" not in columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE class_sessions ADD COLUMN room_id INTEGER NULL"))


def ensure_class_capacity_column(engine):
    columns = {column["name"] for column in inspect(engine).get_columns("classes")}
    if "max_students" not in columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE classes ADD COLUMN max_students INTEGER NULL"))


def ensure_absence_prefilled_column(engine):
    columns = {column['name'] for column in inspect(engine).get_columns('class_sessions')}
    if 'absence_prefilled' not in columns:
        with engine.begin() as connection:
            connection.execute(text('ALTER TABLE class_sessions ADD COLUMN absence_prefilled BOOLEAN NOT NULL DEFAULT 0'))


def ensure_class_opening_schema(engine):
    """Add opening fields without changing the lifecycle of existing classes."""
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "classes" not in tables:
        return
    columns = {item["name"] for item in inspector.get_columns("classes")}
    definitions = {"class_code": "VARCHAR(50) NULL", "learning_mode": "VARCHAR(20) NULL",
                   "curriculum_snapshot": "BOOLEAN NOT NULL DEFAULT 0"}
    with engine.begin() as connection:
        for name, definition in definitions.items():
            if name not in columns:
                connection.execute(text(f"ALTER TABLE classes ADD COLUMN {name} {definition}"))
        rows = connection.execute(text("SELECT class_id, class_code FROM classes ORDER BY class_id")).all()
        used = {code.upper() for _, code in rows if code}
        for class_id, code in rows:
            if code:
                continue
            candidate = f"LOP-{class_id}"
            while candidate.upper() in used:
                candidate += "-CU"
            used.add(candidate.upper())
            connection.execute(text("UPDATE classes SET class_code=:code WHERE class_id=:id"), {"code": candidate, "id": class_id})
    inspector = inspect(engine)
    indexes = inspector.get_indexes("classes") + inspector.get_unique_constraints("classes")
    if not any(item.get("column_names") == ["class_code"] and item.get("unique", True) for item in indexes):
        with engine.begin() as connection:
            connection.execute(text("CREATE UNIQUE INDEX uq_classes_class_code ON classes (class_code)"))
    if "class_subjects" in tables:
        columns = {item["name"] for item in inspector.get_columns("class_subjects")}
        with engine.begin() as connection:
            for name in ["sequence_order", "prerequisite_subject_id"]:
                if name not in columns:
                    connection.execute(text(f"ALTER TABLE class_subjects ADD COLUMN {name} INTEGER NULL"))
