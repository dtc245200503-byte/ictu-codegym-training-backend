from datetime import date
import pytest
from test_student_enrollments import ctx, attendance_ctx, change
from test_attendance import payload
from app.models.user import User
from app.models.student_profile import StudentProfileDetails
from app.models.training_program import TrainingClass
from app.models.student_enrollment import StudentEnrollment
from app.models.attendance import StudentClass
from app.models.role_permission import RolePermission


def get(req, student=4):
    return req('GET', f'/api/students/{student}', actor=1).json()


def body(req, student=4, **overrides):
    current = get(req, student)
    return {'full_name': current['student_name'], 'email': current['email'],
            'phone': current['phone'], 'date_of_birth': current['date_of_birth'], 'address': current['address'],
            'entry_level': current['entry_level'], 'notes': current['notes'],
            'expected_token': current['profile_token'], **overrides}


def put(req, data, actor=1, student=4):
    return req('PUT', f'/api/students/{student}', actor=actor, json=data)


def test_edit_personal_details_and_clear_optional_fields(ctx):
    req, sessions, _, engine = ctx
    data = body(req, full_name='  Nguyễn An  ', email='new@example.com', phone='+84912345678',
                date_of_birth='2000-05-01', address='  Hà Nội ', entry_level=' A2 ', notes=' Cần cấp lại bảng điểm\nLớp cũ năm 2025 ')
    response = put(req, data)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value['student_name'] == 'Nguyễn An' and value['student_code'] == 'HV4'
    assert value['phone'] == '0912345678' and value['address'] == 'Hà Nội'
    assert value['entry_level'] == 'A2' and value['notes'].startswith('Cần cấp lại')
    assert value['updated_by_name'] == 'Người 1' and value['updated_at'] == '2026-10-16T03:00:00+00:00'
    assert value['profile_token'] != data['expected_token']
    from app.database import Base
    Base.metadata.create_all(engine)
    with sessions() as db:
        assert db.get(User,4).email == 'new@example.com'
        assert db.get(StudentProfileDetails,4).entry_level == 'A2'
    response = put(req, body(req, phone='', date_of_birth=None, address=' ', entry_level='', notes=' '))
    assert response.status_code == 200
    assert all(response.json()[field] is None for field in ['phone','date_of_birth','address','entry_level','notes'])


@pytest.mark.parametrize('field,value', [('full_name',' '), ('full_name','a'*101), ('email','invalid'), ('email',None), ('phone','123'), ('phone','+12345'), ('date_of_birth','2026-10-17'), ('date_of_birth','2026-02-30'), ('date_of_birth','0001-01-01'), ('date_of_birth',123), ('address','a'*256), ('entry_level','a'*201), ('notes','a'*5001), ('expected_token','bad'), ('username','OTHER'), ('role_id',1)])
def test_invalid_and_identity_fields_rejected_without_mutation(ctx,field,value):
    req,sessions,_,_=ctx
    assert put(req,body(req,**{field:value})).status_code == 422
    with sessions() as db:
        assert db.get(User,4).full_name == 'Người 4'
        assert db.get(StudentProfileDetails,4) is None


def test_stale_edit_and_changes_from_other_account_routes_are_detected(ctx):
    req,sessions,_,_=ctx
    original=body(req,entry_level='A2')
    assert put(req,original).status_code == 200
    assert put(req,{**original,'notes':'Lost update'}).status_code == 412
    current=body(req,notes='new note')
    with sessions() as db:
        db.get(User,4).phone='0987654321'
        db.commit()
    assert put(req,current).status_code == 412
    assert get(req)['notes'] is None
    assert get(req)['phone'] == '0987654321'


def test_duplicate_email_rolls_back_personal_and_extra_information(ctx):
    req,sessions,_,_=ctx
    response=put(req,body(req,full_name='Changed',email='u5@example.com',notes='not saved'))
    assert response.status_code == 409
    with sessions() as db:
        assert db.get(User,4).full_name == 'Người 4'
        assert db.get(StudentProfileDetails,4) is None


@pytest.mark.parametrize('actor,code',[(None,401),(2,403),(4,403),(9,403),(10,403)])
def test_profile_edits_require_manager_and_permission(ctx,actor,code):
    req,sessions,_,_=ctx
    # Even if the role has STUDENT_MANAGE, a teacher/student/admin is not a training manager.
    with sessions() as db:
        for role in [2,3,4,5]: db.add(RolePermission(role_id=role,permission_id=3))
        db.commit()
    assert put(req,body(req,notes='forbidden'),actor=actor).status_code == code


def test_revoked_permission_and_unknown_student(ctx):
    req,sessions,_,_=ctx
    data=body(req)
    assert put(req,data,student=999).status_code == 404
    assert put(req,data,student=2).status_code == 404
    with sessions() as db:
        db.query(RolePermission).filter_by(role_id=1,permission_id=3).delete()
        db.commit()
    assert put(req,data).status_code == 403


@pytest.mark.parametrize('term',['HV4','Người 4','0912345678','+84 912-345-678','0912 345 678','3456'])
def test_search_code_name_phone(ctx,term):
    req,sessions,_,_=ctx
    with sessions() as db:
        db.get(User,4).phone='+84 912-345-678'
        db.commit()
    response=req('GET','/api/students',actor=1,params={'search':term,'class_id':1,'page_size':1})
    assert response.status_code == 200
    assert response.json()['total'] == 1
    assert response.json()['items'][0]['student_id'] == 4
    assert req('GET','/api/students',actor=1,params={'search':'%'}).json()['total'] == 0


def test_current_upcoming_completed_and_formal_history_are_separate(ctx):
    req,sessions,_,_=ctx
    with sessions() as db:
        db.add_all([TrainingClass(class_id=3,class_name='Finished',status='completed'),TrainingClass(class_id=4,class_name='Next',status='planned')])
        db.flush()
        db.add_all([StudentClass(student_id=4,class_id=3,enrolled_date=date(2025,1,1)),StudentClass(student_id=4,class_id=4,enrolled_date=date(2026,10,16))])
        db.commit()
    rows={row['class_id']:row for row in get(req)['enrollments']}
    assert rows[1]['group']=='current'
    assert rows[3]['group']=='history' and rows[3]['display_status']=='completed' and not rows[3]['can_change']
    assert rows[4]['group']=='upcoming'
    assert change(ctx,'transferred',to_class_id=2).status_code == 201
    rows={row['class_id']:row for row in get(req)['enrollments']}
    assert rows[1]['group']=='history' and rows[2]['group']=='current'


def test_personal_edits_preserve_class_and_attendance_snapshots(ctx):
    req,_,_,_=ctx
    assert req('PUT',json=payload()).status_code == 200
    assert change(ctx,'deferred').status_code == 201
    before=req('GET','/api/students/4/history',actor=1).json()
    assert put(req,body(req,full_name='New name',notes='New notes')).status_code == 200
    assert req('GET','/api/students/4/history',actor=1).json() == before
    records=req('GET','/sessions/1',actor=1).json()
    assert next(row for row in records['archived_students'] if row['student_id']==4)['student_name']=='Người 4'
