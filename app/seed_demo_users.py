import secrets
import string

from sqlalchemy import select

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.role import Role
from app.models.user import User


DEMO_ACCOUNTS = (
    ("demo.admin", "demo.admin@ictu.edu.vn", "Portal Admin", "admin", None),
    ("demo.manager", "demo.manager@ictu.edu.vn", "Training Manager", "manager", None),
    ("demo.admissions", "demo.admissions@ictu.edu.vn", "Admissions Officer", "admissions", None),
    ("demo.accountant", "demo.accountant@ictu.edu.vn", "Portal Accountant", "accountant", None),
    ("demo.instructor", "demo.instructor@ictu.edu.vn", "Demo Instructor", "teacher", None),
    ("demo.ta", "demo.ta@ictu.edu.vn", "Demo Teaching Assistant", "ta", None),
    ("demo.student", "demo.student@ictu.edu.vn", "Demo Student", "student", "DEMO0001"),
    ("demo.guest", "demo.guest@ictu.edu.vn", "Demo Guest", "guest", None),
)


def _temporary_password() -> str:
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


def main() -> int:
    password = _temporary_password()
    created_count = 0

    with SessionLocal() as db:
        roles = {
            role.code: role
            for role in db.scalars(select(Role)).all()
        }
        for _, _, _, role_code, _ in DEMO_ACCOUNTS:
            if role_code not in roles:
                raise RuntimeError(
                    f"Missing role {role_code!r}; run `alembic upgrade head` first."
                )

        for username, email, full_name, role_code, student_code in DEMO_ACCOUNTS:
            user = db.scalar(
                select(User).where(
                    (User.username == username) | (User.email == email)
                )
            )
            if user is None:
                user = User(
                    username=username,
                    email=email,
                    full_name=full_name,
                    student_code=student_code,
                    hashed_password=hash_password(password),
                    must_change_password=role_code == "admin",
                    roles=[roles[role_code]],
                )
                db.add(user)
                created_count += 1
            else:
                if user.username != username or user.email != email:
                    raise RuntimeError(
                        f"Demo username or email is already used by another account: {username}"
                    )
                user.roles = [roles[role_code]]
        db.commit()

    print("Demo accounts:")
    for username, email, _, role_code, _ in DEMO_ACCOUNTS:
        print(f"- {role_code}: {username} ({email})")
    if created_count:
        print(f"Shared password for {created_count} newly created accounts: {password}")
    else:
        print("All demo accounts already existed; passwords were not changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
