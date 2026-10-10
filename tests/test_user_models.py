from sqlalchemy.orm import configure_mappers
from app.db.session import Base
from app.models import Permission, Role, User
def test_all_user_and_permission_tables_are_registered():
    expected_tables = {
        "users",
        "roles",
        "permissions",
        "user_roles",
        "role_permissions",
    }
    assert expected_tables.issubset(Base.metadata.tables.keys())
def test_user_has_required_account_columns():
    columns = User.__table__.columns
    assert "username" in columns
    assert "email" in columns
    assert "hashed_password" in columns
    assert "is_active" in columns
    assert "is_locked" in columns
def test_username_and_email_are_unique():
    assert User.__table__.c.username.unique is True
    assert User.__table__.c.email.unique is True
def test_role_and_permission_codes_are_unique():
    assert Role.__table__.c.code.unique is True
    assert Permission.__table__.c.code.unique is True
def test_orm_relationships_are_valid():
    configure_mappers()
    assert hasattr(User, "roles")
    assert hasattr(Role, "users")
    assert hasattr(Role, "permissions")
    assert hasattr(Permission, "roles")
def test_association_tables_have_composite_primary_keys():
    user_roles = Base.metadata.tables["user_roles"]
    role_permissions = Base.metadata.tables["role_permissions"]
    assert len(user_roles.primary_key.columns) == 2
    assert len(role_permissions.primary_key.columns) == 2
