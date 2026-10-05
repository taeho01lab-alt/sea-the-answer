"""PostgreSQL is the application store. SQLite is supported only by isolated tests."""
import os
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import create_engine, String, Text, JSON, Integer, Float, Boolean, ForeignKey, UniqueConstraint, event, Index, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

def uid(): return str(uuid4())
def now(): return datetime.now(timezone.utc).isoformat()

class Base(DeclarativeBase): pass

class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(20), default="crew")
    vessels: Mapped[list] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

class LoginSession(Base):
    __tablename__ = "login_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    expires: Mapped[float] = mapped_column(Float)
    csrf: Mapped[str] = mapped_column(String(64))

class Vessel(Base):
    __tablename__ = "vessels"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    dwt: Mapped[float] = mapped_column(Float)
    ship_type: Mapped[str] = mapped_column(String(80))
    sample: Mapped[bool] = mapped_column(Boolean, default=False)

class VoyageRecord(Base):
    __tablename__ = "voyage_records"
    __table_args__ = (UniqueConstraint("vessel_id", "date"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    vessel_id: Mapped[str] = mapped_column(ForeignKey("vessels.id"))
    date: Mapped[str] = mapped_column(String(10))
    data: Mapped[dict] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer, default=1)

class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        Index('uq_document_revision', 'logical_id', 'version', unique=True),
        Index('uq_document_current', 'logical_id', unique=True, postgresql_where=text('active = true'), sqlite_where=text('active = 1')),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    logical_id: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(200))
    version: Mapped[str] = mapped_column(String(80))
    metadata_json: Mapped[dict] = mapped_column(JSON)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(20), default="current")
    created_at: Mapped[str] = mapped_column(String(40), default=now)

class Chunk(Base):
    __tablename__ = "chunks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section: Mapped[str] = mapped_column(String(200))

class Report(Base):
    __tablename__ = "reports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    vessel_id: Mapped[str] = mapped_column(ForeignKey("vessels.id"))
    title: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(Text)
    snapshot: Mapped[dict] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[str] = mapped_column(String(40), default=now)

class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    actor: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(80))
    target: Mapped[str] = mapped_column(String(200))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(String(40), default=now)

def database(url=None):
    url = url or os.getenv("DATABASE_URL")
    if not url: raise RuntimeError("DATABASE_URL is required. Run scripts/setup-local.ps1 first.")
    if url.startswith('postgresql://'):
        url = url.replace('postgresql://', 'postgresql+psycopg://', 1)
    options = {"connect_args": {"check_same_thread": False}} if url.startswith('sqlite') else (
        {"connect_args": {"connect_timeout": 5}} if url.startswith('postgresql') else {})
    engine = create_engine(url, hide_parameters=True, pool_pre_ping=True, **options)
    if url.startswith('sqlite'):
        @event.listens_for(engine, 'connect')
        def enable_foreign_keys(connection, record): connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    # Additive, idempotent indexes also cover databases created before this release.
    for index in Document.__table__.indexes: index.create(engine, checkfirst=True)
    return engine, sessionmaker(engine, expire_on_commit=False)

def audit(db, user, action, target="", detail=None):
    db.add(Audit(actor=user.username if isinstance(user, User) else str(user), action=action, target=target, detail=detail or {}))

def rowdict(row):
    return {col.name: getattr(row, col.name) for col in row.__table__.columns}
