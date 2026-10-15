import re
import uuid
from datetime import datetime, timedelta
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text, inspect
from app.main import app
from app.database import SessionLocal
from app.core.security import create_access_token
from app.crud import consultations
from app.models.consultation import ConsultationLead, ConsultationChallenge, ConsultationRateBucket
from app.models.user import User
from app.models.user_role import UserRole
from app.models.role_permission import RolePermission
from app.models.lead_assignment import LeadAssignmentHistory
from app.models.lead_care import LeadCareLog
from app.migrations import ensure_lead_columns, seed_lead_permission


@pytest.fixture
def ctx(monkeypatch):
    prefix = 'T37-' + uuid.uuid4().hex[:12]
    phones, users, headers = set(), [], {}
    host = prefix
    clock = [datetime(2026, 10, 7, 3, 0, 0)]
    monkeypatch.setattr(consultations, 'utcnow', lambda: clock[0])
    with SessionLocal() as db:
        for role, role_id in [('admissions',6),('manager',5),('admin',1),('student',3),('instructor',2)]:
            user = User(username=prefix+role,full_name=prefix+role,email=prefix+role+'@example.com',password='unused',role_id=role_id)
            db.add(user); db.flush(); users.append(user.user_id)
            db.add(UserRole(user_id=user.user_id,role_id=role_id))
            # The claim is deliberately untrusted. Authorization must use database roles.
            headers[role] = {'Authorization':'Bearer '+create_access_token(user.user_id,'TRAINING_MANAGER')}
        db.commit()
    def phone():
        value = '09'+str(uuid.uuid4().int%100000000).zfill(8); phones.add(value); return value
    with TestClient(app,client=(host,12345)) as client:
        yield {'client':client,'prefix':prefix,'headers':headers,'users':users,'phone':phone,'phones':phones,'clock':clock}
    with SessionLocal() as db:
        db.query(ConsultationChallenge).filter(ConsultationChallenge.client_key==consultations.fingerprint('client:'+host)).delete(synchronize_session=False)
        lead_ids = [row[0] for row in db.query(ConsultationLead.lead_id).filter(ConsultationLead.full_name.like(prefix+'%'))]
        db.query(LeadCareLog).filter(LeadCareLog.lead_id.in_(lead_ids)).delete(synchronize_session=False)
        db.query(LeadAssignmentHistory).filter(LeadAssignmentHistory.lead_id.in_(lead_ids)).delete(synchronize_session=False)
        db.query(ConsultationLead).filter(ConsultationLead.full_name.like(prefix+'%')).delete(synchronize_session=False)
        keys = [consultations.fingerprint(action+':'+host) for action in ['challenge','submission']]+[consultations.fingerprint('phone:'+p) for p in phones]
        db.query(ConsultationRateBucket).filter(ConsultationRateBucket.key.in_(keys)).delete(synchronize_session=False)
        db.query(UserRole).filter(UserRole.user_id.in_(users)).delete(synchronize_session=False)
        db.query(User).filter(User.user_id.in_(users)).delete(synchronize_session=False)
        db.commit()


def req(ctx, method, path='/api/leads', role='admissions', **kwargs):
    return ctx['client'].request(method,path,headers=ctx['headers'][role],**kwargs)


def payload(ctx, **changes):
    return {'full_name':ctx['prefix']+' Khách tư vấn','phone':ctx['phone'](),'email':'khach@example.com','source':'Facebook','interest':'Python',**changes}


def create(ctx, **changes):
    result = req(ctx,'POST',json=payload(ctx,**changes)); assert result.status_code==201,result.text
    return result.json()


def test_admissions_create_read_update_and_trim_normalize(ctx):
    phone = ctx['phone']()
    item = create(ctx, full_name='  '+ctx['prefix']+' Khách  ',phone='+84'+phone[1:],source='  Giới thiệu  ')
    assert item['phone']==phone and item['full_name']==ctx['prefix']+' Khách'
    assert item['status']=='new' and item['source']=='Giới thiệu' and item['duplicate_count']==0
    url = f"/api/leads/{item['lead_id']}"
    assert req(ctx,'GET',url).json()==item
    updated = req(ctx,'PUT',url,json=payload(ctx,full_name=ctx['prefix']+' Mới',phone=phone,email=' ',source='Điện thoại',interest=' '))
    assert updated.status_code==200
    result = updated.json()
    assert result['email'] is None and result['interest'] is None and result['source']=='Điện thoại'
    assert result['created_at']==item['created_at'] and result['status']=='new'


@pytest.mark.parametrize('field,value',[('full_name',' '),('full_name','A'*101),('phone',''),('phone','0123456789'),('phone','123'),('phone',None),
    ('email','not-email'),('email','a'*256+'@example.com'),('source',' '),('source','A'*101),('source',None),('interest','A'*256),
    ('confirm_duplicate','true'),('confirm_duplicate',1),('status','converted'),('deleted_at','2026-01-01'),('lead_id',1),('message','Cannot override public message')])
def test_backend_rejects_invalid_fields_and_protected_fields(ctx,field,value):
    assert req(ctx,'POST',json=payload(ctx,**{field:value})).status_code==422
    assert req(ctx,'GET',params={'search':ctx['prefix']}).json()['total']==0


def test_duplicate_warning_requires_explicit_confirmation_and_counts(ctx):
    first = create(ctx)
    body = payload(ctx,phone='+84'+first['phone'][1:])
    warning = req(ctx,'POST',json=body)
    assert warning.status_code==409 and warning.json()['detail']['total']==1
    assert warning.json()['detail']['duplicates'][0]['lead_id']==first['lead_id']
    assert req(ctx,'GET',params={'search':ctx['prefix']}).json()['total']==1
    second = req(ctx,'POST',json={**body,'confirm_duplicate':True})
    assert second.status_code==201 and second.json()['duplicate_count']==1
    assert req(ctx,'GET',f"/api/leads/{first['lead_id']}").json()['duplicate_count']==1
    check = req(ctx,'GET','/api/leads/check-phone',params={'phone':first['phone']}).json()
    assert check['total']==2
    check = req(ctx,'GET','/api/leads/check-phone',params={'phone':first['phone'],'exclude_lead_id':first['lead_id']}).json()
    assert check['total']==1


def test_update_phone_warns_excludes_self_and_failure_is_atomic(ctx):
    first,second = create(ctx),create(ctx)
    body = payload(ctx,phone=second['phone'],full_name=ctx['prefix']+' Không lưu')
    url = f"/api/leads/{first['lead_id']}"
    assert req(ctx,'PUT',url,json=body).status_code==409
    assert req(ctx,'GET',url).json()==first
    assert req(ctx,'PUT',url,json={**body,'phone':first['phone']}).status_code==200
    assert req(ctx,'PUT',url,json={**body,'confirm_duplicate':True}).status_code==200
    assert req(ctx,'GET',url).json()['duplicate_count']==1


def test_duplicate_preflight_is_authorized_and_validates_phone(ctx):
    first = create(ctx)
    assert req(ctx,'GET','/api/leads/check-phone',params={'phone':'+84'+first['phone'][1:]}).json()['total']==1
    for phone in ['','123',' ']:
        assert req(ctx,'GET','/api/leads/check-phone',params={'phone':phone}).status_code in [400,422]
    assert req(ctx,'GET','/api/leads/check-phone',params={'phone':first['phone'],'exclude_lead_id':999999}).status_code==404


def test_search_all_fields_source_filter_pagination_and_literal_wildcards(ctx):
    items = [create(ctx,full_name=ctx['prefix']+f' Khách {index}',source='Giới thiệu' if index%2 else 'Facebook',interest='Python',email=f'{ctx["prefix"]}{index}@example.com') for index in range(22)]
    first = req(ctx,'GET',params={'search':ctx['prefix'],'page_size':20}).json()
    second = req(ctx,'GET',params={'search':ctx['prefix'],'page':2,'page_size':20}).json()
    assert first['total']==22 and len(first['items'])==20 and len(second['items'])==2
    assert not {i['lead_id'] for i in first['items']} & {i['lead_id'] for i in second['items']}
    for term in [items[0]['phone'],'+84'+items[0]['phone'][1:],items[0]['email']]:
        assert req(ctx,'GET',params={'search':term}).json()['total']==1
    assert req(ctx,'GET',params={'search':ctx['prefix'],'source':'Giới thiệu'}).json()['total']==11
    for term in ['%','_']:
        assert req(ctx,'GET',params={'search':term}).json()['total']==0
    for params in [{'page':0},{'page_size':101},{'search':'A'*101}]:
        assert req(ctx,'GET',params=params).status_code==422


@pytest.mark.parametrize('role',['admissions','admin'])
def test_only_training_manager_can_delete_even_with_forged_role_claim(ctx,role):
    item = create(ctx)
    url=f"/api/leads/{item['lead_id']}"
    assert req(ctx,'DELETE',url,role=role).status_code==403
    assert req(ctx,'GET',url).status_code==200


def test_manager_soft_delete_keeps_row_and_hides_from_lists_duplicates(ctx):
    item=create(ctx); url=f"/api/leads/{item['lead_id']}"
    assert req(ctx,'DELETE',url,role='manager').status_code==200
    assert req(ctx,'GET',url).status_code==404
    assert req(ctx,'PUT',url,json=payload(ctx)).status_code==404
    assert req(ctx,'DELETE',url,role='manager').status_code==404
    assert req(ctx,'GET',params={'search':ctx['prefix']}).json()['total']==0
    assert req(ctx,'GET','/api/leads/check-phone',params={'phone':item['phone']}).json()['total']==0
    with SessionLocal() as db:
        row=db.get(ConsultationLead,item['lead_id']); assert row.deleted_at is not None and row.phone==item['phone']
    assert create(ctx,phone=item['phone'])['duplicate_count']==0


def test_manager_role_is_read_from_user_roles_not_primary_or_token(ctx):
    item=create(ctx); url=f"/api/leads/{item['lead_id']}"
    with SessionLocal() as db:
        db.add(UserRole(user_id=ctx['users'][2],role_id=5)); db.commit()
    assert req(ctx,'DELETE',url,role='admin').status_code==200
    item=create(ctx); url=f"/api/leads/{item['lead_id']}"
    with SessionLocal() as db:
        # Retain LEAD_MANAGE through admissions; remove TRAINING_MANAGER membership.
        db.add(UserRole(user_id=ctx['users'][1],role_id=6))
        db.query(UserRole).filter_by(user_id=ctx['users'][1],role_id=5).delete(); db.commit()
    assert req(ctx,'DELETE',url,role='manager').status_code==403


@pytest.mark.parametrize('role',['student','instructor'])
def test_unauthorized_roles_cannot_read_or_write_leads(ctx,role):
    item=create(ctx); url=f"/api/leads/{item['lead_id']}"
    for method,path,body in [('GET','/api/leads',None),('GET',url,None),('GET','/api/leads/check-phone?phone='+item['phone'],None),
        ('POST','/api/leads',payload(ctx)),('PUT',url,payload(ctx)),('DELETE',url,None)]:
        assert req(ctx,method,path,role=role,**({'json':body} if body else {})).status_code==403
    assert ctx['client'].get('/api/leads').status_code==401


def test_public_lead_visible_with_source_and_update_preserves_message_status(ctx):
    proof=ctx['client'].get('/api/public/consultations/challenge').json()
    ctx['clock'][0]+=timedelta(seconds=2)
    phone=ctx['phone']()
    body={'full_name':ctx['prefix']+' Công khai','phone':phone,'interest':'Python','message':'Gọi sau 17 giờ',
          'challenge_token':proof['challenge_token'],'challenge_answer':str(sum(map(int,re.findall(r'\d+',proof['question']))))}
    assert ctx['client'].post('/api/public/consultations',json=body).status_code==201
    assert req(ctx,'GET',params={'search':phone}).json()['total']==0  # Public leads await assignment.
    item=req(ctx,'GET',role='manager',params={'search':phone}).json()['items'][0]
    assert item['source']=='Biểu mẫu công khai' and item['message']=='Gọi sau 17 giờ' and item['status']=='new'
    result=req(ctx,'PUT',f"/api/leads/{item['lead_id']}",role='manager',json=payload(ctx,phone=phone,source='Điện thoại'))
    assert result.status_code==200 and result.json()['message']==item['message'] and result.json()['created_at']==item['created_at']
    assert req(ctx,'DELETE',f"/api/leads/{item['lead_id']}",role='manager').status_code==200
    # Soft deletion keeps the public challenge reference/retry safe.
    assert ctx['client'].post('/api/public/consultations',json=body).status_code==200


def test_duplicate_legacy_international_phone_is_detected(ctx):
    phone=ctx['phone']()
    with SessionLocal() as db:
        db.add(ConsultationLead(full_name=ctx['prefix']+' Cũ',phone=' +84'+phone[1:]+' ',source='Nhập thủ công',created_at=ctx['clock'][0])); db.commit()
    assert req(ctx,'POST',json=payload(ctx,phone=phone)).status_code==409
    warning = req(ctx,'GET','/api/leads/check-phone',params={'phone':phone}).json()
    assert warning['total']==0 and warning['duplicates']==[] and warning['hidden_match'] is True


def test_permission_revocation_is_not_regranted_on_seed(ctx):
    with SessionLocal() as db:
        row=db.query(RolePermission).filter_by(role_id=6,permission_id=5).one()
        db.delete(row); db.commit()
    try:
        seed_lead_permission()
        assert req(ctx,'GET').status_code==403
    finally:
        with SessionLocal() as db:
            db.add(RolePermission(role_id=6,permission_id=5)); db.commit()


def test_migration_adds_source_and_soft_delete_idempotently_without_changing_old_lead():
    engine=create_engine('sqlite://')
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE consultation_leads (lead_id INTEGER PRIMARY KEY, full_name VARCHAR(100), phone VARCHAR(20), status VARCHAR(20))'))
        connection.execute(text("INSERT INTO consultation_leads VALUES (1, 'Khách cũ', '0912345678', 'new')"))
    ensure_lead_columns(engine); ensure_lead_columns(engine)
    with engine.connect() as connection:
        row=connection.execute(text('SELECT * FROM consultation_leads')).mappings().one()
        assert row['full_name']=='Khách cũ' and row['phone']=='0912345678' and row['source']=='Biểu mẫu công khai' and row['deleted_at'] is None
    assert {'source','deleted_at'} <= {c['name'] for c in inspect(engine).get_columns('consultation_leads')}
    engine.dispose()
