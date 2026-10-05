import json
import logging
import os
import re
import secrets
import time
from collections import defaultdict, deque
from datetime import date
from typing import Literal
from fastapi import FastAPI, Request, Response, HTTPException, UploadFile, File, Form
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, ConfigDict, ValidationError
from sqlalchemy import select, update, delete, func
from sqlalchemy.exc import IntegrityError
from starlette.middleware.trustedhost import TrustedHostMiddleware
from dotenv import load_dotenv
from .storage import database, User, LoginSession, Vessel, VoyageRecord, Document, Chunk, Report, Audit, audit, rowdict, uid
from .security import password_hash, password_valid, hash_token, session_user, require_role, vessel_access, document_access, public_user
from .calculations import NoonInput, metrics, report_text
from .retrieval import Search, pdf_sections, chunk_sections
from .llm import Gateway
from . import role4
from . import role4_agent
from . import role4_calculations

load_dotenv('.env.local')
log = logging.getLogger('haedap')

class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

class Login(Strict):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)

class Account(Login):
    role: Literal['admin', 'captain', 'crew'] = 'crew'
    vessels: list[str] = Field(default_factory=list, max_length=100)

class AccountUpdate(Strict):
    role: Literal['admin', 'captain', 'crew']
    vessels: list[str] = Field(default_factory=list, max_length=100)
    active: bool = True

class VesselInput(Strict):
    id: str = Field(pattern=r'^[a-zA-Z0-9-]{1,80}$')
    name: str = Field(min_length=1, max_length=120)
    dwt: float = Field(gt=0, le=1e7, strict=True)
    ship_type: str = Field(min_length=1, max_length=80)
    sample: bool = False

class Period(Strict):
    vessel_id: str = Field(min_length=1, max_length=80)
    start: date
    end: date

class Ask(Period):
    question: str = Field(min_length=1, max_length=2000)
    language: Literal['auto', 'ko', 'en'] = 'auto'
    use_model: bool = False

class Draft(Period):
    kind: Literal['Noon Report', 'MRV Report']
    question: str = Field(default='CII 연료 배출량 규정', max_length=2000)

class EditReport(Strict):
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=200000)
    version: int = Field(ge=1, strict=True)

class DocSection(Strict):
    page: int | None = Field(default=None, ge=1, le=10000)
    section: str = Field(default='본문', min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=100000)

class DocMeta(Strict):
    logical_id: str = Field(pattern=r'^[a-zA-Z0-9-]{1,80}$')
    title: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=80)
    kind: Literal['official-summary', 'onboard', 'sample'] = 'onboard'
    issuer: str = Field(default='', max_length=200)
    issued_at: date | None = None
    revised_at: date | None = None
    applicability: str = Field(default='적용 조건 미검증', max_length=500)
    restricted: bool = False
    vessels: list[str] = Field(default_factory=list, max_length=100)
    source_url: str = Field(default='', max_length=2000)

class DocInput(DocMeta):
    sections: list[DocSection] = Field(min_length=1, max_length=250)

def period_records(db, user, p):
    vessel_access(user, p.vessel_id)
    vessel = db.get(Vessel, p.vessel_id)
    if not vessel: raise HTTPException(404, '선박을 찾을 수 없습니다.')
    if p.start > p.end or (p.end-p.start).days > 3660: raise HTTPException(422, '기간을 확인하세요. 최대 10년입니다.')
    records = list(db.scalars(select(VoyageRecord).where(VoyageRecord.vessel_id == p.vessel_id,
                   VoyageRecord.date >= str(p.start), VoyageRecord.date <= str(p.end)).order_by(VoyageRecord.date)))
    return vessel, records

def register_document(db, user, payload, search):
    require_role(user, 'admin', 'captain')
    if user.role != 'admin':
        if not payload.vessels: raise HTTPException(403, '담당 선박을 지정하세요. 전체 공개 등록은 관리자만 가능합니다.')
        for vid in payload.vessels: vessel_access(user, vid)
    for vid in payload.vessels:
        if not db.get(Vessel, vid): raise HTTPException(422, '존재하지 않는 적용 선박입니다.')
    if payload.source_url and not re.match(r'^https://[^\s]+$', payload.source_url): raise HTTPException(422, '출처 URL은 HTTPS여야 합니다.')
    if sum(len(s.text) for s in payload.sections) > 1_000_000: raise HTTPException(422, '문서는 100만 자 이하여야 합니다.')
    old = list(db.scalars(select(Document).where(Document.logical_id == payload.logical_id).with_for_update()))
    if any(not document_access(user, d) or (user.role!='admin' and (not d.metadata_json.get('vessels') or not set(d.metadata_json['vessels']).issubset(user.vessels))) for d in old):
        raise HTTPException(403, '해당 문서를 개정할 권한이 없습니다.')
    if any(d.version == payload.version for d in old): raise HTTPException(409, '이미 등록된 버전입니다. 새로운 버전명을 사용하세요.')
    for d in old: d.active = False; d.status = 'superseded'
    metadata = payload.model_dump(mode='json', exclude={'logical_id','title','version','sections'})
    doc = Document(id=uid(), logical_id=payload.logical_id, title=payload.title, version=payload.version, metadata_json=metadata)
    db.flush()  # Retire the previous revision before inserting the new current row.
    db.add(doc); db.flush()
    chunks = [Chunk(document_id=doc.id, **c) for c in chunk_sections([s.model_dump() for s in payload.sections])]
    if not chunks: raise HTTPException(422, '색인할 본문이 없습니다.')
    db.add_all(chunks); audit(db, user, 'document.update' if old else 'document.create', doc.id, {'version':doc.version})
    db.commit()
    state = search.index(chunks)
    return {'document': rowdict(doc), 'chunks':len(chunks), 'vector_status':state}

def create_app(db_url=None, search=None, gateway=None):
    app = FastAPI(title='해,답 API', version='2.0.0')
    engine, sessions = database(db_url)
    app.state.engine, app.state.sessions = engine, sessions
    search, gateway = search or Search(), gateway or Gateway()
    origins = set(os.getenv('WEB_ORIGINS', 'http://127.0.0.1:3000,http://localhost:3000,http://127.0.0.1:8000').split(','))
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost','127.0.0.1','testserver'])
    failures = defaultdict(deque)

    @app.middleware('http')
    async def boundary(request, call_next):
        origin = request.headers.get('origin')
        if origin and origin not in origins: return JSONResponse({'detail':'허용되지 않은 출처입니다.'}, status_code=403)
        if request.headers.get('sec-fetch-site') in {'cross-site','same-site'}: return JSONResponse({'detail':'동일 출처 요청만 허용합니다.'}, status_code=403)
        try: length=int(request.headers.get('content-length','0'))
        except ValueError: return JSONResponse({'detail':'잘못된 요청 길이입니다.'},status_code=400)
        if request.method not in {'GET','HEAD','OPTIONS'} and length > 11*1024*1024:
            return JSONResponse({'detail':'요청이 너무 큽니다.'},status_code=413)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    def auth(db, request, *roles):
        user, session = session_user(db, request.cookies.get('haedap_session'))
        if request.method != 'GET' and not secrets.compare_digest(request.headers.get('x-csrf-token',''), session.csrf):
            raise HTTPException(403, '세션을 새로고침해 주세요.')
        if roles: require_role(user, *roles)
        return user, session

    @app.exception_handler(ValueError)
    async def bad_input(request, exc): return JSONResponse({'detail':str(exc)},status_code=422)

    @app.exception_handler(RequestValidationError)
    async def validation_failure(request, exc):
        with sessions() as db:
            audit(db,'system','validation.failed',request.url.path,{'fields':[str(e['loc']) for e in exc.errors()]}); db.commit()
        return JSONResponse({'detail':'입력 형식·단위·필수 항목을 확인해 주세요.'},status_code=422)

    @app.exception_handler(IntegrityError)
    async def conflict(request, exc): return JSONResponse({'detail':'중복된 데이터 또는 동시 변경입니다. 새로고침 후 확인하세요.'},status_code=409)

    @app.exception_handler(Exception)
    async def failure(request, exc):
        log.error('Request failed: %s %s', request.url.path, type(exc).__name__)
        try:
            with sessions() as db:
                audit(db, 'system', 'error', request.url.path, {'type':type(exc).__name__}); db.commit()
        except Exception: pass
        return JSONResponse({'detail':'처리를 완료하지 못했습니다. 관리자에게 문의하세요.'},status_code=500)

    @app.get('/api/role4/factors')
    def role4_factors(request: Request):
        with sessions() as db:
            auth(db, request, 'admin')
            return role4_calculations.catalog()

    @app.post('/api/role4/calculate')
    def role4_calculate(body: role4_calculations.CalculationRequest, request: Request):
        with sessions() as db:
            user, _ = auth(db, request, 'admin')
            result = role4_calculations.calculate(engine, body)
            audit(db, user, 'role4.calculate', body.scope.vessel_id,
                  {'request': body.model_dump(mode='json'), 'version': result['version'],
                   'status': result['status'], 'record_count': result['record_count']})
            db.commit()
            return result

    @app.post('/api/role4/ask')
    def role4_ask(body: role4_agent.Ask, request: Request):
        with sessions() as db:
            user, _ = auth(db, request, 'admin')
            result = role4_agent.run(engine, gateway, body)
            audit(db, user, 'role4.ask', body.scope.dataset,
                  {'question': body.question, 'scope': body.scope.model_dump(mode='json'),
                   'routing': result['routing'], 'actions': result['actions']})
            db.commit()
            return result

    @app.post('/api/role4/query')
    def role4_query(body: role4.Role4Query, request: Request):
        with sessions() as db:
            user, _ = auth(db, request, 'admin')
            result = role4.query(engine, body)
            audit(db, user, 'role4.query', body.dataset,
                  {'filters': body.model_dump(mode='json'), 'total': result['total'],
                   'returned': len(result['rows'])})
            db.commit()
            return result

    @app.get('/api/health')
    def health():
        with sessions() as db: db.execute(select(1))
        return {'ok':True,'storage':engine.dialect.name,'vectors':search.enabled,'llm':gateway.enabled,'local_model':gateway.local}

    @app.post('/api/login')
    def login(body:Login, request:Request, response:Response):
        key = request.client.host if request.client else 'local'
        queue = failures[key]; stamp=time.time()
        while queue and queue[0] < stamp-60: queue.popleft()
        if len(queue)>=10: raise HTTPException(429,'로그인 시도가 많습니다. 1분 후 다시 시도하세요.')
        with sessions() as db:
            user=db.scalar(select(User).where(User.username==body.username))
            if not user or not user.active or not password_valid(body.password,user.password_hash):
                queue.append(stamp); audit(db,'anonymous','login.failed'); db.commit()
                raise HTTPException(401,'계정 또는 비밀번호를 확인하세요.')
            queue.clear(); token=secrets.token_urlsafe(32); csrf=secrets.token_hex(32)
            db.execute(delete(LoginSession).where(LoginSession.expires<stamp))
            db.add(LoginSession(token_hash=hash_token(token),user_id=user.id,expires=stamp+8*3600,csrf=csrf))
            audit(db,user,'login'); db.commit()
            response.set_cookie('haedap_session',token,httponly=True,samesite='strict',secure=os.getenv('COOKIE_SECURE')=='1',max_age=8*3600,path='/')
            return {'user':public_user(user),'csrf':csrf}

    @app.get('/api/me')
    def me(request:Request):
        with sessions() as db:
            user,s=auth(db,request); return {'user':public_user(user),'csrf':s.csrf}

    @app.post('/api/logout')
    def logout(request:Request,response:Response):
        with sessions() as db:
            user,s=auth(db,request); db.delete(s); db.commit()
        response.delete_cookie('haedap_session',path='/'); return {'ok':True}

    @app.get('/api/users')
    def users(request:Request):
        with sessions() as db:
            auth(db,request,'admin'); return [public_user(u) for u in db.scalars(select(User))]

    @app.post('/api/users')
    def add_user(body:Account,request:Request):
        with sessions() as db:
            actor,_=auth(db,request,'admin')
            if any(not db.get(Vessel,v) for v in body.vessels): raise HTTPException(422,'존재하지 않는 선박입니다.')
            user=User(username=body.username,password_hash=password_hash(body.password),role=body.role,vessels=body.vessels)
            db.add(user); audit(db,actor,'user.create',body.username,{'role':body.role}); db.commit(); return public_user(user)

    @app.put('/api/users/{user_id}')
    def edit_user(user_id:str,body:AccountUpdate,request:Request):
        with sessions() as db:
            actor,_=auth(db,request,'admin'); target=db.get(User,user_id)
            if not target: raise HTTPException(404,'사용자를 찾을 수 없습니다.')
            if target.id==actor.id and (body.role!='admin' or not body.active): raise HTTPException(422,'현재 관리자 본인의 권한은 제거할 수 없습니다.')
            if any(not db.get(Vessel,v) for v in body.vessels): raise HTTPException(422,'존재하지 않는 선박입니다.')
            target.role=body.role; target.vessels=body.vessels; target.active=body.active
            db.execute(delete(LoginSession).where(LoginSession.user_id==target.id))
            audit(db,actor,'user.update',target.username,body.model_dump()); db.commit(); return public_user(target)

    @app.get('/api/vessels')
    def vessels(request:Request):
        with sessions() as db:
            user,_=auth(db,request)
            rows=db.scalars(select(Vessel).order_by(Vessel.id))
            return [rowdict(v) for v in rows if user.role=='admin' or v.id in user.vessels]

    @app.post('/api/vessels')
    def add_vessel(body:VesselInput,request:Request):
        with sessions() as db:
            user,_=auth(db,request,'admin'); vessel=Vessel(**body.model_dump()); db.add(vessel)
            audit(db,user,'vessel.create',body.id); db.commit(); return rowdict(vessel)

    @app.get('/api/records')
    def records(request:Request,vessel_id:str,start:date,end:date):
        with sessions() as db:
            user,_=auth(db,request); v,rows=period_records(db,user,Period(vessel_id=vessel_id,start=start,end=end))
            return {'vessel':rowdict(v),'records':[rowdict(r) for r in rows]}

    @app.post('/api/records')
    def add_record(body:NoonInput,request:Request):
        with sessions() as db:
            user,_=auth(db,request,'admin','captain'); vessel_access(user,body.vessel_id)
            if not db.get(Vessel,body.vessel_id): raise HTTPException(404,'선박을 찾을 수 없습니다.')
            data=body.model_dump(mode='json',exclude={'vessel_id','date'})
            row=VoyageRecord(vessel_id=body.vessel_id,date=str(body.date),data=data)
            db.add(row); db.flush(); audit(db,user,'record.create',row.id); db.commit(); return rowdict(row)

    @app.put('/api/records/{record_id}')
    def edit_record(record_id:str,body:NoonInput,request:Request,version:int):
        with sessions() as db:
            user,_=auth(db,request,'admin','captain'); row=db.get(VoyageRecord,record_id)
            if not row: raise HTTPException(404,'운항 기록을 찾을 수 없습니다.')
            vessel_access(user,row.vessel_id); vessel_access(user,body.vessel_id)
            before=rowdict(row)
            values={'vessel_id':body.vessel_id,'date':str(body.date),'data':body.model_dump(mode='json',exclude={'vessel_id','date'}),'version':version+1}
            changed=db.execute(update(VoyageRecord).where(VoyageRecord.id==record_id,VoyageRecord.version==version).values(**values)).rowcount
            if not changed: raise HTTPException(409,'다른 사용자가 변경했습니다. 다시 불러오세요.')
            audit(db,user,'record.update',record_id,{'before':before,'after':values}); db.commit(); db.refresh(row); return rowdict(row)

    @app.delete('/api/records/{record_id}')
    def remove_record(record_id:str,request:Request,version:int):
        with sessions() as db:
            user,_=auth(db,request,'admin','captain'); row=db.get(VoyageRecord,record_id)
            if not row: raise HTTPException(404,'운항 기록을 찾을 수 없습니다.')
            vessel_access(user,row.vessel_id); snapshot=rowdict(row)
            changed=db.execute(delete(VoyageRecord).where(VoyageRecord.id==record_id,VoyageRecord.version==version)).rowcount
            if not changed: raise HTTPException(409,'기록이 변경되었습니다.')
            audit(db,user,'record.delete',record_id,{'before':snapshot}); db.commit(); return {'ok':True}

    @app.post('/api/metrics')
    def calculate(body:Period,request:Request):
        with sessions() as db:
            user,_=auth(db,request); v,rows=period_records(db,user,body); result=metrics(v,rows)
            audit(db,user,'tool.voyage_metrics',v.id,{'input':body.model_dump(mode='json'),'output':result}); db.commit(); return result

    @app.get('/api/documents')
    def documents(request:Request):
        with sessions() as db:
            user,_=auth(db,request)
            return [rowdict(d) for d in db.scalars(select(Document).order_by(Document.created_at.desc())) if document_access(user,d)]

    @app.get('/api/documents/{doc_id}')
    def document(doc_id:str,request:Request):
        with sessions() as db:
            user,_=auth(db,request); d=db.get(Document,doc_id)
            if not d or not document_access(user,d): raise HTTPException(404,'문서를 찾을 수 없습니다.')
            return {**rowdict(d),'chunks':[rowdict(c) for c in db.scalars(select(Chunk).where(Chunk.document_id==d.id))]}

    @app.post('/api/documents')
    def add_document(body:DocInput,request:Request):
        with sessions() as db:
            user,_=auth(db,request); return register_document(db,user,body,search)

    @app.post('/api/documents/pdf')
    async def add_pdf(request:Request,metadata:str=Form(...),file:UploadFile=File(...)):
        with sessions() as db:
            user,_=auth(db,request,'admin','captain')
            data=await file.read(10*1024*1024+1)
            try:
                meta=DocMeta.model_validate_json(metadata)
                payload=DocInput(**meta.model_dump(),sections=pdf_sections(data))
            except ValidationError as exc: raise HTTPException(422,'문서 메타데이터를 확인하세요.') from exc
            return register_document(db,user,payload,search)

    @app.delete('/api/documents/{doc_id}')
    def remove_document(doc_id:str,request:Request):
        with sessions() as db:
            user,_=auth(db,request,'admin','captain'); d=db.get(Document,doc_id)
            if not d or not document_access(user,d): raise HTTPException(404,'문서를 찾을 수 없습니다.')
            if user.role!='admin' and (not d.metadata_json.get('vessels') or not set(d.metadata_json['vessels']).issubset(user.vessels)):
                raise HTTPException(403,'전체 또는 타 선박의 문서 폐기는 관리자만 가능합니다.')
            d.active=False; d.status='withdrawn'; audit(db,user,'document.withdraw',d.id); db.commit(); return {'ok':True}

    @app.post('/api/ask')
    def ask(body:Ask,request:Request):
        started=time.monotonic()
        with sessions() as db:
            user,_=auth(db,request)
            language=body.language if body.language!='auto' else ('ko' if re.search(r'[가-힣]',body.question) else 'en')
            planner=gateway if body.use_model else Gateway(env={})
            plan,routing,warnings=planner.plan(body.question)
            evidence=[]; method='none'; result=None; data=[]; draft=None
            # Scope is explicit UI context, never arbitrary model-generated SQL or IDs.
            if 'search_documents' in plan.actions:
                evidence,method,search_warnings=search.query(db,user,body.question); warnings+=search_warnings
            v=None
            if any(a in plan.actions for a in ['query_voyage','calculate_metrics','draft_report']):
                mentioned=set(re.findall(r'\bHAE-\d+\b',body.question.upper()))
                if mentioned and mentioned!={body.vessel_id.upper()}:
                    raise HTTPException(422,'질문의 선박과 선택한 선박이 다릅니다. 상단 선박 선택을 맞춰 주세요.')
                v,rows=period_records(db,user,body)
                data=[rowdict(r) for r in rows]
                if not rows: warnings.append('NO_VOYAGE_DATA')
                elif 'calculate_metrics' in plan.actions or 'draft_report' in plan.actions:
                    result=metrics(v,rows); audit(db,user,'tool.voyage_metrics',v.id,{'input':body.model_dump(mode='json'),'output':result})
            statements=[{'text':e['text'],'chunk_id':e['id'],'quote':e['text']} for e in evidence[:3]]
            generated=False; insufficient=not evidence and not data
            if body.use_model and gateway.enabled and evidence:
                try:
                    answer=gateway.answer(body.question,evidence,language)
                    statements=answer['statements']; generated=True
                    insufficient=answer['insufficient'] and not data
                except Exception: warnings.append('LLM_FAILED_EXTRACTIVE_FALLBACK')
            if body.use_model and not gateway.enabled: warnings.append('LLM_NOT_CONFIGURED')
            if 'draft_report' in plan.actions and result:
                draft={'kind':plan.report_kind,'text':report_text(plan.report_kind,v,result,evidence,language)}
            output={'language':language,'routing':routing,'actions':plan.actions,'retrieval':method,'evidence':evidence,
                    'statements':statements,'metrics':result,'records':data,'draft':draft,'warnings':warnings,
                    'insufficient':insufficient,'generation':'llm' if generated else 'extractive',
                    'notice': ('근거 원문을 표시합니다. 생성·번역된 답변이 아닙니다.' if language=='ko' else 'Source excerpts shown in their original language; not a generated or translated answer.') if not generated else 'AI 초안: 출처와 적용 조건을 확인하세요.',
                    'elapsed_ms':round((time.monotonic()-started)*1000)}
            audit(db,user,'query',body.vessel_id,{'question':body.question,'response':output}); db.commit(); return output

    @app.post('/api/reports')
    def create_report(body:Draft,request:Request):
        with sessions() as db:
            user,_=auth(db,request); v,rows=period_records(db,user,body); result=metrics(v,rows)
            evidence,_,_=search.query(db,user,body.question)
            report=Report(owner_id=user.id,vessel_id=v.id,title=f'{body.kind} · {v.name} · {body.start}',kind=body.kind,
                          text=report_text(body.kind,v,result,evidence),snapshot={'metrics':result,'evidence':evidence,'period':body.model_dump(mode='json')})
            db.add(report); db.flush(); audit(db,user,'report.create',report.id); db.commit(); return rowdict(report)

    def allowed_report(db,user,report_id):
        r=db.get(Report,report_id)
        if not r: raise HTTPException(404,'보고서를 찾을 수 없습니다.')
        vessel_access(user,r.vessel_id)
        if user.role=='crew' and r.owner_id!=user.id: raise HTTPException(403,'본인이 만든 초안만 열람할 수 있습니다.')
        # A report contains immutable evidence; recheck it against current document ACLs.
        for e in r.snapshot.get('evidence',[]):
            d=db.get(Document,e['document_id'])
            if not d or not document_access(user,d): raise HTTPException(403,'보고서 근거 문서에 접근할 권한이 없습니다.')
        return r

    @app.get('/api/reports')
    def reports(request:Request):
        with sessions() as db:
            user,_=auth(db,request); result=[]
            for r in db.scalars(select(Report).order_by(Report.created_at.desc())):
                try: allowed_report(db,user,r.id)
                except HTTPException: continue
                result.append({k:v for k,v in rowdict(r).items() if k not in {'text','snapshot'}})
            return result

    @app.get('/api/reports/{report_id}')
    def get_report(report_id:str,request:Request):
        with sessions() as db:
            user,_=auth(db,request); return rowdict(allowed_report(db,user,report_id))

    @app.put('/api/reports/{report_id}')
    def edit_report(report_id:str,body:EditReport,request:Request):
        with sessions() as db:
            user,_=auth(db,request); r=allowed_report(db,user,report_id)
            changed=db.execute(update(Report).where(Report.id==r.id,Report.version==body.version).values(title=body.title,text=body.text,version=body.version+1)).rowcount
            if not changed: raise HTTPException(409,'다른 창에서 수정되었습니다. 최신 초안을 다시 불러오세요.')
            audit(db,user,'report.update',r.id); db.commit(); db.refresh(r); return rowdict(r)

    @app.get('/api/reports/{report_id}/export')
    def export_report(report_id:str,request:Request,format:Literal['md','json']='md'):
        with sessions() as db:
            user,_=auth(db,request); r=allowed_report(db,user,report_id)
            content=r.text if format=='md' else json.dumps(rowdict(r),ensure_ascii=False,indent=2)
            audit(db,user,'report.export',r.id,{'format':format,'version':r.version}); db.commit()
            return Response(content,media_type='text/markdown' if format=='md' else 'application/json',
                            headers={'Content-Disposition':f'attachment; filename="{r.id}.{format}"'})

    @app.get('/api/history')
    def history(request:Request):
        with sessions() as db:
            user,_=auth(db,request)
            stmt=select(Audit).order_by(Audit.created_at.desc()).limit(100)
            if user.role!='admin': stmt=stmt.where(Audit.actor==user.username)
            # Non-admins see operation summaries, not historical documents whose permissions may have changed.
            return [rowdict(r) if user.role=='admin' else {k:v for k,v in rowdict(r).items() if k!='detail'} for r in db.scalars(stmt)]

    return app

def main():
    import uvicorn
    uvicorn.run(create_app(),host='127.0.0.1',port=int(os.getenv('API_PORT','8000')))

if __name__=='__main__': main()
