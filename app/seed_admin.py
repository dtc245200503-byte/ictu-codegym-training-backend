import os
import re
import sys

from sqlalchemy import select

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.role import Role
from app.models.user import User


def main() -> int:
    email = os.getenv("INITIAL_ADMIN_EMAIL", "").strip().lower()
    username = os.getenv("INITIAL_ADMIN_USERNAME", "").strip()
    full_name = os.getenv("INITIAL_ADMIN_NAME", "").strip()
    password = os.getenv("INITIAL_ADMIN_PASSWORD", "")

    if not all((email, username, full_name, password)):
        print(
            "Set INITIAL_ADMIN_EMAIL, INITIAL_ADMIN_USERNAME, "
            "INITIAL_ADMIN_NAME and INITIAL_ADMIN_PASSWORD first.",
            file=sys.stderr,
        )
        return 2
    if (
        len(password) < 8
        or not re.search(r"[A-Za-z]", password)
        or not re.search(r"\d", password)
        or not re.search(r"[^A-Za-z0-9]", password)
    ):
        print(
            "INITIAL_ADMIN_PASSWORD must be at least 8 characters and include "
            "a letter, a number and a special character.",
            file=sys.stderr,
        )
        return 2

    with SessionLocal() as db:
        if db.scalar(select(User.id).where(User.email == email)):
            print("An account with INITIAL_ADMIN_EMAIL already exists.", file=sys.stderr)
            return 1
        role = db.scalar(select(Role).where(Role.code == "admin"))
        if role is None:
            print("Run `alembic upgrade head` before creating the administrator.", file=sys.stderr)
            return 1
        admin = User(
            username=username,
            email=email,
            full_name=full_name,
            hashed_password=hash_password(password),
            must_change_password=True,
            roles=[role],
        )
        db.add(admin)
        db.commit()
    print(f"Administrator account created for {email}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
