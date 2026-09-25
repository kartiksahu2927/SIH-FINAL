import base64
import hashlib
import hmac
import os
import time
from collections import defaultdict, deque
from typing import Callable

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .db import get_db
from .models import User


JWT_SECRET = os.getenv("JWT_SECRET", "change-me-in-production")
JWT_ALGORITHM = "HS256"
TOKEN_MINUTES = int(os.getenv("ACCESS_TOKEN_MINUTES", "60"))
bearer = HTTPBearer(auto_error=False)
_attempts: dict[str, deque[float]] = defaultdict(deque)


def hash_password(password: str) -> str:
    if len(password) < 6:
        raise ValueError("Password must be at least 6 characters.")
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_b64, digest_b64 = stored.split("$", 2)
        digest = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), n=2**14, r=8, p=1)
        return hmac.compare_digest(digest, base64.b64decode(digest_b64))
    except (ValueError, TypeError):
        return False


def create_access_token(user: User) -> str:
    now = int(time.time())
    payload = {"sub": str(user.id), "role": user.role, "iat": now, "exp": now + TOKEN_MINUTES * 60}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def rate_limit(request: Request, limit: int | None = None, window_seconds: int = 60) -> None:
    max_limit = limit if limit is not None else int(os.getenv("RATE_LIMIT_MAX", "120"))
    key = request.client.host if request.client else "unknown"
    now = time.time()
    bucket = _attempts[key]
    while bucket and bucket[0] <= now - window_seconds:
        bucket.popleft()
    if len(bucket) >= max_limit:
        raise HTTPException(status_code=429, detail="Too many attempts. Please try again shortly.")
    bucket.append(now)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)
) -> User:
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user = db.get(User, int(payload["sub"]))
    except (jwt.PyJWTError, KeyError, ValueError):
        user = None
    if not user or not user.active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired access token")
    return user


def require_roles(*roles: str) -> Callable:
    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not have permission for this action")
        return user
    return dependency
