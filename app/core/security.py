import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from jose import JWTError, jwt
from pydantic import BaseModel

from app.core.config import settings

_hasher = PasswordHasher()


def hash_secret(raw: str) -> str:
    """Used for both passwords and PIN codes — same hashing needs, different inputs."""
    return _hasher.hash(raw)


def verify_secret(raw: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, raw)
    except VerifyMismatchError:
        return False


# Argon2 is deliberately expensive — ~44ms per call at these parameters — and
# argon2-cffi is synchronous CPU-bound C code. Calling it straight from an
# async endpoint pins the event loop for that whole time, so every other
# request in flight (a cashier ringing up a sale, say) simply stops. Async
# callers must use the wrappers below; the sync versions above stay for
# scripts and tests that have no loop to block.
async def hash_secret_async(raw: str) -> str:
    return await asyncio.to_thread(hash_secret, raw)


async def verify_secret_async(raw: str, hashed: str) -> bool:
    return await asyncio.to_thread(verify_secret, raw, hashed)


class TokenType(str, Enum):
    access = "access"
    refresh = "refresh"


class TokenPayload(BaseModel):
    sub: uuid.UUID
    # Exactly one of tid/bid is set — tid for an ordinary shop account, bid
    # for a branch_champion (see UserRole.branch_champion). Both optional
    # rather than a tagged union to keep decode_token/jose's dict round-trip
    # simple; get_tenant_context/get_branch_context each check theirs.
    tid: uuid.UUID | None = None
    bid: uuid.UUID | None = None
    role: str
    type: TokenType


def _create_token(
    user_id: uuid.UUID,
    tenant_id: uuid.UUID | None,
    branch_id: uuid.UUID | None,
    role: str,
    token_type: TokenType,
    expires_delta: timedelta,
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "tid": str(tenant_id) if tenant_id is not None else None,
        "bid": str(branch_id) if branch_id is not None else None,
        "role": role,
        "type": token_type.value,
        "iat": now,
        "exp": now + expires_delta,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(
    user_id: uuid.UUID, tenant_id: uuid.UUID | None, role: str, branch_id: uuid.UUID | None = None
) -> str:
    return _create_token(
        user_id, tenant_id, branch_id, role, TokenType.access,
        timedelta(minutes=settings.access_token_expire_minutes),
    )


def create_refresh_token(
    user_id: uuid.UUID, tenant_id: uuid.UUID | None, role: str, branch_id: uuid.UUID | None = None
) -> str:
    return _create_token(
        user_id, tenant_id, branch_id, role, TokenType.refresh,
        timedelta(days=settings.refresh_token_expire_days),
    )


def decode_token(token: str) -> TokenPayload:
    try:
        raw = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise ValueError("invalid or expired token") from exc
    return TokenPayload.model_validate(raw)
