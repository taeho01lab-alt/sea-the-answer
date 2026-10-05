import io
import json
import math
import pytest
import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select
from service.app import create_app
from service.storage import User,Vessel,Document,Audit
from service.security import password_hash
from service.retrieval import Search,pdf_sections,chunk_sections
from service.llm import Gateway

PW='only-for-tests-12345'
SCOPE={'vessel_id':'HAE-01','start':'2026-09-11','end':'2026-09-17'}
RECORD={'vessel_id':'HAE-01','date':'2026-09-11','voyage':'TEST-01','status':'Under way',
        'latitude':35.,'longitude':129.,'speed_kn':12.,'distance_nm':100.,'fuel_t':100.,'factor':3.,'engine_hours':24.,'draft_m':10.,'weather':'Fair'}
DOC={'logical_id':'sulphur','title':'Training sulphur guidance','version':'1','kind':'sample',
     'sections':[{'page':7,'section':'Regulation 14','text':'Training evidence only: fuel sulphur limit is 0.10 percent in this fictional ECA.'}]}

@pytest.fixture
def app(tmp_path):
    app=create_app('sqlite:///'+str(tmp_path/'db.sqlite'),Search(vector_enabled=False),Gateway(env={}))
    with app.state.sessions() as db:
        db.add_all([Vessel(id='HAE-01',name='Ship A',dwt=1000.,ship_type='bulk',sample=True),Vessel(id='HAE-02',name='Ship B',dwt=2000.,ship_type='bulk',sample=True)])
        db.flush()
        db.add_all([User(username='admin',role='admin',vessels=[],password_hash=password_hash(PW)),
                    User(username='captain',role='captain',vessels=['HAE-01'],password_hash=password_hash(PW)),
                    User(username='crew',role='crew',vessels=['HAE-01'],password_hash=password_hash(PW)),
                    User(username='other',role='crew',vessels=['HAE-02'],password_hash=password_hash(PW))]); db.commit()
    yield app
    app.state.engine.dispose()

def login(app,name='admin'):
    client=TestClient(app)
    result=client.post('/api/login',json={'username':name,'password':PW})
    assert result.status_code==200,result.text
    client.headers['X-CSRF-Token']=result.json()['csrf']; return client

def seed(client):
    assert client.post('/api/records',json=RECORD).status_code==200
    response=client.post('/api/documents',json=DOC)
    assert response.status_code==200,response.text
    return response.json()['document']['id']

def test_authentication_csrf_origin_logout(app):
    anon=TestClient(app)
    for path in ['/api/documents','/api/vessels','/api/reports','/api/history']: assert anon.get(path).status_code==401
    assert anon.post('/api/login',json={'username':'admin','password':'wrong'}).status_code==401
    client=login(app)
    assert 'HttpOnly' in client.cookies.jar._cookies['testserver.local']['/']['haedap_session']._rest
    assert client.post('/api/records',json=RECORD,headers={'X-CSRF-Token':''}).status_code==403
    assert client.post('/api/records',json=RECORD,headers={'Origin':'https://evil.example'}).status_code==403
    assert client.get('/api/health',headers={'Host':'evil.example'}).status_code==400
    assert client.post('/api/logout',json={}).status_code==200
    assert client.get('/api/me').status_code==401

def test_login_rate_limit(app):
    client=TestClient(app)
    for _ in range(10): assert client.post('/api/login',json={'username':'bad','password':'bad'}).status_code==401
    assert client.post('/api/login',json={'username':'bad','password':'bad'}).status_code==429

def test_role_and_vessel_scope(app):
    admin=login(app); seed(admin); crew=login(app,'crew'); captain=login(app,'captain')
    assert [v['id'] for v in crew.get('/api/vessels').json()]==['HAE-01']
    assert crew.post('/api/records',json=RECORD).status_code==403
    assert crew.get('/api/users').status_code==403
    assert crew.post('/api/metrics',json={**SCOPE,'vessel_id':'HAE-02'}).status_code==403
    assert captain.post('/api/records',json={**RECORD,'vessel_id':'HAE-02'}).status_code==403

def test_restricted_evidence_never_enters_search_or_reports(app):
    admin=login(app); seed(admin)
    restricted={**DOC,'logical_id':'secret','restricted':True,'title':'Secret','sections':[{'page':1,'section':'secret','text':'Neptune confidential passwordless unique term.'}]}
    docid=admin.post('/api/documents',json=restricted).json()['document']['id']
    crew=login(app,'crew')
    assert all(d['id']!=docid for d in crew.get('/api/documents').json())
    assert crew.get('/api/documents/'+docid).status_code==404
    answer=crew.post('/api/ask',json={**SCOPE,'question':'Neptune confidential'}).json()
    assert answer['evidence']==[] and answer['insufficient']
    report=crew.post('/api/reports',json={**SCOPE,'kind':'Noon Report','question':'Neptune confidential'}).json()
    assert 'Neptune' not in report['text']

@pytest.mark.parametrize('change',[{'fuel_t':-1.},{'distance_nm':0.},{'fuel_t':'100'},{'date':'2026-02-30'},{'engine_hours':25.},{'latitude':91.},{'factor':0.}])
def test_bad_noon_input_never_persists(app,change):
    client=login(app)
    assert client.post('/api/records',json={**RECORD,**change}).status_code==422
    assert client.get('/api/records',params=SCOPE).json()['records']==[]

def test_python_numbers_period_scope_and_determinism(app):
    client=login(app); seed(client)
    client.post('/api/records',json={**RECORD,'date':'2026-09-20','fuel_t':999.})
    result=client.post('/api/metrics',json=SCOPE).json()
    assert result['emission_t']==300. and result['intensity']==3000.
    assert result['fuel_t']==100. and result['inputs']['records'][0]['date']=='2026-09-11'
    assert result['official_cii_rating'] is None
    for _ in range(5): assert client.post('/api/metrics',json=SCOPE).json()==result
    assert client.post('/api/metrics',json={**SCOPE,'start':'2026-10-01','end':'2026-10-02'}).status_code==422
    assert client.post('/api/metrics',json={**SCOPE,'start':'2026-10-01'}).status_code==422

def test_document_version_page_withdrawal_and_report_snapshot(app):
    client=login(app); old=seed(client)
    report=client.post('/api/reports',json={**SCOPE,'kind':'MRV Report','question':'sulphur limit'}).json()
    assert 'page 7' in report['text']
    assert client.post('/api/documents',json=DOC).status_code==409
    newer={**DOC,'version':'2','sections':[{'page':8,'section':'Regulation 14','text':'Updated sulphur test evidence, no numerical legal claim.'}]}
    new=client.post('/api/documents',json=newer).json()['document']['id']
    result=client.post('/api/ask',json={**SCOPE,'question':'sulphur limit'}).json()
    assert result['evidence'] and all(e['document_id']==new for e in result['evidence'])
    assert client.get('/api/documents/'+old).json()['status']=='superseded'
    assert client.delete('/api/documents/'+new).status_code==200
    assert client.post('/api/ask',json={**SCOPE,'question':'sulphur limit'}).json()['evidence']==[]
    assert client.get('/api/reports/'+report['id']).json()['snapshot']['evidence'][0]['document_id']==old

def test_report_ownership_and_optimistic_edit(app):
    admin=login(app); seed(admin); crew=login(app,'crew'); other=login(app,'other')
    r=crew.post('/api/reports',json={**SCOPE,'kind':'Noon Report'}).json()
    body={'title':r['title'],'text':r['text']+'\nReviewed','version':1}
    assert crew.put('/api/reports/'+r['id'],json=body).status_code==200
    assert crew.put('/api/reports/'+r['id'],json=body).status_code==409
    assert other.get('/api/reports/'+r['id']).status_code==403
    assert other.get('/api/reports').json()==[]
    exported=crew.get('/api/reports/'+r['id']+'/export?format=json')
    assert exported.status_code==200 and exported.json()['version']==2
    assert 'attachment' in exported.headers['content-disposition']
    assert crew.get('/api/reports/'+r['id']+'/export?format=md').text==body['text']
    assert other.get('/api/reports/'+r['id']+'/export').status_code==403

def test_report_permission_recheck_after_document_restriction(app):
    admin=login(app); docid=seed(admin); crew=login(app,'crew')
    report=crew.post('/api/reports',json={**SCOPE,'kind':'Noon Report','question':'sulphur limit'}).json()
    with app.state.sessions() as db:
        d=db.get(Document,docid); d.metadata_json={**d.metadata_json,'restricted':True}; db.commit()
    assert crew.get('/api/reports/'+report['id']).status_code==403

@pytest.mark.parametrize('question,expected',[
    ('fuel sulphur limit',['search_documents']),('현재 선박 항차 정보',['query_voyage']),
    ('배출량 계산',['query_voyage','calculate_metrics']),('Noon Report 초안 생성',['query_voyage','calculate_metrics','draft_report']),
    ('현재 선박 배출량과 IMO 규정',['search_documents','query_voyage','calculate_metrics'])])
def test_routing_and_offline_workflow(app,question,expected):
    client=login(app); seed(client)
    result=client.post('/api/ask',json={**SCOPE,'question':question}).json()
    assert result['actions']==expected
    assert result['routing']=='rules'
    if 'draft_report' in expected: assert result['draft']['kind']=='Noon Report'

def test_record_mutation_conflict_and_audit(app):
    client=login(app); r=client.post('/api/records',json=RECORD).json()
    url='/api/records/'+r['id']
    assert client.put(url+'?version=1',json={**RECORD,'fuel_t':80.}).status_code==200
    assert client.delete(url+'?version=1').status_code==409
    assert client.delete(url+'?version=2').status_code==200
    actions=[r['action'] for r in client.get('/api/history').json()]
    assert all(a in actions for a in ['record.create','record.update','record.delete'])
    entry=next(r for r in client.get('/api/history').json() if r['action']=='record.update')
    assert entry['detail']['before']['data']['fuel_t']==100.
    assert entry['detail']['after']['data']['fuel_t']==80.

def test_pdf_rejects_scanned_and_corrupt_files():
    from pypdf import PdfWriter
    writer=PdfWriter();writer.add_blank_page(width=200,height=200); stream=io.BytesIO();writer.write(stream)
    with pytest.raises(ValueError,match='OCR'): pdf_sections(stream.getvalue())
    with pytest.raises(ValueError): pdf_sections(b'not a pdf')
    chunks=chunk_sections([{'page':4,'section':'Chapter','text':'Regulation 14\nFuel sulphur table\nFuel A    0.10\nFuel B    0.50'}])
    assert chunks[0]['page']==4 and chunks[0]['section']=='Regulation 14'
    assert 'Fuel A    0.10' in chunks[0]['text']

def test_gateway_validates_citations_and_unknown_tools():
    env={'LLM_ENABLED':'1','LLM_MODEL':'test-model','LLM_BASE_URL':'http://localhost:11434/v1'}
    evidence=[{'id':'chunk-1','text':'The supplied evidence has no official CII rating.'}]
    def transport(request):
        body=json.loads(request.content)
        if 'tools' in body: message={'tool_calls':[{'function':{'name':'delete_database','arguments':'{}'}}]}
        else: message={'content':json.dumps({'insufficient':False,'statements':[{'chunk_id':'fake','quote':'invented quotation','text':'wrong'}]})}
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':message}]})
    g=Gateway(env,httpx.Client(transport=httpx.MockTransport(transport)))
    plan,routing,warnings=g.plan('sulphur limit');assert routing=='rules' and warnings
    with pytest.raises(ValueError,match='citation'):g.answer('CII',evidence,'en')
    with pytest.raises(ValueError):Gateway({'LLM_ENABLED':'1','LLM_MODEL':'x','LLM_BASE_URL':'http://external.example/v1'})

def test_account_restriction_revokes_existing_session(app):
    admin=login(app); crew=login(app,'crew')
    user=next(u for u in admin.get('/api/users').json() if u['username']=='crew')
    assert admin.put('/api/users/'+user['id'],json={'role':'crew','vessels':['HAE-02'],'active':True}).status_code==200
    assert crew.get('/api/me').status_code==401
    crew=login(app,'crew')
    assert [v['id'] for v in crew.get('/api/vessels').json()]==['HAE-02']
    assert admin.put('/api/users/'+user['id'],json={'role':'crew','vessels':[],'active':False}).status_code==200
    assert TestClient(app).post('/api/login',json={'username':'crew','password':PW}).status_code==401

def test_captain_cannot_replace_global_document_or_confuse_ship(app):
    admin=login(app); docid=seed(admin); captain=login(app,'captain')
    assert captain.post('/api/documents',json={**DOC,'version':'2','vessels':['HAE-01']}).status_code==403
    assert captain.delete('/api/documents/'+docid).status_code==403
    assert admin.post('/api/ask',json={**SCOPE,'question':'HAE-02 배출량 계산'}).status_code==422

def test_pdf_upload_preserves_page_and_section(app):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer=PdfWriter(); page=writer.add_blank_page(width=500,height=500)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
    stream=DecodedStreamObject(); stream.set_data(b'BT /F1 12 Tf 30 450 Td (Regulation 14) Tj 0 -20 Td (Training fuel sulphur evidence.) Tj ET')
    page[NameObject('/Contents')]=writer._add_object(stream)
    out=io.BytesIO(); writer.write(out)
    client=login(app)
    result=client.post('/api/documents/pdf',data={'metadata':json.dumps({k:v for k,v in DOC.items() if k!='sections'})},files={'file':('training.pdf',out.getvalue(),'application/pdf')})
    assert result.status_code==200,result.text
    detail=client.get('/api/documents/'+result.json()['document']['id']).json()
    assert detail['chunks'][0]['page']==1 and detail['chunks'][0]['section']=='Regulation 14'

def test_backup_roundtrip_and_nonempty_restore_refusal(app,tmp_path,monkeypatch):
    from service.manage import backup,restore
    from service.storage import database,Report,LoginSession
    from sqlalchemy import func
    client=login(app); seed(client)
    report=client.post('/api/reports',json={**SCOPE,'kind':'Noon Report'}).json()
    monkeypatch.setenv('DATABASE_URL',str(app.state.engine.url))
    path=tmp_path/'backup.json'; backup(path)
    target='sqlite:///'+str(tmp_path/'restored.sqlite'); restore(path,target)
    engine,sessions=database(target)
    with sessions() as db:
        assert db.get(Report,report['id']).snapshot==report['snapshot']
        assert db.scalar(select(func.count()).select_from(LoginSession))==0
    engine.dispose()
    with pytest.raises(ValueError,match='empty'):restore(path,target)
    corrupted=json.loads(path.read_text(encoding='utf-8')); corrupted['payload']['settings']['LLM_MODEL']='changed'
    path.write_text(json.dumps(corrupted),encoding='utf-8')
    with pytest.raises(ValueError,match='checksum'):restore(path,target)

def test_vector_results_cannot_bypass_document_acl(app):
    client=login(app); seed(client)
    from service.storage import Chunk
    class FakeVector:
        def query(self,**kwargs):
            assert kwargs['where']['document_id']['$in']==allowed
            return {'ids':[['unauthorized-chunk',chunk.id]],'distances':[[0.,.1]]}
    with app.state.sessions() as db:
        crew=db.scalar(select(User).where(User.username=='crew'))
        chunk=db.scalar(select(Chunk)); allowed=[chunk.document_id]
        search=Search(vector_enabled=True); search.collection=FakeVector()
        evidence,method,warnings=search.query(db,crew,'fuel sulphur')
        assert method=='bm25+chroma-rrf' and not warnings
        assert [e['id'] for e in evidence]==[chunk.id]
