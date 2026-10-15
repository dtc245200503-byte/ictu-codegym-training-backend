from datetime import date
import pytest
from test_leads import ctx, req
from app.database import SessionLocal
from app.models.training_program import TrainingProgram, TrainingClass
from app.models.subject import Subject, ClassSubject, ClassSubjectAssistant
from app.models.subject_lesson import SubjectLesson
from app.models.curriculum import CurriculumSubject
from app.models.consultation import ConsultationLead
from app.models.lead_assignment import LeadAssignmentHistory
from app.models.permission import Permission
from app.models.role import Role
from app.models.role_permission import RolePermission
from app.models.user import User
from app.models.user_role import UserRole
from app.schemas.profile import UpdateProfileRequest
from scripts.seed_demo_data import seed_demo_data, PROGRAMS, SUBJECTS, CLASSES


@pytest.fixture
def demo(ctx):
    with SessionLocal() as db:
        permission = db.query(Permission).filter_by(permission_name='CLASS_MANAGE').first()
        if not permission:
            permission = Permission(permission_name='CLASS_MANAGE')
            db.add(permission)
            db.flush()
        permission_id = permission.permission_id
        for role_id in [1, 2, 5]:
            if not db.query(RolePermission).filter_by(role_id=role_id, permission_id=permission_id).first():
                db.add(RolePermission(role_id=role_id, permission_id=permission_id))

        assistant_role = db.query(Role).filter(Role.role_name.in_(['ASSISTANT', 'TA'])).first()
        if not assistant_role:
            assistant_role = Role(role_id=9, role_name='ASSISTANT')
            db.add(assistant_role)
            db.flush()

        ta_users = [
            User(username=ctx['prefix']+'ta1', full_name=ctx['prefix']+' Trợ Giảng 1', email=ctx['prefix']+'ta1@example.com', password='unused', role_id=assistant_role.role_id, is_locked=False),
            User(username=ctx['prefix']+'ta2', full_name=ctx['prefix']+' Trợ Giảng 2', email=ctx['prefix']+'ta2@example.com', password='unused', role_id=assistant_role.role_id, is_locked=False),
        ]
        db.add_all(ta_users)
        db.flush()
        for ta in ta_users:
            db.add(UserRole(user_id=ta.user_id, role_id=assistant_role.role_id))

        seed_demo_data(db, today=date(2026, 10, 7))
        db.commit()
    yield ctx
    with SessionLocal() as db:
        db.query(ClassSubjectAssistant).delete(synchronize_session=False)
        leads = db.query(ConsultationLead).filter(ConsultationLead.email.like('demo.lead%@example.com')).all()
        ids = [item.lead_id for item in leads]
        db.query(LeadAssignmentHistory).filter(LeadAssignmentHistory.lead_id.in_(ids)).delete(synchronize_session=False)
        db.query(ConsultationLead).filter(ConsultationLead.lead_id.in_(ids)).delete(synchronize_session=False)
        classes = [r[0] for r in db.query(TrainingClass.class_id).filter(TrainingClass.class_name.in_([item[0] for item in CLASSES]))]
        db.query(ClassSubject).filter(ClassSubject.class_id.in_(classes)).delete(synchronize_session=False)
        db.query(TrainingClass).filter(TrainingClass.class_id.in_(classes)).delete(synchronize_session=False)
        programs = [r[0] for r in db.query(TrainingProgram.course_id).filter(TrainingProgram.code.in_([item[0] for item in PROGRAMS]))]
        db.query(CurriculumSubject).filter(CurriculumSubject.curriculum_id.in_(programs)).delete(synchronize_session=False)
        db.query(TrainingProgram).filter(TrainingProgram.course_id.in_(programs)).delete(synchronize_session=False)
        subjects = [r[0] for r in db.query(Subject.id).filter(Subject.code.in_([item[0] for item in SUBJECTS]))]
        db.query(SubjectLesson).filter(SubjectLesson.subject_id.in_(subjects)).delete(synchronize_session=False)
        db.query(Subject).filter(Subject.id.in_(subjects)).delete(synchronize_session=False)
        db.query(RolePermission).filter_by(permission_id=permission_id).delete(synchronize_session=False)
        db.query(Permission).filter_by(permission_id=permission_id).delete(synchronize_session=False)
        db.commit()



def test_seed_counts_links_valid_phones_and_idempotence(demo):
    with SessionLocal() as db:
        assert db.query(TrainingProgram).filter(TrainingProgram.code.like('DEMO-%')).count()==5
        assert db.query(Subject).filter(Subject.code.like('DEMO-%')).count()==10
        assert db.query(SubjectLesson).count()==46
        assert db.query(CurriculumSubject).count()==17
        assert db.query(ClassSubject).count()==21
        leads=db.query(ConsultationLead).filter(ConsultationLead.email.like('demo.lead%@example.com')).all()
        assert len(leads)==12
        assert all(UpdateProfileRequest.validate_phone(item.phone)==item.phone for item in leads)
        assert len([item for item in leads if item.assignee_id is not None])==8
        assert db.query(LeadAssignmentHistory).count()==8
        assert all(count==0 for count in seed_demo_data(db,today=date(2026,10,7)).values());db.commit()


def test_seed_preserves_edits_removed_links_deleted_leads_and_original_records(demo):
    with SessionLocal() as db:
        program=db.query(TrainingProgram).filter_by(code='DEMO-PY').one()
        program.course_name='Tên đã chỉnh sửa';program.standard_tuition=123456
        link=db.query(CurriculumSubject).filter_by(curriculum_id=program.course_id).first();db.delete(link)
        lead=db.query(ConsultationLead).filter_by(email='demo.lead01@example.com').one()
        lead.deleted_at=lead.created_at
        lesson=db.query(SubjectLesson).first();lesson.topic='Chủ đề đã chỉnh sửa'
        db.commit()
        assert all(count==0 for count in seed_demo_data(db,today=date(2026,12,1)).values());db.commit()
        assert program.course_name=='Tên đã chỉnh sửa' and program.standard_tuition==123456
        assert db.query(CurriculumSubject).filter_by(curriculum_id=program.course_id).count()==3
        assert lead.deleted_at is not None and lesson.topic=='Chủ đề đã chỉnh sửa'


def test_classes_show_program_teacher_subjects_filter_and_pagination(demo):
    c=demo
    result=req(c,'GET','/api/classes',role='manager',params={'search':'[Mẫu]','page_size':2}).json()
    assert result['total']==6 and len(result['items'])==2
    all_items=req(c,'GET','/api/classes',role='manager',params={'search':'[Mẫu]','page_size':100}).json()['items']
    python=next(item for item in all_items if item['class_name']=='[Mẫu] Python P01')
    assert python['program_code']=='DEMO-PY' and python['instructor_name']==c['prefix']+'instructor'
    assert len(python['subjects'])==4 and python['start_date']=='2026-09-12'
    assert req(c,'GET','/api/classes',role='manager',params={'search':'[Mẫu]','status':'planned'}).json()['total']==2
    assert req(c,'GET','/api/classes',role='manager',params={'search':'[Mẫu]','course_id':python['course_id']}).json()['total']==2
    assert req(c,'GET','/api/classes',role='admin',params={'search':'DEMO-WEB'}).json()['total']==1
    assert req(c,'GET','/api/classes',role='manager',params={'search':'%'}).json()['total']==0


def test_instructor_only_sees_assigned_classes_and_actual_roles_override_token(demo):
    result=req(demo,'GET','/api/classes',role='instructor',params={'search':'[Mẫu]'}).json()
    assert result['total']==5
    assert all(item['instructor_id']==demo['users'][4] for item in result['items'])
    assert req(demo,'GET','/api/classes',role='instructor',params={'status':'cancelled'}).json()['total']==0


@pytest.mark.parametrize('role',['student','admissions'])
def test_classes_require_permission(demo,role):
    assert req(demo,'GET','/api/classes',role=role).status_code==403
    assert demo['client'].get('/api/classes').status_code==401


@pytest.mark.parametrize('params',[{'status':'unknown'},{'course_id':0},{'page':0},{'page_size':101},{'search':'A'*101}])
def test_invalid_class_filters(demo,params):
    assert req(demo,'GET','/api/classes',role='manager',params=params).status_code==422


def test_candidates_endpoint_only_shows_matching_unlocked_roles(demo):
    c = demo
    res = req(c, 'GET', '/api/classes/candidates', role='manager')
    assert res.status_code == 200
    data = res.json()
    assert 'instructors' in data and 'assistants' in data
    inst_ids = {u['user_id'] for u in data['instructors']}
    ta_ids = {u['user_id'] for u in data['assistants']}
    # INSTRUCTOR user from fixture
    assert c['users'][4] in inst_ids
    # Student and admissions must NOT be in instructors
    assert c['users'][3] not in inst_ids
    assert c['users'][0] not in inst_ids
    assert c['users'][3] not in ta_ids


def test_assign_lecturer_and_multiple_assistants_by_subject(demo):
    c = demo
    # Get a class
    all_classes = req(c, 'GET', '/api/classes', role='manager', params={'search': '[Mẫu] Python P01'}).json()['items']
    p01 = all_classes[0]
    class_id = p01['class_id']
    subject = p01['subjects'][0]
    subject_id = subject['subject_id']

    # Fetch candidates
    candidates = req(c, 'GET', '/api/classes/candidates', role='manager').json()
    instructor = candidates['instructors'][0]
    assert len(candidates['assistants']) >= 1
    ta_ids = [a['user_id'] for a in candidates['assistants'][:2]]

    # Assign 1 lecturer and multiple assistants to this subject
    assign_payload = {
        'instructor_id': instructor['user_id'],
        'assistant_ids': ta_ids
    }
    assign_res = req(c, 'PUT', f'/api/classes/{class_id}/subjects/{subject_id}/assignment',
                     role='manager', json=assign_payload)
    assert assign_res.status_code == 200
    assigned_subj = assign_res.json()
    assert assigned_subj['instructor_id'] == instructor['user_id']
    assert assigned_subj['instructor_name'] == instructor['full_name']
    assert len(assigned_subj['assistants']) == len(ta_ids)
    assert {a['user_id'] for a in assigned_subj['assistants']} == set(ta_ids)

    # Check via class assignments detail endpoint
    detail = req(c, 'GET', f'/api/classes/{class_id}/assignments', role='manager').json()
    assert detail['class_id'] == class_id
    detail_subj = next(s for s in detail['subjects'] if s['subject_id'] == subject_id)
    assert detail_subj['instructor_id'] == instructor['user_id']
    assert len(detail_subj['assistants']) == len(ta_ids)

    # Check via class list
    refreshed = req(c, 'GET', '/api/classes', role='manager', params={'search': '[Mẫu] Python P01'}).json()['items'][0]
    refreshed_subj = next(s for s in refreshed['subjects'] if s['subject_id'] == subject_id)
    assert refreshed_subj['instructor_id'] == instructor['user_id']
    assert len(refreshed_subj['assistants']) == len(ta_ids)


def test_assigned_instructor_sees_class_in_their_list(demo):
    c = demo
    from app.models.user import User
    from app.models.user_role import UserRole
    from app.core.security import create_access_token

    # Create a fresh 2nd instructor who has NO classes assigned initially
    with SessionLocal() as db:
        new_inst = User(username=c['prefix']+'inst2', full_name=c['prefix']+' Giảng Viên 2',
                        email=c['prefix']+'inst2@example.com', password='unused', role_id=2, is_locked=False)
        db.add(new_inst); db.flush()
        db.add(UserRole(user_id=new_inst.user_id, role_id=2))
        db.commit()
        inst2_id = new_inst.user_id

    inst2_header = {'Authorization': 'Bearer ' + create_access_token(inst2_id, 'INSTRUCTOR')}

    # Initial check: instructor 2 has 0 classes
    initial_res = c['client'].get('/api/classes', headers=inst2_header).json()
    assert initial_res['total'] == 0

    # Manager assigns instructor 2 to a subject in React W01
    react_class = req(c, 'GET', '/api/classes', role='manager', params={'search': '[Mẫu] React W01'}).json()['items'][0]
    class_id = react_class['class_id']
    subj_id = react_class['subjects'][0]['subject_id']

    assign_res = req(c, 'PUT', f'/api/classes/{class_id}/subjects/{subj_id}/assignment', role='manager',
                     json={'instructor_id': inst2_id, 'assistant_ids': []})
    assert assign_res.status_code == 200

    # Now Instructor 2 sees React W01 in their class list!
    after_res = c['client'].get('/api/classes', headers=inst2_header).json()
    assert after_res['total'] == 1
    assert after_res['items'][0]['class_id'] == class_id


def test_assignment_validation_rejects_invalid_roles_and_conflicts(demo):
    c = demo
    all_classes = req(c, 'GET', '/api/classes', role='manager', params={'search': '[Mẫu] Python P01'}).json()['items']
    class_id = all_classes[0]['class_id']
    subj_id = all_classes[0]['subjects'][0]['subject_id']

    student_id = c['users'][3]
    instructor_id = c['users'][4]

    # 1. Non-instructor as main lecturer -> 422
    res = req(c, 'PUT', f'/api/classes/{class_id}/subjects/{subj_id}/assignment', role='manager',
              json={'instructor_id': student_id, 'assistant_ids': []})
    assert res.status_code == 422
    assert 'vai trò Giảng viên' in res.json()['detail']

    # 2. Non-assistant as assistant -> 422
    res2 = req(c, 'PUT', f'/api/classes/{class_id}/subjects/{subj_id}/assignment', role='manager',
               json={'instructor_id': instructor_id, 'assistant_ids': [student_id]})
    assert res2.status_code == 422
    assert 'vai trò Trợ giảng' in res2.json()['detail']

    # 3. Main lecturer simultaneously assistant -> 422
    candidates = req(c, 'GET', '/api/classes/candidates', role='manager').json()
    ta_id = candidates['assistants'][0]['user_id']
    # If same user is in both instructor and assistant_ids
    res3 = req(c, 'PUT', f'/api/classes/{class_id}/subjects/{subj_id}/assignment', role='manager',
               json={'instructor_id': ta_id, 'assistant_ids': [ta_id]})
    assert res3.status_code == 422


def test_batch_assignment_updates_multiple_subjects(demo):
    c = demo
    all_classes = req(c, 'GET', '/api/classes', role='manager', params={'search': '[Mẫu] Python P01'}).json()['items']
    p01 = all_classes[0]
    class_id = p01['class_id']
    subjects = p01['subjects']
    assert len(subjects) >= 2

    candidates = req(c, 'GET', '/api/classes/candidates', role='manager').json()
    inst_id = candidates['instructors'][0]['user_id']
    ta_ids = [a['user_id'] for a in candidates['assistants']]

    batch_payload = {
        'assignments': [
            {'subject_id': subjects[0]['subject_id'], 'instructor_id': inst_id, 'assistant_ids': ta_ids[:1]},
            {'subject_id': subjects[1]['subject_id'], 'instructor_id': inst_id, 'assistant_ids': ta_ids[:2]},
        ]
    }
    batch_res = req(c, 'PUT', f'/api/classes/{class_id}/assignments', role='manager', json=batch_payload)
    assert batch_res.status_code == 200
    res_subjects = {s['subject_id']: s for s in batch_res.json()['subjects']}
    assert res_subjects[subjects[0]['subject_id']]['instructor_id'] == inst_id
    assert len(res_subjects[subjects[0]['subject_id']]['assistants']) == 1
    assert res_subjects[subjects[1]['subject_id']]['instructor_id'] == inst_id
    assert len(res_subjects[subjects[1]['subject_id']]['assistants']) == min(2, len(ta_ids))
