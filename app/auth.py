"""Accounts and the guard that protects every /opportunities and /reports route.

Passwords: bcrypt (slow on purpose, salted). Tokens: HS256 JWTs signed with
JWT_SECRET, carrying only the user id and an expiry. No crypto is hand-written.
"""

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app import config, db


class AuthFailure(Exception):
    def __init__(self, message: str):
        self.message = message


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def check_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


# A real hash of a random string, so a login for an unknown email takes as long as
# a wrong password and response timing can't reveal which emails have accounts.
_DUMMY_HASH = hash_password("timing-equaliser-not-a-real-password")


def issue_token(user_id: int) -> tuple[str, int]:
    expires_in = config.JWT_TTL_HOURS * 3600
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user_id), "iat": now, "exp": now + timedelta(seconds=expires_in)}
    return jwt.encode(payload, config.JWT_SECRET, algorithm="HS256"), expires_in


def login(email: str, password: str) -> dict | None:
    with db.session() as conn:
        user = db.get_user_for_login(conn, email)
    if user is None:
        check_password(password, _DUMMY_HASH)
        return None
    return user if check_password(password, user["password_hash"]) else None


bearer_scheme = HTTPBearer(auto_error=False, description="access_token from POST /auth/login")


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)) -> dict:
    if credentials is None or not credentials.credentials.strip():
        raise AuthFailure("Access token required")
    try:
        payload = jwt.decode(
            credentials.credentials.strip(), config.JWT_SECRET, algorithms=["HS256"],
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError:  # bad signature, expired, malformed, wrong algorithm
        raise AuthFailure("Invalid or expired token") from None
    with db.session() as conn:
        user = db.get_user(conn, int(payload["sub"]))
    if user is None:  # account deleted after the token was issued
        raise AuthFailure("Invalid or expired token")
    return user
