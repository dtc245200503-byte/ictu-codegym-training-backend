def seed_assignment_permissions():
    from app.database import SessionLocal
    from app.models.permission import Permission
    from app.models.role import Role
    from app.models.role_permission import RolePermission
    with SessionLocal() as db:
        for name, description, names in [
            ("ASSIGNMENT_VIEW", "Xem bài tập được giao", ["INSTRUCTOR", "TEACHER", "STUDENT"]),
            ("ASSIGNMENT_MANAGE", "Soạn và giao bài tập cho lớp", ["INSTRUCTOR", "TEACHER"]),
        ]:
            if db.query(Permission).filter_by(permission_name=name).first():
                continue
            roles = db.query(Role).filter(Role.role_name.in_(names)).all()
            if not roles:
                continue
            item = Permission(permission_name=name, description=description)
            db.add(item)
            db.flush()
            db.add_all([RolePermission(role_id=role.role_id, permission_id=item.permission_id) for role in roles])
        db.commit()
