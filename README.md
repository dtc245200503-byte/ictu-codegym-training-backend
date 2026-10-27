# ICTU × CodeGym — Backend

REST API for the training-management portal. Built with FastAPI, SQLAlchemy 2, Alembic and MySQL.

## Local setup

1. Create a MySQL database using `utf8mb4` and copy `.env.example` to `.env`.
2. Set a private `DATABASE_URL` and a random `JWT_SECRET_KEY` (at least 32 characters). For real password-reset email delivery, set `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_SERVER`, `MAIL_PORT` and `MAIL_FROM` in `backend/.env`; set `FRONTEND_URL` to the public frontend origin. In development with SMTP unset, the reset URL is written to the backend log. Production returns no reset link by email when SMTP is unavailable.
3. Install dependencies and apply the database migrations from this directory:

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   .\.venv\Scripts\alembic.exe upgrade head
   ```

4. Create the first administrator without committing credentials:

   ```powershell
   $env:INITIAL_ADMIN_EMAIL = "admin@ictu.edu.vn"
   $env:INITIAL_ADMIN_USERNAME = "ictu.admin"
   $env:INITIAL_ADMIN_NAME = "ICTU Administrator"
   $env:INITIAL_ADMIN_PASSWORD = "use-a-private-strong-password"
   .\.venv\Scripts\python.exe -m app.seed_admin
   ```

5. For frontend role-menu testing, create the eight idempotent demo accounts:

   ```powershell
   .\.venv\Scripts\python.exe -m app.seed_demo_users
   ```

   The command prints the account names and a randomly generated shared password for newly created accounts. Save that password securely; rerunning the command does not reset existing demo passwords. The accounts are `demo.admin`, `demo.manager`, `demo.admissions`, `demo.accountant`, `demo.instructor`, `demo.ta`, `demo.student` and `demo.guest` (all use `@ictu.edu.vn` addresses).

6. Start the API:

   ```powershell
   .\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
   ```

The API documentation is available at `http://localhost:8000/docs`. Use MySQL for development and production; tests override the database with an isolated in-memory SQLite database.

## Seed academic demo data

After applying the migrations and creating the role demo accounts, populate the two degree programs, ten prerequisite-linked courses, weekday class sections, six students, enrollments and four recent attendance sessions per class:

```powershell
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\python.exe -m app.seed_academic_data
```

The seed is repeatable and updates only its reserved program/course/class/student identifiers; existing matching enrollments and attendance sessions are updated rather than duplicated. Newly created instructor, TA or student accounts share one generated password, printed once by the command. To seed a separate SQLite database, set `DATABASE_URL` to a SQLite URL in the same PowerShell session, apply migrations, and run the same module:

```powershell
$env:DATABASE_URL = "sqlite:///./ictu_academic_demo.db"
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\python.exe -m app.seed_academic_data
```

The two program credit totals (126 each) are degree requirements; the ten subjects are a representative seed catalog, not a complete 126-credit curriculum.

## Implemented modules

- JWT login, refresh rotation, logout, password reset, change password and account lockout.
- Initial-admin seed accounts and accounts created through Admin account management or `.xlsx` import must change their temporary password on first login; account management and import use a temporary default (overridable in the form or optional `password` spreadsheet column). Protected APIs remain unavailable until the password is changed.
- Password-reset tokens expire after 30 minutes by default (`PASSWORD_RESET_TOKEN_EXPIRE_MINUTES` can override this). When SMTP is not configured, the backend logs the reset link for local development.
- Role-protected account search, pagination, profile editing, role assignment, lock/unlock audit history and `.xlsx` import/template download.
- Self-service profile updates and private PNG/JPEG/WebP avatar storage (maximum 2 MB per avatar).
- Public consultation requests persisted as leads, with an Admissions/Admin Kanban pipeline, searchable status columns, drag-and-drop stage updates, and an append-only care journal.
- Qualified leads can be converted once into a student account and student profile with a generated student code, a temporary password, and mandatory first-login password change.
- Searchable student-profile directory for Training Managers and Admissions, including program, contact, and identity updates.
- Course catalog with credits, departments, prerequisites and safe deactivation.
- Eight seeded roles: Admin, Training Manager, Admissions, Accountant, Instructor, TA, Student and Guest.
- Training classes opened from courses in a selected study program, with instructor/TA assignment, weekly schedule and room/staff conflict checks.
- Room catalog for room code, capacity, and online meeting URL; class capacity is validated against registered rooms.
- Searchable/filterable class list with quick enrollment/capacity statistics and single or bulk student enrollment with capacity and student timetable-conflict validation.
- Role-scoped class lists, rosters, attendance status, timetable, assignments and dashboard summary.
- Undergraduate study programs and per-session attendance history for seeded academic demonstrations.

The supplied workbook's summary mentions 75 stories, but its detailed sheet contains acceptance criteria for US01–US15 only. Additional stories need their detailed requirements before they can be implemented accurately.
