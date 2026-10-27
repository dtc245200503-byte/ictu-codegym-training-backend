import pytest
from pydantic import ValidationError
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from time import perf_counter

from app.core.security import create_access_token, hash_password
from app.models.course import Course
from app.models.role import Role
from app.models.study_program import ProgramFeeSchedule, StudyProgram
from app.models.training_class import (Assignment, AssignmentGradeVersion, AssignmentSubmission,
                                      ClassEnrollment, TrainingClass)
from app.models.attendance import AttendanceRecord, AttendanceRiskState, AttendanceSession
from app.models.academic import CourseFinalGrade, GradePolicy, GraduationEvent
from app.models.student_profile import StudentProfile
from app.models.survey import ClassSurvey
from app.models.tuition import TuitionInstallment, TuitionPlan
from app.models.user import User
from app.schemas.management import GradePolicyCreate


def test_grade_policy_requires_weights_to_total_one_hundred():
    with pytest.raises(ValidationError, match="weights must sum"):
        GradePolicyCreate.model_validate({
            "program_id": 1,
            "course_id": 1,
            "components": [
                {"key": "quiz", "name": "Quiz", "weight": "30"},
                {"key": "project", "name": "Project", "weight": "60"},
            ],
            "pass_score": "60",
        })


def test_grade_policy_validates_unique_components_and_critical_threshold():
    base = {
        "program_id": 1,
        "course_id": 1,
        "components": [
            {"key": "quiz", "name": "Quiz", "weight": "50"},
            {"key": "project", "name": "Project", "weight": "50"},
        ],
        "pass_score": "60",
    }
    with pytest.raises(ValidationError, match="unique"):
        GradePolicyCreate.model_validate({**base, "components": [
            {"key": "quiz", "name": "Quiz", "weight": "50"},
            {"key": "quiz", "name": "Project", "weight": "50"},
        ]})
    with pytest.raises(ValidationError, match="must be configured together"):
        GradePolicyCreate.model_validate({**base, "critical_component_key": "project"})
    policy = GradePolicyCreate.model_validate({
        **base, "critical_component_key": "project", "critical_score": "50"
    })
    assert policy.critical_score == 50


def test_grade_policy_class_override_api_is_scoped_and_versioned(client, test_db):
    manager_role = Role(code="manager", name="Manager")
    teacher_role = Role(code="teacher", name="Teacher")
    manager = User(username="policy-manager", email="policy-manager@example.test",
        full_name="Manager", hashed_password=hash_password("test-password"), roles=[manager_role])
    teacher = User(username="policy-teacher", email="policy-teacher@example.test",
        full_name="Teacher", hashed_password=hash_password("test-password"), roles=[teacher_role])
    course = Course(code="POL-01", name="Policy Course", department="ICTU", credits=3)
    program = StudyProgram(code="POL-PROG", name="Policy Program", total_credits=3)
    program.courses.append(course)
    test_db.add_all([manager, teacher, course, program])
    test_db.flush()
    training_class = TrainingClass(code="POL-CLASS", name="Policy Class", course_id=course.id,
        teacher_id=teacher.id, room="A1", term="2026", day_of_week=1,
        start_time=time(8), end_time=time(10), capacity=20)
    test_db.add(training_class)
    test_db.commit()
    manager_headers = {"Authorization": f"Bearer {create_access_token(str(manager.id))}"}
    teacher_headers = {"Authorization": f"Bearer {create_access_token(str(teacher.id))}"}
    policy_json = {"program_id": program.id, "course_id": course.id,
        "components": [{"key": "exam", "name": "Exam", "weight": "100"}],
        "pass_score": "60"}
    base = client.post("/api/grade-policies", headers=manager_headers, json=policy_json)
    assert base.status_code == 201 and base.json()["version"] == 1
    override = client.post("/api/grade-policies", headers=manager_headers,
        json={**policy_json, "class_id": training_class.id, "pass_score": "70"})
    assert override.status_code == 201 and override.json()["version"] == 1
    listed = client.get(f"/api/grade-policies?program_id={program.id}&course_id={course.id}&class_id={training_class.id}",
                        headers=teacher_headers)
    assert listed.status_code == 200 and listed.json()[0]["class_id"] == training_class.id
    assert listed.json()[0]["pass_score"] == "70.00"
    denied = client.post("/api/grade-policies", headers=teacher_headers, json=policy_json)
    assert denied.status_code == 403


def test_final_grade_calculation_lock_and_manager_reopen(client, test_db):
    manager_role = Role(code="manager", name="Manager")
    teacher_role = Role(code="teacher", name="Teacher")
    student_role = Role(code="student", name="Student")
    manager = User(username="lock-manager", email="lock-manager@example.test",
        full_name="Manager", hashed_password=hash_password("test-password"), roles=[manager_role])
    teacher = User(username="lock-teacher", email="lock-teacher@example.test",
        full_name="Teacher", hashed_password=hash_password("test-password"), roles=[teacher_role])
    student = User(username="lock-student", email="lock-student@example.test",
        full_name="Student", hashed_password=hash_password("test-password"), roles=[student_role])
    critical_student = User(username="critical-student", email="critical-student@example.test",
        full_name="Critical Student", hashed_password=hash_password("test-password"), roles=[student_role])
    course = Course(code="LOCK-01", name="Lock Course", department="ICTU", credits=3)
    program = StudyProgram(code="LOCK-PROG", name="Lock Program", total_credits=3)
    program.courses.append(course)
    test_db.add_all([manager, teacher, student, critical_student, course, program])
    test_db.flush()
    training_class = TrainingClass(code="LOCK-CLASS", name="Lock Class", course_id=course.id,
        teacher_id=teacher.id, room="A1", term="2026", day_of_week=1,
        start_time=time(8), end_time=time(10), capacity=20)
    test_db.add(training_class)
    test_db.flush()
    test_db.add_all([
        ClassEnrollment(class_id=training_class.id, student_id=student.id),
        ClassEnrollment(class_id=training_class.id, student_id=critical_student.id),
    ])
    policy = GradePolicy(program_id=program.id, course_id=course.id,
        scope_key="program", version=1,
        components=[
            {"key": "exam", "name": "Exam", "weight": "70", "required": True},
            {"key": "project", "name": "Project", "weight": "30", "required": True},
        ], pass_score=Decimal("60"), critical_component_key="exam", critical_score=Decimal("85"))
    assignment = Assignment(class_id=training_class.id, title="Final exam", max_score=Decimal("100"),
        status="published", grade_component_key="exam", created_by=teacher.id)
    project_assignment = Assignment(class_id=training_class.id, title="Project", max_score=Decimal("100"),
        status="published", grade_component_key="project", created_by=teacher.id)
    test_db.add_all([policy, assignment, project_assignment])
    test_db.flush()
    submission = AssignmentSubmission(assignment_id=assignment.id, student_id=student.id,
        version_number=1, repository_url="https://example.test/repo", submitted_at=datetime.now())
    test_db.add(submission)
    test_db.flush()
    test_db.add(AssignmentGradeVersion(assignment_id=assignment.id, submission_id=submission.id,
        student_id=student.id, version_number=1, score=Decimal("82"), status="published",
        created_by=teacher.id, published_at=datetime.now()))
    second_exam_submission = AssignmentSubmission(assignment_id=assignment.id, student_id=critical_student.id,
        version_number=1, repository_url="https://example.test/critical-exam", submitted_at=datetime.now())
    second_project_submission = AssignmentSubmission(assignment_id=project_assignment.id,
        student_id=critical_student.id, version_number=1,
        repository_url="https://example.test/critical-project", submitted_at=datetime.now())
    test_db.add_all([second_exam_submission, second_project_submission])
    test_db.flush()
    test_db.add_all([
        AssignmentGradeVersion(assignment_id=assignment.id, submission_id=second_exam_submission.id,
            student_id=critical_student.id, version_number=1, score=Decimal("82"), status="published",
            created_by=teacher.id, published_at=datetime.now()),
        AssignmentGradeVersion(assignment_id=project_assignment.id, submission_id=second_project_submission.id,
            student_id=critical_student.id, version_number=1, score=Decimal("90"), status="published",
            created_by=teacher.id, published_at=datetime.now()),
    ])
    test_db.commit()
    teacher_headers = {"Authorization": f"Bearer {create_access_token(str(teacher.id))}"}
    manager_headers = {"Authorization": f"Bearer {create_access_token(str(manager.id))}"}
    url = f"/api/classes/{training_class.id}/final-grades?program_id={program.id}"
    calculation = client.get(url, headers=teacher_headers)
    assert calculation.status_code == 200
    rows = {row["student_id"]: row for row in calculation.json()["students"]}
    assert rows[student.id]["score"] == "57.40"
    assert rows[student.id]["result"] == "incomplete"
    assert rows[student.id]["missing_components"] == ["project"]
    assert rows[student.id]["breakdown"]["components"][0]["score_percent"] == "82.00"
    assert rows[critical_student.id]["score"] == "84.40"
    assert rows[critical_student.id]["result"] == "fail"
    assert rows[critical_student.id]["critical_component_score"] == "82.00"
    incomplete_lock = client.post(f"/api/classes/{training_class.id}/final-grades/lock?program_id={program.id}",
        headers=teacher_headers)
    assert incomplete_lock.status_code == 409
    assert incomplete_lock.json()["detail"]["student_ids"] == [student.id]
    project_submission = AssignmentSubmission(assignment_id=project_assignment.id, student_id=student.id,
        version_number=1, repository_url="https://example.test/project", submitted_at=datetime.now())
    test_db.add(project_submission)
    test_db.flush()
    test_db.add(AssignmentGradeVersion(assignment_id=project_assignment.id,
        submission_id=project_submission.id, student_id=student.id, version_number=1,
        score=Decimal("90"), status="published", created_by=teacher.id, published_at=datetime.now()))
    test_db.commit()
    recalculated = client.get(url, headers=teacher_headers).json()["students"]
    first_after_component = next(row for row in recalculated if row["student_id"] == student.id)
    assert first_after_component["result"] == "fail"
    assert first_after_component["score"] == "84.40"
    locked = client.post(f"/api/classes/{training_class.id}/final-grades/lock?program_id={program.id}",
                         headers=teacher_headers)
    assert locked.status_code == 200 and locked.json()["locked_count"] == 2
    assert client.post(f"/api/classes/{training_class.id}/students/{student.id}/final-grade/reopen",
        headers=teacher_headers, json={"reason": "Fix score"}).status_code == 403
    reopened = client.post(f"/api/classes/{training_class.id}/students/{student.id}/final-grade/reopen",
        headers=manager_headers, json={"reason": "Corrected exam entry"})
    assert reopened.status_code == 200 and reopened.json()["is_locked"] is False


def test_student_transcript_is_self_scoped_and_downloadable(client, test_db):
    role = Role(code="student", name="Student")
    test_db.add(role)
    test_db.flush()
    student = User(username="transcript-student", email="transcript@example.test",
                   full_name="Transcript Student", student_code="ST-01",
                   hashed_password=hash_password("test-password"), roles=[role])
    teacher_role = Role(code="teacher", name="Teacher")
    teacher = User(username="transcript-teacher", email="teacher-transcript@example.test",
                   full_name="Teacher", hashed_password=hash_password("test-password"),
                   roles=[teacher_role])
    course = Course(code="TR-01", name="Transcript Course", department="ICTU", credits=3)
    course_two = Course(code="TR-02", name="Transcript Course Z", department="ICTU", credits=3)
    program = StudyProgram(code="TR-PROG", name="Transcript Program", total_credits=3)
    program.courses.append(course)
    program.courses.append(course_two)
    test_db.add_all([student, teacher, course, course_two, program])
    test_db.flush()
    training_class = TrainingClass(code="TR-CLASS", name="Transcript Class", course_id=course.id,
        teacher_id=teacher.id, room="A1", term="2026", day_of_week=1,
        start_time=time(8), end_time=time(10), capacity=20)
    test_db.add(training_class)
    test_db.flush()
    test_db.add(ClassEnrollment(class_id=training_class.id, student_id=student.id))
    training_class_two = TrainingClass(code="TR-CLASS-2", name="Transcript Class 2",
        course_id=course_two.id, teacher_id=teacher.id, room="A2", term="2026",
        day_of_week=2, start_time=time(8), end_time=time(10), capacity=20)
    test_db.add(training_class_two)
    test_db.flush()
    test_db.add(ClassEnrollment(class_id=training_class_two.id, student_id=student.id))
    policy = GradePolicy(program_id=program.id, course_id=course.id, class_id=None,
        scope_key="program", version=1, components=[{"key": "exam", "name": "Exam", "weight": "100"}],
        pass_score=Decimal("60"))
    test_db.add(policy)
    test_db.flush()
    test_db.add(CourseFinalGrade(class_id=training_class.id, student_id=student.id,
        course_id=course.id, policy_id=policy.id, version=1, score=Decimal("88"),
        result="pass", breakdown={"weighted_score": "88"}, is_locked=True,
        finalized_by=teacher.id))
    policy_two = GradePolicy(program_id=program.id, course_id=course_two.id, class_id=None,
        scope_key="program", version=1, components=[{"key": "exam", "name": "Exam", "weight": "100"}],
        pass_score=Decimal("60"))
    test_db.add(policy_two)
    test_db.flush()
    test_db.add(CourseFinalGrade(class_id=training_class_two.id, student_id=student.id,
        course_id=course_two.id, policy_id=policy_two.id, version=1, score=Decimal("68"),
        result="pass", breakdown={"weighted_score": "68"}, is_locked=True,
        finalized_by=teacher.id))
    test_db.commit()
    headers = {"Authorization": f"Bearer {create_access_token(str(student.id))}"}
    response = client.get("/api/students/me/transcript", headers=headers)
    assert response.status_code == 200
    assert response.json()["student_code"] == "ST-01"
    assert response.json()["courses"][0]["course_name"] == "Transcript Course"
    assert response.json()["courses"][0]["result"] == "pass"
    assert response.json()["courses"][0]["final_score"] == "88.00"
    assert response.json()["course_average"] == "78.00"
    first_page = client.get("/api/students/me/transcript?page=1&page_size=1", headers=headers)
    second_page = client.get("/api/students/me/transcript?page=2&page_size=1", headers=headers)
    assert first_page.status_code == second_page.status_code == 200
    assert [item["course_name"] for item in first_page.json()["courses"]] == ["Transcript Course"]
    assert [item["course_name"] for item in second_page.json()["courses"]] == ["Transcript Course Z"]
    assert first_page.json()["course_average"] == second_page.json()["course_average"] == "78.00"
    assert first_page.json()["total"] == 2 and first_page.headers["x-total-count"] == "2"
    pdf = client.get("/api/students/me/transcript.pdf", headers=headers)
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF-1.4")
    assert b"Transcript Course" in pdf.content and b"Transcript Course Z" in pdf.content
    assert b"88.00" in pdf.content
    assert b"ST-01" in pdf.content
    assert response.json()["issued_on"].encode("ascii") in pdf.content
    assert b"ICTU x CodeGym" in pdf.content


def test_graduation_requires_configured_threshold_and_manager_scope(client, test_db):
    role = Role(code="manager", name="Training Manager")
    test_db.add(role)
    manager = User(username="grad-manager", email="grad-manager@example.test",
                   full_name="Manager", hashed_password=hash_password("test-password"),
                   roles=[role])
    program = StudyProgram(code="GRAD-01", name="Graduation Program", total_credits=3)
    test_db.add_all([manager, program])
    test_db.commit()
    headers = {"Authorization": f"Bearer {create_access_token(str(manager.id))}"}
    response = client.post(f"/api/study-programs/{program.id}/graduations/confirm",
                           headers=headers, json={"student_ids": [12345]})
    assert response.status_code == 409
    response = client.put(f"/api/study-programs/{program.id}/graduation-policy",
                          headers=headers,
                          json={"attendance_threshold_percent": "80"})
    assert response.status_code == 200
    assert response.json()["attendance_threshold_percent"] == "80.00"
    assert client.get(f"/api/study-programs/{program.id}/graduation-eligibility",
                      headers=headers).status_code == 200
    assert client.post(f"/api/study-programs/{program.id}/graduations/confirm",
                       headers=headers, json={"student_ids": [12345]}).status_code == 404
    assert client.put(f"/api/study-programs/{program.id}/graduation-policy",
                      json={"attendance_threshold_percent": "80"}).status_code == 401


def test_graduation_checks_pass_attendance_debt_and_records_batch_history(client, test_db):
    manager_role = Role(code="manager", name="Manager")
    student_role = Role(code="student", name="Student")
    teacher_role = Role(code="teacher", name="Teacher")
    manager = User(username="eligible-manager", email="eligible-manager@example.test",
        full_name="Manager", hashed_password=hash_password("test-password"), roles=[manager_role])
    student = User(username="eligible-student", email="eligible-student@example.test",
        full_name="Eligible Student", student_code="EL-01",
        hashed_password=hash_password("test-password"), roles=[student_role])
    ineligible_student = User(username="ineligible-student", email="ineligible-student@example.test",
        full_name="Ineligible Student", student_code="IN-01",
        hashed_password=hash_password("test-password"), roles=[student_role])
    teacher = User(username="eligible-teacher", email="eligible-teacher@example.test",
        full_name="Teacher", hashed_password=hash_password("test-password"), roles=[teacher_role])
    course = Course(code="EL-01", name="Eligible Course", department="ICTU", credits=3)
    program = StudyProgram(code="EL-PROG", name="Eligible Program", total_credits=3,
                           graduation_attendance_threshold=Decimal("80"))
    program.courses.append(course)
    test_db.add_all([manager, student, ineligible_student, teacher, course, program])
    test_db.flush()
    training_class = TrainingClass(code="EL-CLASS", name="Eligible Class", course_id=course.id,
        teacher_id=teacher.id, room="A1", term="2026", day_of_week=1,
        start_time=time(8), end_time=time(10), capacity=20)
    test_db.add(training_class)
    test_db.flush()
    test_db.add(ClassEnrollment(class_id=training_class.id, student_id=student.id,
                                enrollment_status="completed", effective_date=date.today() - timedelta(days=2)))
    ineligible_enrollment = ClassEnrollment(class_id=training_class.id, student_id=ineligible_student.id,
        enrollment_status="completed", effective_date=date.today() - timedelta(days=2))
    test_db.add(ineligible_enrollment)
    policy = GradePolicy(program_id=program.id, course_id=course.id, class_id=None,
        scope_key="program", version=1, components=[{"key": "exam", "name": "Exam", "weight": "100"}],
        pass_score=Decimal("60"))
    test_db.add(policy)
    test_db.flush()
    session = AttendanceSession(class_id=training_class.id, session_date=date.today() - timedelta(days=1),
        start_time=time(8), end_time=time(10), teacher_id=teacher.id)
    test_db.add(session)
    test_db.flush()
    test_db.add(AttendanceRecord(session_id=session.id, student_id=student.id,
                                 marked_by=teacher.id, status="present"))
    test_db.add(AttendanceRecord(session_id=session.id, student_id=ineligible_student.id,
        marked_by=teacher.id, status="unexcused_absent"))
    test_db.add(CourseFinalGrade(class_id=training_class.id, student_id=student.id,
        course_id=course.id, policy_id=policy.id, version=1, score=Decimal("90"),
        result="pass", breakdown={"weighted_score": "90"}, is_locked=True,
        finalized_by=teacher.id))
    fee_schedule = ProgramFeeSchedule(program_id=program.id, version=1, amount_vnd=5000,
        valid_from=date.today() - timedelta(days=30))
    test_db.add(fee_schedule)
    test_db.flush()
    debt_plan = TuitionPlan(enrollment_id=ineligible_enrollment.id, fee_schedule_id=fee_schedule.id,
        total_due_vnd=5000, applied_on=date.today() - timedelta(days=2))
    test_db.add(debt_plan)
    test_db.flush()
    test_db.add(TuitionInstallment(plan_id=debt_plan.id, installment_number=1,
        amount_due_vnd=5000, due_date=date.today() + timedelta(days=10)))
    test_db.commit()
    headers = {"Authorization": f"Bearer {create_access_token(str(manager.id))}"}
    eligibility = client.get(f"/api/study-programs/{program.id}/graduation-eligibility", headers=headers)
    assert eligibility.status_code == 200
    assert [row["student_id"] for row in eligibility.json()["eligible"]] == [student.id]
    ineligible = next(row for row in eligibility.json()["ineligible"]
        if row["student_id"] == ineligible_student.id)
    assert {"courses_not_passed", "attendance_below_threshold", "tuition_debt_outstanding"}.issubset(
        set(ineligible["reasons"])
    )
    assert ineligible["outstanding_vnd"] == "5000"
    confirmed = client.post(f"/api/study-programs/{program.id}/graduations/confirm",
        headers=headers, json={"student_ids": [student.id]})
    assert confirmed.status_code == 200 and confirmed.json()["confirmed_count"] == 1
    duplicate_confirmation = client.post(f"/api/study-programs/{program.id}/graduations/confirm",
        headers=headers, json={"student_ids": [student.id]})
    assert duplicate_confirmation.status_code == 409
    assert test_db.query(GraduationEvent).filter_by(student_id=student.id, program_id=program.id).count() == 1
    test_db.refresh(test_db.get(StudentProfile, student.id))
    assert test_db.get(StudentProfile, student.id).graduation_status == "graduated"
    assert test_db.query(GraduationEvent).filter_by(student_id=student.id, program_id=program.id).count() == 1
    profile_response = client.get(f"/api/students/{student.id}", headers=headers)
    assert profile_response.status_code == 200
    assert profile_response.json()["graduation_status"] == "graduated"


def test_survey_response_is_one_time_and_reports_are_anonymous(client, test_db):
    student_role = Role(code="student", name="Student")
    manager_role = Role(code="manager", name="Manager")
    teacher_role = Role(code="teacher", name="Teacher")
    student = User(username="survey-student", email="survey-student@example.test",
        full_name="Survey Student", hashed_password=hash_password("test-password"),
        roles=[student_role], student_code="SUR-1")
    manager = User(username="survey-manager", email="survey-manager@example.test",
        full_name="Survey Manager", hashed_password=hash_password("test-password"),
        roles=[manager_role])
    teacher = User(username="survey-teacher", email="survey-teacher@example.test",
        full_name="Survey Teacher", hashed_password=hash_password("test-password"),
        roles=[teacher_role])
    course = Course(code="SUR-01", name="Survey Course", department="ICTU", credits=3)
    test_db.add_all([student, manager, teacher, course])
    test_db.flush()
    training_class = TrainingClass(code="SUR-CLASS", name="Survey Class", course_id=course.id,
        teacher_id=teacher.id, room="A1", term="2026", day_of_week=1,
        start_time=time(8), end_time=time(10), capacity=20)
    test_db.add(training_class)
    test_db.flush()
    test_db.add(ClassEnrollment(class_id=training_class.id, student_id=student.id))
    test_db.commit()
    manager_headers = {"Authorization": f"Bearer {create_access_token(str(manager.id))}"}
    student_headers = {"Authorization": f"Bearer {create_access_token(str(student.id))}"}
    teacher_headers = {"Authorization": f"Bearer {create_access_token(str(teacher.id))}"}
    from datetime import datetime, timedelta, timezone
    created = client.post("/api/surveys", headers=manager_headers, json={
        "class_id": training_class.id, "title": "Course feedback",
        "deadline": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
        "questions": [
            {"prompt": "Rate teaching", "question_type": "rating"},
            {"prompt": "Comment", "question_type": "text"},
        ],
    })
    assert created.status_code == 201
    survey_id = created.json()["id"]
    assert client.post("/api/surveys", headers=manager_headers, json={
        "class_id": training_class.id, "title": "Second survey",
        "deadline": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
        "questions": [{"prompt": "Question", "question_type": "text"}],
    }).status_code == 409
    question_ids = [question["id"] for question in created.json()["questions"]]
    invalid_rating = client.post(f"/api/surveys/{survey_id}/responses", headers=student_headers,
        json={"answers": {str(question_ids[0]): 6, str(question_ids[1]): "Invalid rating"}})
    assert invalid_rating.status_code == 422
    submitted = client.post(f"/api/surveys/{survey_id}/responses", headers=student_headers,
        json={"answers": {str(question_ids[0]): 4, str(question_ids[1]): "Good course"}})
    assert submitted.status_code == 201
    assert client.post(f"/api/surveys/{survey_id}/responses", headers=student_headers,
        json={"answers": {str(question_ids[0]): 4, str(question_ids[1]): "Again"}}).status_code == 409
    report = client.get(f"/api/surveys/{survey_id}/results", headers=manager_headers)
    assert report.status_code == 200
    assert report.json()["questions"][0]["average"] == 4
    assert report.json()["text_answers"][0]["answer"] == "Good course"
    assert "student_id" not in report.text and "student_code" not in report.text
    identities = client.get(f"/api/surveys/{survey_id}/respondents", headers=manager_headers)
    assert identities.json()["responded_student_ids"] == [student.id]
    assert client.get(f"/api/surveys/{survey_id}/respondents", headers=teacher_headers).status_code == 403
    second_class = TrainingClass(code="SUR-CLASS-2", name="Survey Class Two", course_id=course.id,
        teacher_id=teacher.id, room="A2", term="2026", day_of_week=2,
        start_time=time(8), end_time=time(10), capacity=20)
    test_db.add(second_class)
    test_db.flush()
    test_db.add(ClassEnrollment(class_id=second_class.id, student_id=student.id))
    test_db.commit()
    second_survey = client.post("/api/surveys", headers=manager_headers, json={
        "class_id": second_class.id, "title": "Second class feedback",
        "deadline": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
        "questions": [{"prompt": "Rate teaching", "question_type": "rating"}],
    })
    assert second_survey.status_code == 201
    second_question_id = second_survey.json()["questions"][0]["id"]
    stored_second_survey = test_db.get(ClassSurvey, second_survey.json()["id"])
    stored_second_survey.deadline = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
    test_db.commit()
    assert client.post(f"/api/surveys/{second_survey.json()['id']}/responses", headers=student_headers,
        json={"answers": {str(second_question_id): 5}}).status_code == 409
    stored_second_survey.deadline = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1)
    test_db.commit()
    assert client.post(f"/api/surveys/{second_survey.json()['id']}/responses", headers=student_headers,
        json={"answers": {str(second_question_id): 5}}).status_code == 201
    comparison_json = client.get(f"/api/survey-reports/teachers/{teacher.id}", headers=manager_headers)
    assert comparison_json.status_code == 200 and len(comparison_json.json()["classes"]) == 2
    class_reports = {item["class_id"]: item for item in comparison_json.json()["classes"]}
    assert class_reports[training_class.id]["questions"][0]["average"] == 4
    assert class_reports[second_class.id]["questions"][0]["average"] == 5
    exported = client.get(f"/api/surveys/{survey_id}/results.xlsx", headers=manager_headers)
    assert exported.status_code == 200 and exported.content.startswith(b"PK")
    comparison = client.get(f"/api/survey-reports/teachers/{teacher.id}/export.xlsx",
                            headers=manager_headers)
    assert comparison.status_code == 200 and comparison.content.startswith(b"PK")


def test_dashboards_enforce_role_and_return_real_empty_aggregates(client, test_db):
    manager_role = Role(code="manager", name="Manager")
    teacher_role = Role(code="teacher", name="Teacher")
    manager = User(username="dash-manager", email="dash-manager@example.test",
        full_name="Manager", hashed_password=hash_password("test-password"), roles=[manager_role])
    teacher = User(username="dash-teacher", email="dash-teacher@example.test",
        full_name="Teacher", hashed_password=hash_password("test-password"), roles=[teacher_role])
    test_db.add_all([manager, teacher])
    test_db.commit()
    manager_headers = {"Authorization": f"Bearer {create_access_token(str(manager.id))}"}
    teacher_headers = {"Authorization": f"Bearer {create_access_token(str(teacher.id))}"}
    manager_data = client.get("/api/dashboards/management", headers=manager_headers)
    assert manager_data.status_code == 200
    assert manager_data.json()["cards"]["active_classes"] == 0
    assert manager_data.json()["cards"]["active_students"] == 0
    assert manager_data.json()["high_absence_students"] == []
    teacher_data = client.get("/api/dashboards/teacher", headers=teacher_headers)
    assert teacher_data.status_code == 200
    assert teacher_data.json()["next_class"] is None
    assert teacher_data.json()["ungraded_submission_count"] == 0
    assert client.get("/api/dashboards/management", headers=teacher_headers).status_code == 403


def test_teacher_dashboard_scopes_upcoming_class_and_ungraded_submission(client, test_db):
    role = Role(code="teacher", name="Teacher")
    other_role = role
    teacher = User(username="scoped-teacher", email="scoped-teacher@example.test",
        full_name="Scoped Teacher", hashed_password=hash_password("test-password"), roles=[role])
    other = User(username="other-teacher", email="other-teacher@example.test",
        full_name="Other Teacher", hashed_password=hash_password("test-password"), roles=[other_role])
    student = User(username="scoped-student", email="scoped-student@example.test",
        full_name="Student", hashed_password=hash_password("test-password"))
    course = Course(code="DASH-01", name="Dashboard Course", department="ICTU", credits=3)
    test_db.add_all([teacher, other, student, course])
    test_db.flush()
    training_class = TrainingClass(code="DASH-CLASS", name="Dashboard Class", course_id=course.id,
        teacher_id=teacher.id, room="A1", term="2026", day_of_week=1,
        start_time=time(8), end_time=time(10), capacity=20, status="active")
    test_db.add(training_class)
    test_db.flush()
    assignment = Assignment(class_id=training_class.id, title="Pending", status="published",
                            created_by=teacher.id)
    test_db.add(assignment)
    test_db.flush()
    test_db.add(AssignmentSubmission(assignment_id=assignment.id, student_id=student.id,
        version_number=1, repository_url="https://example.test/submission", submitted_at=datetime.now()))
    future_day = date.today() + timedelta(days=1)
    session = AttendanceSession(class_id=training_class.id, session_date=future_day,
        start_time=time(9), end_time=time(11), teacher_id=teacher.id)
    past_session = AttendanceSession(class_id=training_class.id,
        session_date=date.today() - timedelta(days=1), start_time=time(9), end_time=time(11),
        teacher_id=teacher.id)
    test_db.add_all([session, past_session])
    test_db.flush()
    test_db.add(AttendanceRecord(session_id=past_session.id, student_id=student.id,
        marked_by=teacher.id, status="unexcused_absent"))
    test_db.commit()
    teacher_headers = {"Authorization": f"Bearer {create_access_token(str(teacher.id))}"}
    other_headers = {"Authorization": f"Bearer {create_access_token(str(other.id))}"}
    scoped = client.get("/api/dashboards/teacher", headers=teacher_headers)
    assert scoped.status_code == 200
    assert scoped.json()["next_class"]["class_id"] == training_class.id
    assert scoped.json()["ungraded_submission_count"] == 1
    assert scoped.json()["most_absent_students"][0]["student_id"] == student.id
    assert scoped.json()["most_absent_students"][0]["absence_count"] == 1
    assert scoped.json()["most_absent_students"][0]["attendance_url"] == (
        f"/attendance?class_id={training_class.id}&student_id={student.id}"
    )
    assert scoped.json()["grading_url"] == "/assignments"
    isolated = client.get("/api/dashboards/teacher", headers=other_headers)
    assert isolated.status_code == 200
    assert isolated.json()["next_class"] is None
    assert isolated.json()["ungraded_submission_count"] == 0


def test_management_dashboard_ranks_persisted_risks_with_representative_load(client, test_db):
    manager_role = Role(code="manager", name="Manager")
    manager = User(username="dashboard-risk-manager", email="dashboard-risk-manager@example.test",
        full_name="Dashboard Manager", hashed_password="unused", roles=[manager_role])
    teacher_role = Role(code="teacher", name="Teacher")
    teacher = User(username="dashboard-risk-teacher", email="dashboard-risk-teacher@example.test",
        full_name="Dashboard Teacher", hashed_password="unused", roles=[teacher_role])
    course = Course(code="RISK-DASH", name="Risk Dashboard", department="ICTU", credits=3)
    test_db.add_all([manager, teacher, course])
    test_db.flush()
    classes = [TrainingClass(code=f"RISK-DASH-{index:02}", name=f"Risk class {index}",
        course_id=course.id, teacher_id=teacher.id, room=f"R{index}", term="2026",
        day_of_week=index % 7, start_time=time(8), end_time=time(10), capacity=20, status="active")
        for index in range(10)]
    test_db.add_all(classes)
    test_db.flush()
    students = [User(username=f"risk-dashboard-{index:03}",
        email=f"risk-dashboard-{index:03}@example.test", full_name=f"Risk Student {index:03}",
        hashed_password="unused") for index in range(100)]
    test_db.add_all(students)
    test_db.flush()
    enrollments = []
    states = []
    for class_index, training_class in enumerate(classes):
        for student_index in range(10):
            student = students[class_index * 10 + student_index]
            enrollments.append(ClassEnrollment(class_id=training_class.id, student_id=student.id))
            states.append(AttendanceRiskState(class_id=training_class.id, student_id=student.id,
                is_at_risk=True, absent_count=5 if class_index == 0 and student_index == 0 else 3,
                completed_session_count=10, absence_threshold_percent=20, activation_count=1))
    test_db.add_all([*enrollments, *states])
    test_db.commit()
    headers = {"Authorization": f"Bearer {create_access_token(str(manager.id))}"}
    started = perf_counter()
    response = client.get("/api/dashboards/management", headers=headers)
    elapsed = perf_counter() - started
    assert response.status_code == 200
    assert response.json()["cards"]["active_classes"] == 10
    assert response.json()["cards"]["active_students"] == 100
    risks = response.json()["high_absence_students"]
    assert len(risks) == 50
    assert risks[0]["student_id"] == students[0].id
    assert risks[0]["absence_rate_percent"] == 50.0
    assert risks[0]["severity_percent"] == 30.0
    assert all(item["severity_percent"] >= risks[-1]["severity_percent"] for item in risks)
    assert elapsed < 2.0
