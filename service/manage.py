"""Local provisioning, reproducible sample import, vector preparation and backup."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
from datetime import date, timedelta
from dotenv import load_dotenv
from sqlalchemy import select, func, insert
from .storage import database, Base, User, Vessel, VoyageRecord, Document, Chunk, Report, Audit, now, rowdict
from .security import password_hash
from .retrieval import Search

ROOT=Path(__file__).resolve().parent.parent
TABLES=[User,Vessel,VoyageRecord,Document,Chunk,Report,Audit]

def command(args, **kwargs):
    return subprocess.run([str(a) for a in args],check=True,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,**kwargs)

def local_db(pg_bin=None):
    import psycopg
    from psycopg import sql
    pg_bin=Path(pg_bin or os.getenv('PG_BIN', r'C:\Program Files\PostgreSQL\18\bin'))
    suffix='.exe' if os.name=='nt' else ''
    def exe(name): return pg_bin/(name+suffix)
    config=ROOT/'.env.local'; data=ROOT/'data'; data.mkdir(exist_ok=True)
    pgdata=data/'postgres'; ownerfile=data/'pg-owner-password'
    if not (pgdata/'PG_VERSION').exists():
        if pgdata.exists() and any(pgdata.iterdir()): raise RuntimeError('Postgres directory is not empty; inspect it before retrying.')
        ownerfile.write_text(secrets.token_urlsafe(32),encoding='utf-8')
        command([exe('initdb'),'-D',pgdata,'-U','haedap_owner','--pwfile',ownerfile,'--auth=scram-sha-256','--encoding=UTF8','--locale=C'])
    status=subprocess.run([str(exe('pg_ctl')),'-D',str(pgdata),'status'],capture_output=True,
                          creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    if status.returncode:
        command([exe('pg_ctl'),'-D',pgdata,'-l',data/'postgres.log','-o','-h 127.0.0.1 -p 55432','-w','start'])
    if config.exists():
        print('Existing .env.local preserved. Dedicated PostgreSQL is running on 127.0.0.1:55432.'); return
    app_password=secrets.token_urlsafe(32)
    with psycopg.connect(host='127.0.0.1',port=55432,user='haedap_owner',password=ownerfile.read_text(),dbname='postgres',autocommit=True) as conn:
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname='haedap_app'").fetchone():
            conn.execute(sql.SQL('CREATE ROLE haedap_app LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE').format(sql.Literal(app_password)))
        else: raise RuntimeError('haedap_app already exists but config is missing. Restore the config instead of resetting credentials.')
        conn.execute('CREATE DATABASE haedap OWNER haedap_app')
    config.write_text(f'DATABASE_URL=postgresql+psycopg://haedap_app:{app_password}@127.0.0.1:55432/haedap\nCHROMA_PATH=data/chroma\nVECTOR_ENABLED=0\nLLM_ENABLED=0\nLLM_BASE_URL=http://127.0.0.1:11434/v1\nLLM_MODEL=\nALLOW_EXTERNAL_LLM=0\n',encoding='utf-8')
    print('Project PostgreSQL and .env.local created. Existing PostgreSQL service was not changed.')

def bootstrap():
    from .app import DocInput, register_document
    engine,sessions=database(); search=Search(vector_enabled=False)
    with sessions() as db:
        admin=db.scalar(select(User).where(User.role=='admin'))
        if not admin:
            password=secrets.token_urlsafe(18)
            admin=User(username='captain',password_hash=password_hash(password),role='admin',vessels=[])
            db.add(admin); db.commit()
            path=ROOT/'data'/'initial-login.txt'
            path.write_text('해,답 로컬 개발 계정\nUsername: captain\nPassword: '+password+'\n',encoding='utf-8')
            print('Initial credentials saved in data/initial-login.txt (Git ignored).')
        for vid,name,dwt in [('HAE-01','해답 1호',50000),('HAE-02','해답 2호',65000)]:
            if not db.get(Vessel,vid):
                db.add(Vessel(id=vid,name=name,dwt=dwt,ship_type='bulk carrier',sample=True)); db.flush()
                for i in range(7):
                    db.add(VoyageRecord(vessel_id=vid,date=str(date(2026,9,11)+timedelta(days=i)),data={
                        'voyage':'DEMO-026','status':'Under way (fictional)','latitude':35.1-i*1.3,'longitude':129-i*1.4,
                        'speed_kn':13.+i*.1,'distance_nm':312.+i*3,'fuel_t':24.+i*.4+(3 if vid=='HAE-02' else 0),
                        'factor':3.114,'engine_hours':24.,'draft_m':10.,'weather':'Fair — fictional sample'}))
        db.commit()
        seeds=json.loads((ROOT/'knowledge'/'seed.json').read_text(encoding='utf-8'))
        for d in seeds:
            if not db.scalar(select(Document).where(Document.logical_id==d['id'])):
                payload=DocInput(logical_id=d['id'],title=d['title'],version=d['version'],kind=d['kind'],issuer='기존 MVP의 공개 요약 / 가상 예시',
                    revised_at=d['reviewedAt'],source_url=d.get('url') or '',
                    sections=[{'section':s['heading'],'text':s['text'],'page':None} for s in d['sections']])
                register_document(db,admin,payload,search)
    engine.dispose(); print('Sample vessels, 14 records and document excerpts ready. Existing data preserved.')

def prepare_vectors():
    from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2
    # Explicit provisioning command: downloads the public model once if absent.
    ONNXMiniLM_L6_V2()(['model readiness check'])
    engine,sessions=database(); search=Search(vector_enabled=True)
    with sessions() as db:
        result=search.index(list(db.scalars(select(Chunk))))
        if result!='indexed': raise RuntimeError(result)
    engine.dispose(); print('Chroma index ready. Set VECTOR_ENABLED=1 in .env.local. English MiniLM + bilingual glossary; multilingual quality needs evaluation.')

def backup(path):
    engine,sessions=database()
    with engine.connect().execution_options(isolation_level='REPEATABLE READ' if engine.dialect.name=='postgresql' else 'SERIALIZABLE') as conn:
        from sqlalchemy.orm import Session
        with Session(conn) as db:
            payload={'format':'haedap-backup-1','created_at':now(),'tables':{m.__tablename__:[rowdict(r) for r in db.scalars(select(m))] for m in TABLES},
                     'settings':{'VECTOR_ENABLED':os.getenv('VECTOR_ENABLED','0'),'LLM_BASE_URL':os.getenv('LLM_BASE_URL',''),'LLM_MODEL':os.getenv('LLM_MODEL','')},
                     'note':'Sessions and API/database secrets excluded. Rebuild Chroma after restore.'}
    raw=json.dumps(payload,ensure_ascii=False,sort_keys=True).encode()
    target=Path(path); target.parent.mkdir(parents=True,exist_ok=True)
    with target.open('x',encoding='utf-8') as f: json.dump({'sha256':hashlib.sha256(raw).hexdigest(),'payload':payload},f,ensure_ascii=False,indent=2)
    engine.dispose(); print('Backup created:',target)

def restore(path,url):
    source=json.loads(Path(path).read_text(encoding='utf-8')); payload=source['payload']
    raw=json.dumps(payload,ensure_ascii=False,sort_keys=True).encode()
    if hashlib.sha256(raw).hexdigest()!=source['sha256'] or payload['format']!='haedap-backup-1': raise ValueError('Backup checksum or format mismatch')
    engine,sessions=database(url)
    with sessions() as db:
        if any(db.scalar(select(func.count()).select_from(m)) for m in TABLES): raise ValueError('Restore target must be an empty database. Existing data will not be overwritten.')
        for model in TABLES:
            rows=payload['tables'][model.__tablename__]
            if rows: db.execute(insert(model),rows)
        db.commit()
    engine.dispose(); print('Restore complete. Configure secrets separately and rebuild vectors. No login sessions were restored.')

def main():
    os.chdir(ROOT); load_dotenv('.env.local')
    parser=argparse.ArgumentParser(); parser.add_argument('action',choices=['local-db','bootstrap','prepare-vectors','backup','restore'])
    parser.add_argument('--pg-bin'); parser.add_argument('--path'); parser.add_argument('--database-url')
    args=parser.parse_args()
    if args.action=='local-db': local_db(args.pg_bin)
    elif args.action=='bootstrap': bootstrap()
    elif args.action=='prepare-vectors': prepare_vectors()
    elif args.action=='backup': backup(args.path or 'data/backups/'+now().replace(':','-')+'.json')
    elif args.action=='restore':
        if not args.path or not args.database_url: parser.error('restore requires --path and --database-url for an empty target')
        restore(args.path,args.database_url)

if __name__=='__main__': main()
