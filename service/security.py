import hashlib
import hmac
import secrets
import time
from fastapi import HTTPException
from sqlalchemy import select
from .storage import LoginSession, User

def password_hash(password):
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise ValueError("비밀번호는 12~256자여야 합니다.")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return salt.hex() + ":" + digest.hex()

def password_valid(password, stored):
    try:
        salt, expected = stored.split(":")
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, AttributeError): return False

def hash_token(token): return hashlib.sha256(token.encode()).hexdigest()

def session_user(db, token):
    session = db.get(LoginSession, hash_token(token or ""))
    user = db.get(User, session.user_id) if session and session.expires > time.time() else None
    if not user or not user.active: raise HTTPException(401, "로그인이 필요합니다.")
    return user, session

def require_role(user, *roles):
    if user.role not in roles: raise HTTPException(403, "이 작업에 필요한 권한이 없습니다.")

def vessel_access(user, vessel_id):
    if user.role != "admin" and vessel_id not in user.vessels:
        raise HTTPException(403, "접근할 수 없는 선박입니다.")

def document_access(user, document):
    meta = document.metadata_json
    if user.role == "admin": return True
    if meta.get("restricted", False) and user.role != "captain": return False
    vessels = meta.get("vessels", [])
    return not vessels or bool(set(vessels) & set(user.vessels))

def public_user(user):
    return {"id": user.id, "username": user.username, "role": user.role, "vessels": user.vessels, "active":user.active}
